#!/usr/bin/env bash
# Generate a signed incremental OTA from the live release to a new signed build.
# sign-lineage-build.sh calls it during a campaign build. The build campaign may
# rerun it standalone, after a one-time authorize-recovery, without a rebuild.
set -Eeo pipefail

umask 077

readonly script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
readonly build_root=${YRRP_BUILD_ROOT:-/opt/android}
readonly cert_dir=${YRRP_CERT_DIR:-/opt/yrrp/signing}
readonly signed_dir=${build_root}/out/signed
readonly ota_container=${OTA_CONTAINER_NAME:-yrrp-ota-server}
readonly incremental_tool=${script_dir}/incremental-ota.py
readonly password_file=${YRRP_INCREMENTAL_PASSWORD_FILE:-/home/android/.android-signing-passwords-incremental}
readonly build_id_pattern='^[0-9]{8}-[0-9]{6}$'

source "${script_dir}/yrrp-release-lib.sh"

source_build=
target_build=
work_dir=
owns_password_file=false

usage() {
    echo "usage: $0 --source-build YYYYMMDD-HHMMSS --target-build YYYYMMDD-HHMMSS" >&2
    exit 64
}

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

cleanup() {
    if [[ -n ${work_dir} && -d ${work_dir} ]]; then
        rm -rf "${work_dir}"
    fi
    if ${owns_password_file}; then
        rm -f "${password_file}"
    fi
}
trap cleanup EXIT

parse_arguments() {
    while (($#)); do
        case $1 in
            --source-build) source_build=${2:-}; shift 2 || usage ;;
            --target-build) target_build=${2:-}; shift 2 || usage ;;
            *) usage ;;
        esac
    done
    [[ ${source_build} =~ ${build_id_pattern} && ${target_build} =~ ${build_id_pattern} ]] || usage
    [[ ${source_build} != "${target_build}" ]] || fail "source and target build must differ"
}

fetch_live_release() {
    local live_build
    live_build=$(docker inspect --format '{{ index .Config.Labels "io.yrrp.ota.build-id" }}' "${ota_container}") \
        || fail "live OTA container ${ota_container} is unavailable"
    [[ ${live_build} == "${source_build}" ]] \
        || fail "source build ${source_build} is not the live release (${live_build})"
    docker exec "${ota_container}" cat "/srv/ota/install/salami/${source_build}/release.json" \
        > "${work_dir}/source-release.json"
}

verify_certificate() {
    local otacert_sha release_sha
    unzip -p "${incremental_ota}" META-INF/com/android/otacert > "${work_dir}/otacert.x509.pem"
    otacert_sha=$(yrrp_cert_sha256 "${work_dir}/otacert.x509.pem")
    release_sha=$(yrrp_cert_sha256 "${cert_dir}/releasekey.x509.pem")
    [[ -n ${release_sha} && ${otacert_sha} == "${release_sha}" ]] \
        || fail "incremental OTA certificate does not match release key"
}

parse_arguments "$@"
yrrp_require_build_lock_free

readonly source_target_files="${signed_dir}/lineage-23.2-salami-${source_build}-signed-target_files.zip"
readonly target_target_files="${signed_dir}/lineage-23.2-salami-${target_build}-signed-target_files.zip"
readonly incremental_ota="${signed_dir}/lineage-23.2-salami-${source_build}-to-${target_build}-signed-incremental-ota.zip"
readonly generation_log="${incremental_ota%.zip}.log"
for file in "${source_target_files}" "${target_target_files}" \
    "${cert_dir}/releasekey.pk8" "${cert_dir}/releasekey.x509.pem"; do
    [[ -s ${file} ]] || fail "Required file missing or empty: ${file}"
done

work_dir=$(mktemp -d)
fetch_live_release
source_fields=$(python3 "${incremental_tool}" check-source \
    --target-files "${source_target_files}" \
    --release-json "${work_dir}/source-release.json")
read -r source_incremental source_sha256 <<<"${source_fields}"

if [[ -z ${ANDROID_PW_FILE:-} || ! -s ${ANDROID_PW_FILE} ]]; then
    owns_password_file=true
    yrrp_load_signing_passwords "${cert_dir}" "${password_file}"
fi

export PATH="${build_root}/out/host/linux-x86/bin:${build_root}/prebuilts/jdk/jdk21/linux-x86/bin:${PATH}"
rm -f "${incremental_ota}" "${incremental_ota}.json" "${generation_log}"
ota_from_target_files \
    -k "${cert_dir}/releasekey" \
    --block \
    -i "${source_target_files}" \
    --enable_zucchini=true \
    --enable_lz4diff=true \
    "${target_target_files}" \
    "${incremental_ota}" 2>&1 | tee "${generation_log}"

if grep -E 'Disabling (zucchini|lz4diff)' "${generation_log}" >&2; then
    fail "incremental OTA lost a delta optimization; see ${generation_log}"
fi

unzip -tq "${incremental_ota}" >/dev/null
verify_certificate
python3 "${incremental_tool}" verify-output \
    --incremental "${incremental_ota}" \
    --target-files "${target_target_files}" \
    --source-build "${source_build}" \
    --target-build "${target_build}" \
    --source-incremental "${source_incremental}" \
    --source-sha256 "${source_sha256}"

printf 'Signed incremental OTA: %s\n' "${incremental_ota}"
