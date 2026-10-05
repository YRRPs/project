#!/usr/bin/env bash
set -Eeo pipefail

umask 077

readonly build_root=/opt/android
readonly cert_dir=/home/android/.android-certs
readonly password_file=${cert_dir}/passwords
readonly status_file=/home/android/signed-build.status
readonly output_dir=${build_root}/out/signed

readonly -a release_apks=(
    com.android.appsearch.apk.apk
    AdServicesApk.apk
    FederatedCompute.apk
    HalfSheetUX.apk
    HealthConnectBackupRestore.apk
    HealthConnectController.apk
    OsuLogin.apk
    SafetyCenterResources.apk
    ServiceConnectivityResources.apk
    ServiceUwbResources.apk
    ServiceWifiResources.apk
    TelecomServiceResources.apk
    TelecomUi.apk
    WebAppService.apk
    WifiDialog.apk
)

record_exit() {
    local exit_code=$?
    if [[ -n "${verify_dir:-}" && -d "${verify_dir}" ]]; then
        rm -rf "${verify_dir}"
    fi
    if ((exit_code != 0)); then
        printf 'signing-failed:%s\n' "${exit_code}" > "${status_file}"
    fi
}
trap record_exit EXIT

require_file() {
    local file=$1
    if [[ ! -s "${file}" ]]; then
        printf 'Required file missing or empty: %s\n' "${file}" >&2
        exit 1
    fi
}

cd "${build_root}"
source build/envsetup.sh
breakfast salami

export ANDROID_PW_FILE="${password_file}"
require_file "${ANDROID_PW_FILE}"
require_file "${cert_dir}/releasekey.pk8"
require_file "${cert_dir}/releasekey.x509.pem"

mapfile -t target_files < <(
    find "${OUT}/obj/PACKAGING/target_files_intermediates" \
        -type f -name '*-target_files*.zip' -print \
        | sort
)
if ((${#target_files[@]} != 1)); then
    printf 'Expected exactly one target-files archive, found %s\n' "${#target_files[@]}" >&2
    printf '%s\n' "${target_files[@]}" >&2
    exit 1
fi

mapfile -t apex_payload_keys < <(
    find "${cert_dir}" -maxdepth 1 -type f \
        -name '*.pem' ! -name '*.x509.pem' -printf '%f\n' \
        | sed 's/\.pem$//' \
        | sort
)
if ((${#apex_payload_keys[@]} != 75)); then
    printf 'Expected 75 APEX payload keys, found %s\n' "${#apex_payload_keys[@]}" >&2
    exit 1
fi

mkdir -p "${output_dir}"
readonly build_date=$(date +%Y%m%d-%H%M%S)
readonly signed_target_files="${output_dir}/lineage-23.2-salami-${build_date}-signed-target_files.zip"
readonly signed_ota="${output_dir}/lineage-23.2-salami-${build_date}-signed-ota.zip"

sign_args=(-o -d "${cert_dir}")
for apk in "${release_apks[@]}"; do
    sign_args+=(--extra_apks "${apk}=${cert_dir}/releasekey")
done
for apex in "${apex_payload_keys[@]}"; do
    require_file "${cert_dir}/${apex}.pk8"
    require_file "${cert_dir}/${apex}.x509.pem"
    require_file "${cert_dir}/${apex}.pem"
    sign_args+=(--extra_apks "${apex}.apex=${cert_dir}/${apex}")
    sign_args+=(--extra_apex_payload_key "${apex}.apex=${cert_dir}/${apex}.pem")
done

printf 'signing-target-files\n' > "${status_file}"
sign_target_files_apks \
    "${sign_args[@]}" \
    "${target_files[0]}" \
    "${signed_target_files}"

printf 'generating-signed-ota\n' > "${status_file}"
ota_from_target_files \
    -k "${cert_dir}/releasekey" \
    --block \
    --backup=true \
    "${signed_target_files}" \
    "${signed_ota}"

printf 'verifying-signed-artifacts\n' > "${status_file}"
unzip -tq "${signed_target_files}" >/dev/null
unzip -tq "${signed_ota}" >/dev/null

verify_dir=$(mktemp -d)
unzip -p "${signed_ota}" META-INF/com/android/otacert \
    > "${verify_dir}/otacert.x509.pem"
release_fingerprint=$(
    openssl x509 -in "${cert_dir}/releasekey.x509.pem" \
        -outform DER \
        | sha256sum \
        | awk '{print $1}'
)
ota_fingerprint=$(
    openssl x509 -in "${verify_dir}/otacert.x509.pem" \
        -outform DER \
        | sha256sum \
        | awk '{print $1}'
)
if [[ "${release_fingerprint}" != "${ota_fingerprint}" ]]; then
    printf 'OTA certificate does not match release key\n' >&2
    exit 1
fi

systemui_path=$(unzip -Z1 "${signed_target_files}" | grep '/SystemUI.apk$' | head -n 1)
if [[ -z "${systemui_path}" ]]; then
    printf 'SystemUI APK not found in signed target files\n' >&2
    exit 1
fi
unzip -p "${signed_target_files}" "${systemui_path}" \
    > "${verify_dir}/SystemUI.apk"
platform_fingerprint=$(
    openssl x509 -in "${cert_dir}/platform.x509.pem" \
        -outform DER \
        | sha256sum \
        | awk '{print $1}'
)
export PATH="${build_root}/prebuilts/jdk/jdk21/linux-x86/bin:${PATH}"
systemui_fingerprint=$(
    "${build_root}/out/host/linux-x86/bin/apksigner" \
        verify --print-certs "${verify_dir}/SystemUI.apk" \
        | awk -F: '/Signer #1 certificate SHA-256 digest:/{gsub(/ /,"",$2); print $2; exit}'
)
if [[ "${platform_fingerprint}" != "${systemui_fingerprint}" ]]; then
    printf 'SystemUI certificate does not match platform key\n' >&2
    exit 1
fi

rm -rf "${verify_dir}"
verify_dir=

readonly target_files_sha256=$(sha256sum "${signed_target_files}" | awk '{print $1}')
readonly ota_sha256=$(sha256sum "${signed_ota}" | awk '{print $1}')
readonly summary="${output_dir}/lineage-23.2-salami-${build_date}-SHA256SUMS.txt"

{
    printf '%s  %s\n' "${target_files_sha256}" "$(basename "${signed_target_files}")"
    printf '%s  %s\n' "${ota_sha256}" "$(basename "${signed_ota}")"
} > "${summary}"

printf 'complete\n' > "${status_file}"
printf 'Signed target files: %s\n' "${signed_target_files}"
printf 'Signed OTA: %s\n' "${signed_ota}"
printf 'Checksums: %s\n' "${summary}"
