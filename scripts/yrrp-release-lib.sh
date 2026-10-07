# shellcheck shell=bash
# Shared helpers for the YRRP release scripts. Source it; it only defines functions.

# Load the archived signing password map, rewritten to the live key directory.
# The caller must remove the runtime file from its EXIT trap.
yrrp_load_signing_passwords() {
    local key_dir=$1
    local runtime_file=$2
    local stored_file=${key_dir}/passwords
    if [[ ! -s "${stored_file}" ]]; then
        printf 'Required file missing or empty: %s\n' "${stored_file}" >&2
        return 1
    fi
    sed "s|/home/android/.android-certs|${key_dir}|g" "${stored_file}" > "${runtime_file}"
    chmod 0600 "${runtime_file}"
    export ANDROID_PW_FILE="${runtime_file}"
}

# Print the SHA-256 of a PEM certificate's DER encoding.
yrrp_cert_sha256() {
    openssl x509 -in "$1" -outform DER | sha256sum | awk '{print $1}'
}

# Refuse to run while a campaign build holds the launch lock. sign-lineage-build.sh
# already runs under that lock and sets YRRP_BUILD_LOCK_HELD=1 for its children.
yrrp_require_build_lock_free() {
    local build_lock_file=${YRRP_BUILD_LOCK_FILE:-/home/android/.yrrp-build-launch.lock}
    if [[ ${YRRP_BUILD_LOCK_HELD:-} == 1 ]]; then
        return 0
    fi
    exec 8>>"${build_lock_file}"
    if ! flock -n 8; then
        printf 'A campaign build holds %s\n' "${build_lock_file}" >&2
        return 1
    fi
}
