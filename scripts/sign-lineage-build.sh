#!/usr/bin/env bash
set -Eeo pipefail

umask 077

readonly script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "${script_dir}/yrrp-release-lib.sh"
readonly build_root=${YRRP_BUILD_ROOT:-/opt/android}
readonly cert_dir=${YRRP_CERT_DIR:-/opt/yrrp/signing}
readonly password_file=${YRRP_RUNTIME_PASSWORD_FILE:-/home/android/.android-signing-passwords}
readonly status_file=${YRRP_STATUS_FILE:-/home/android/signed-build.status}
readonly output_dir=${build_root}/out/signed
readonly deploy_script=${YRRP_DEPLOY_SCRIPT:-${script_dir}/deploy-ota-release.sh}
readonly incremental_script=${YRRP_INCREMENTAL_SCRIPT:-${script_dir}/generate-incremental-ota.sh}
readonly ota_container=${OTA_CONTAINER_NAME:-yrrp-ota-server}
failure_domain=signing

: "${OTA_PUBLIC_BASE_URL:=https://ota.yimura.dev}"
: "${OTA_BASE_IMAGE_REF:=ghcr.io/yrrps/ota-server:main}"
: "${OTA_NETWORK:=proxy-net}"
export OTA_PUBLIC_BASE_URL OTA_BASE_IMAGE_REF OTA_NETWORK

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
    rm -f "${password_file}"
    if ((exit_code != 0)); then
        printf '%s-failed:%s\n' "${failure_domain}" "${exit_code}" > "${status_file}"
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

# Print the live release build ID when it can be an incremental source.
# Print a skip reason to stderr and nothing to stdout when it cannot.
resolve_incremental_source() {
    local device build_id
    if ! docker container inspect "${ota_container}" >/dev/null 2>&1; then
        printf 'incremental-skipped: no live OTA container\n' >&2
        return 0
    fi
    # Command substitution does not inherit errexit; a failed inspect must not look like a skip.
    device=$(docker inspect --format '{{ index .Config.Labels "io.yrrp.ota.device" }}' "${ota_container}") || return 1
    if [[ ${device} != salami ]]; then
        printf 'incremental-skipped: live container serves device %s\n' "${device:-unknown}" >&2
        return 0
    fi
    build_id=$(docker inspect --format '{{ index .Config.Labels "io.yrrp.ota.build-id" }}' "${ota_container}") || return 1
    if [[ ! ${build_id} =~ ^[0-9]{8}-[0-9]{6}$ ]]; then
        printf 'Live OTA container has invalid build-id label: %s\n' "${build_id}" >&2
        return 1
    fi
    # Skip only when the source is absent; generate-incremental-ota.sh rejects an empty or corrupt one.
    if [[ ! -e "${output_dir}/lineage-23.2-salami-${build_id}-signed-target_files.zip" ]]; then
        printf 'incremental-skipped: no signed target-files for live build %s\n' "${build_id}" >&2
        return 0
    fi
    printf '%s\n' "${build_id}"
}

readonly build_lock_fd=${YRRP_BUILD_LOCK_FD:-}
[[ ${build_lock_fd} =~ ^[0-9]+$ ]] || {
    printf 'Release launcher must provide numeric YRRP_BUILD_LOCK_FD\n' >&2
    exit 1
}
[[ -e /proc/$$/fd/${build_lock_fd} ]] || {
    printf 'YRRP_BUILD_LOCK_FD is not open: %s\n' "${build_lock_fd}" >&2
    exit 1
}
readonly build_lock_file=/home/android/.yrrp-build-launch.lock
build_lock_fd_identity=$(stat -Lc '%d:%i' -- "/proc/$$/fd/${build_lock_fd}") || {
    printf 'Cannot identify YRRP_BUILD_LOCK_FD: %s\n' "${build_lock_fd}" >&2
    exit 1
}
build_lock_file_identity=$(stat -Lc '%d:%i' -- "${build_lock_file}") || {
    printf 'Cannot identify canonical release lock: %s\n' "${build_lock_file}" >&2
    exit 1
}
[[ ${build_lock_fd_identity} == "${build_lock_file_identity}" ]] || {
    printf 'YRRP_BUILD_LOCK_FD is not the canonical release lock\n' >&2
    exit 1
}
flock -n "${build_lock_fd}" || {
    printf 'YRRP_BUILD_LOCK_FD does not hold the release lock\n' >&2
    exit 1
}

require_file "${deploy_script}"
[[ -x "${deploy_script}" ]] || {
    printf 'Deployment script is not executable: %s\n' "${deploy_script}" >&2
    exit 1
}
[[ ${OTA_PUBLIC_BASE_URL:-} == https://* ]] || {
    printf 'OTA_PUBLIC_BASE_URL must use HTTPS\n' >&2
    exit 1
}
[[ -n ${OTA_BASE_IMAGE_REF:-} ]] || {
    printf 'OTA_BASE_IMAGE_REF is required\n' >&2
    exit 1
}
command -v docker >/dev/null 2>&1 || {
    printf 'Docker CLI is required for automatic OTA deployment\n' >&2
    exit 1
}
if [[ ! -S /var/run/docker.sock ]]; then
    printf 'Docker socket is required for automatic OTA deployment\n' >&2
    exit 1
fi
docker version >/dev/null
docker buildx version >/dev/null
docker compose version >/dev/null
docker network inspect "${OTA_NETWORK:-proxy-net}" >/dev/null

cd "${build_root}"
source build/envsetup.sh
breakfast salami
printf 'building-target-files\n' > "${status_file}"
mka target-files-package otatools

yrrp_load_signing_passwords "${cert_dir}" "${password_file}"
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
readonly build_date=${YRRP_BUILD_DATE:-$(date +%Y%m%d-%H%M%S)}
readonly signed_target_files="${output_dir}/lineage-23.2-salami-${build_date}-signed-target_files.zip"
readonly signed_ota="${output_dir}/lineage-23.2-salami-${build_date}-signed-ota.zip"
readonly signing_profile_dir="${output_dir}/profile/${build_date}"

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
# The YRRPs build/make fork records where signing time goes; see docs/build/signing.md.
YRRP_SIGNING_PROFILE_DIR="${signing_profile_dir}" sign_target_files_apks \
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
release_fingerprint=$(yrrp_cert_sha256 "${cert_dir}/releasekey.x509.pem")
ota_fingerprint=$(yrrp_cert_sha256 "${verify_dir}/otacert.x509.pem")
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
platform_fingerprint=$(yrrp_cert_sha256 "${cert_dir}/platform.x509.pem")
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

printf 'generating-incremental-ota\n' > "${status_file}"
failure_domain=incremental
incremental_ota=
incremental_source=$(resolve_incremental_source)
if [[ -n ${incremental_source} ]]; then
    YRRP_BUILD_LOCK_HELD=1 "${incremental_script}" \
        --source-build "${incremental_source}" \
        --target-build "${build_date}"
    incremental_ota="${output_dir}/lineage-23.2-salami-${incremental_source}-to-${build_date}-signed-incremental-ota.zip"
    require_file "${incremental_ota}"
fi
failure_domain=signing

readonly target_files_sha256=$(sha256sum "${signed_target_files}" | awk '{print $1}')
readonly ota_sha256=$(sha256sum "${signed_ota}" | awk '{print $1}')
readonly summary="${output_dir}/lineage-23.2-salami-${build_date}-SHA256SUMS.txt"
incremental_sha256=
if [[ -n ${incremental_ota} ]]; then
    incremental_sha256=$(sha256sum "${incremental_ota}" | awk '{print $1}')
fi

{
    printf '%s  %s\n' "${target_files_sha256}" "$(basename "${signed_target_files}")"
    printf '%s  %s\n' "${ota_sha256}" "$(basename "${signed_ota}")"
    if [[ -n ${incremental_ota} ]]; then
        printf '%s  %s\n' "${incremental_sha256}" "$(basename "${incremental_ota}")"
    fi
} > "${summary}"

printf 'preparing-ota-release\n' > "${status_file}"
failure_domain=deployment
deploy_args=(--ota "${signed_ota}" --target-files "${signed_target_files}" --build-id "${build_date}")
if [[ -n ${incremental_ota} ]]; then
    deploy_args+=(--incremental "${incremental_ota}")
fi
OTA_STATUS_FILE="${status_file}" YRRP_BUILD_LOCK_HELD=1 "${deploy_script}" "${deploy_args[@]}"
failure_domain=signing

printf 'complete\n' > "${status_file}"
printf 'Signed target files: %s\n' "${signed_target_files}"
printf 'Signed OTA: %s\n' "${signed_ota}"
[[ -z ${incremental_ota} ]] || printf 'Signed incremental OTA: %s\n' "${incremental_ota}"
printf 'Checksums: %s\n' "${summary}"
printf 'Signing profile: %s\n' "${signing_profile_dir}"
