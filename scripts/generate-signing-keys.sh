#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

readonly build_root="/opt/android"
readonly cert_dir="/home/android/.android-certs"
readonly passphrase_file="/home/android/.android-signing-passphrase"
readonly export_dir="/opt/android/.signing-export"
readonly status_file="/home/android/signing-key-generation.status"
readonly standard_subject="/C=BE/ST=Vlaams Brabant/L=Leuven/O=Yim's Riced ROM Project/OU=Me, myself and AI/CN=Yim's Ricing Development Team"

readonly -a standard_keys=(
    bluetooth
    cyngn-app
    media
    networkstack
    nfc
    platform
    releasekey
    sdk_sandbox
    shared
    testcert
    verity
)

readonly -a apex_keys=(
    com.android.adbd
    com.android.adservices
    com.android.adservices.api
    com.android.appsearch
    com.android.art
    com.android.bluetooth
    com.android.bt
    com.android.btservices
    com.android.cellbroadcast
    com.android.compos
    com.android.configinfrastructure
    com.android.connectivity.resources
    com.android.conscrypt
    com.android.crashrecovery
    com.android.devicelock
    com.android.extservices
    com.android.graphics.pdf
    com.android.hardware.authsecret
    com.android.hardware.biometrics.face.virtual
    com.android.hardware.biometrics.fingerprint.virtual
    com.android.hardware.boot
    com.android.hardware.cas
    com.android.hardware.contexthub
    com.android.hardware.drm.clearkey
    com.android.hardware.dumpstate
    com.android.hardware.gatekeeper.nonsecure
    com.android.hardware.neuralnetworks
    com.android.hardware.power
    com.android.hardware.rebootescrow
    com.android.hardware.thermal
    com.android.hardware.threadnetwork
    com.android.hardware.uwb
    com.android.hardware.vibrator
    com.android.hardware.wifi
    com.android.healthfitness
    com.android.hotspot2.osulogin
    com.android.i18n
    com.android.ipsec
    com.android.media
    com.android.media.swcodec
    com.android.mediaprovider
    com.android.nearby.halfsheet
    com.android.networkstack.tethering
    com.android.neuralnetworks
    com.android.nfcservices
    com.android.npumanager
    com.android.ondevicepersonalization
    com.android.os.statsd
    com.android.permission
    com.android.profiling
    com.android.resolv
    com.android.rkpd
    com.android.runtime
    com.android.safetycenter.resources
    com.android.scheduling
    com.android.sdkext
    com.android.support.apexer
    com.android.telephony
    com.android.telephonycore
    com.android.telephonymodules
    com.android.tethering
    com.android.tzdata
    com.android.uprobestats
    com.android.uwb
    com.android.uwb.resources
    com.android.virt
    com.android.vndk.current
    com.android.vndk.current.on_vendor
    com.android.webapp
    com.android.wifi
    com.android.wifi.dialog
    com.android.wifi.resources
    com.google.pixel.camera.hal
    com.google.pixel.vibrator.hal
    com.qorvo.uwb
)

print_inventory() {
    printf 'standard_keys=%s\n' "${#standard_keys[@]}"
    printf 'apex_keys=%s\n' "${#apex_keys[@]}"
    printf 'subject=%s\n' "${standard_subject}"
}

record_exit() {
    local exit_code=$?
    if [[ -n "${restore_dir:-}" && -d "${restore_dir}" ]]; then
        rm -rf "${restore_dir}"
    fi
    if ((exit_code != 0)); then
        printf 'failed:%s\n' "${exit_code}" > "${status_file}"
    fi
}

require_file() {
    local file=$1
    if [[ ! -s "${file}" ]]; then
        printf 'Required file missing or empty: %s\n' "${file}" >&2
        exit 1
    fi
}

validate_key_pair() {
    local name=$1
    local private_key="${cert_dir}/${name}.pk8"
    local certificate="${cert_dir}/${name}.x509.pem"
    local certificate_public_key
    local private_public_key

    require_file "${private_key}"
    require_file "${certificate}"

    certificate_public_key=$(
        openssl x509 -in "${certificate}" -pubkey -noout \
            | openssl pkey -pubin -outform DER 2>/dev/null \
            | sha256sum \
            | awk '{print $1}'
    )
    private_public_key=$(
        openssl pkcs8 -in "${private_key}" -inform DER \
            -passin "file:${passphrase_file}" 2>/dev/null \
            | openssl pkey -pubout -outform DER 2>/dev/null \
            | sha256sum \
            | awk '{print $1}'
    )

    if [[ "${certificate_public_key}" != "${private_public_key}" ]]; then
        printf 'Certificate and private key do not match: %s\n' "${name}" >&2
        exit 1
    fi
}

generate_key() {
    local generator=$1
    local name=$2
    local subject=$3

    # AOSP make_key currently exits 1 from its cleanup trap after producing
    # valid files, so validate artifacts instead of trusting its exit status.
    printf '%s\n' "${password}" \
        | "${generator}" "${cert_dir}/${name}" "${subject}" >/dev/null 2>&1 \
        || true

    validate_key_pair "${name}"
}

if [[ "${1:-}" == "--list" ]]; then
    print_inventory
    exit 0
fi

trap record_exit EXIT

require_file "${passphrase_file}"
require_file "${build_root}/development/tools/make_key"

for command in openssl gpg tar gzip base64 sha256sum sed find sort xargs awk; do
    command -v "${command}" >/dev/null || {
        printf 'Required command unavailable: %s\n' "${command}" >&2
        exit 1
    }
done

if [[ -e "${cert_dir}" ]]; then
    printf 'Certificate directory already exists: %s\n' "${cert_dir}" >&2
    exit 1
fi

mkdir -p "${cert_dir}" "${export_dir}"
password=$(<"${passphrase_file}")

printf 'generating-standard-keys\n' > "${status_file}"
for key in "${standard_keys[@]}"; do
    generate_key \
        "${build_root}/development/tools/make_key" \
        "${key}" \
        "${standard_subject}"
done

ln -s releasekey.pk8 "${cert_dir}/testkey.pk8"
ln -s releasekey.x509.pem "${cert_dir}/testkey.x509.pem"

cp "${build_root}/development/tools/make_key" "${cert_dir}/make_key"
sed -i 's|2048|4096|g' "${cert_dir}/make_key"
chmod 0700 "${cert_dir}/make_key"

printf 'generating-apex-keys\n' > "${status_file}"
for apex in "${apex_keys[@]}"; do
    apex_subject="/C=BE/ST=Vlaams Brabant/L=Leuven/O=Yim's Riced ROM Project/OU=Me, myself and AI/CN=${apex}"
    generate_key "${cert_dir}/make_key" "${apex}" "${apex_subject}"

    if ! openssl pkcs8 \
        -in "${cert_dir}/${apex}.pk8" \
        -inform DER \
        -passin "file:${passphrase_file}" \
        -out "${cert_dir}/${apex}.pem" >/dev/null 2>&1; then
        printf 'APEX payload-key conversion failed: %s\n' "${apex}" >&2
        exit 1
    fi
    require_file "${cert_dir}/${apex}.pem"
done

password_file="${cert_dir}/passwords"
for key in "${standard_keys[@]}"; do
    printf '[[[ %s ]]] %s/%s\n' "${password}" "${cert_dir}" "${key}" >> "${password_file}"
done
for apex in "${apex_keys[@]}"; do
    printf '[[[ %s ]]] %s/%s\n' "${password}" "${cert_dir}" "${apex}" >> "${password_file}"
done
chmod 0600 "${password_file}"

(
    cd "${cert_dir}"
    find . -type f ! -name MANIFEST.sha256 -print0 \
        | sort -z \
        | xargs -0 sha256sum > MANIFEST.sha256
)

archive="${export_dir}/android-signing-keys-$(date +%Y%m%d).tar.gz.gpg"
base64_archive="${archive}.b64"
if [[ -e "${archive}" || -e "${base64_archive}" ]]; then
    printf 'Signing export already exists for today\n' >&2
    exit 1
fi

printf 'encrypting-export\n' > "${status_file}"
tar -C /home/android -czf - .android-certs \
    | gpg --batch --yes --pinentry-mode loopback \
        --passphrase-file "${passphrase_file}" \
        --symmetric --cipher-algo AES256 \
        --s2k-digest-algo SHA512 \
        --output "${archive}"

base64 --wrap=76 "${archive}" > "${base64_archive}"
chmod 0600 "${archive}" "${base64_archive}"

printf 'verifying-export\n' > "${status_file}"
restore_dir=$(mktemp -d)

gpg --batch --yes --pinentry-mode loopback \
    --passphrase-file "${passphrase_file}" \
    --decrypt "${archive}" 2>/dev/null \
    | tar -xzf - -C "${restore_dir}"

(
    cd "${restore_dir}/.android-certs"
    sha256sum --check --quiet MANIFEST.sha256
    [[ "$(readlink testkey.pk8)" == "releasekey.pk8" ]]
    [[ "$(readlink testkey.x509.pem)" == "releasekey.x509.pem" ]]
)

archive_sha256=$(sha256sum "${archive}" | awk '{print $1}')
decoded_sha256=$(base64 --decode "${base64_archive}" | sha256sum | awk '{print $1}')
if [[ "${archive_sha256}" != "${decoded_sha256}" ]]; then
    printf 'Base64 export verification failed\n' >&2
    exit 1
fi

rm -rf "${restore_dir}"
restore_dir=
unset password

summary="${export_dir}/README.txt"
{
    printf 'Encrypted signing-key archive: %s\n' "${archive}"
    printf 'Base64 export: %s\n' "${base64_archive}"
    printf 'Encrypted archive SHA-256: %s\n' "${archive_sha256}"
    printf 'Standard key count: %s\n' "${#standard_keys[@]}"
    printf 'APEX key count: %s\n' "${#apex_keys[@]}"
    printf 'Store passphrase separately. Never commit either export.\n'
} > "${summary}"
chmod 0600 "${summary}"

printf 'complete\n' > "${status_file}"
printf 'Signing key export complete. See %s\n' "${summary}"
