#!/usr/bin/env bash
set -Eeuo pipefail

readonly script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
readonly prepare_script=${script_dir}/prepare-ota-release.py
source "${script_dir}/yrrp-release-lib.sh"
: "${OTA_PUBLIC_BASE_URL:=https://ota.yimura.dev}"
: "${OTA_BASE_IMAGE_REF:=ghcr.io/yrrps/ota-server:main}"
readonly container_name=${OTA_CONTAINER_NAME:-yrrp-ota-server}
readonly hostname=${OTA_HOSTNAME:-ota-server}
readonly network=${OTA_NETWORK:-proxy-net}
readonly network_alias=${OTA_NETWORK_ALIAS:-ota-server}
readonly internal_port=${OTA_INTERNAL_PORT:-8080}
readonly health_timeout=${OTA_HEALTH_TIMEOUT:-90}
readonly health_interval=${OTA_HEALTH_INTERVAL:-2}
readonly work_dir=${OTA_WORK_DIR:-/opt/android/out/ota-release-contexts}
readonly lock_file=${OTA_LOCK_FILE:-/opt/android/out/.ota-deploy.lock}

ota=
target_files=
build_id=
incremental=
source_incremental=
context=
previous_exists=false
previous_stopped=false
previous_renamed=false
candidate_started=false
rollout_started=false
deployment_complete=false
candidate_image=
previous_image=

usage() {
    echo "usage: $0 --ota FILE --target-files FILE --build-id YYYYMMDD-HHMMSS [--incremental FILE]" >&2
    exit 64
}

fail() {
    echo "$*" >&2
    exit 1
}

report() {
    echo "$1"
    if [[ -n ${OTA_STATUS_FILE:-} ]]; then
        printf '%s\n' "$1" > "${OTA_STATUS_FILE}"
    fi
}

while (($#)); do
    case $1 in
        --ota) ota=${2:-}; shift 2 ;;
        --target-files) target_files=${2:-}; shift 2 ;;
        --build-id) build_id=${2:-}; shift 2 ;;
        --incremental) incremental=${2:-}; shift 2 ;;
        *) usage ;;
    esac
done
[[ -n "${ota}" && -n "${target_files}" && -n "${build_id}" ]] || usage

cleanup() {
    if [[ -n "${context}" && -d "${context}" ]]; then
        rm -rf "${context}"
    fi
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command missing: $1"
}

wait_for_health() {
    local target=$1
    local deadline=$((SECONDS + health_timeout))
    local state
    while ((SECONDS <= deadline)); do
        state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${target}" 2>/dev/null || true)
        case ${state} in
            healthy) return 0 ;;
            unhealthy|exited|dead) return 1 ;;
        esac
        sleep "${health_interval}"
    done
    return 1
}

container_get() {
    docker exec "${container_name}" sh -c "wget -q -O - http://127.0.0.1:${internal_port}$1 | grep -q '$2'"
}

container_range() {
    docker exec "${container_name}" sh -c \
        "printf 'GET $1 HTTP/1.1\\r\\nHost: localhost\\r\\nRange: bytes=0-0\\r\\nConnection: close\\r\\n\\r\\n' | nc 127.0.0.1 ${internal_port} | grep -q '206 Partial Content'"
}

verify_candidate() {
    local ota_name incremental_name
    ota_name=$(basename "${ota}")
    docker exec "${container_name}" wget -q -O /dev/null "http://127.0.0.1:${internal_port}/healthz" || return 1
    docker exec "${container_name}" wget -q -O /dev/null "http://127.0.0.1:${internal_port}/updates/salami.json" || return 1
    container_range "/install/salami/${build_id}/${ota_name}" || return 1
    container_get /updates/salami/1.json "${ota_name}" || return 1
    if [[ -n ${incremental} ]]; then
        incremental_name=$(basename "${incremental}")
        container_get "/updates/salami/${source_incremental}.json" "${incremental_name}" || return 1
        container_range "/install/salami/${build_id}/${incremental_name}" || return 1
    fi
}

rollback_transaction() {
    local rollback_failed=false
    report rolling-back-ota-release >&2
    if ${candidate_started}; then
        candidate_build=$(docker inspect --format '{{ index .Config.Labels "io.yrrp.ota.build-id" }}' "${container_name}" 2>/dev/null || true)
        if [[ ${candidate_build} == "${build_id}" ]]; then
            docker logs --tail 100 "${container_name}" >&2 || true
            docker rm -f "${container_name}" >/dev/null 2>&1 || true
        fi
    fi
    if ${previous_exists}; then
        if ${previous_renamed}; then
            docker rename "${container_name}.previous" "${container_name}" || rollback_failed=true
        fi
        if ${previous_stopped}; then
            docker start "${container_name}" >/dev/null || rollback_failed=true
            wait_for_health "${container_name}" || rollback_failed=true
        fi
    fi
    if [[ -n ${candidate_image} ]]; then
        docker image rm "${candidate_image}" >/dev/null 2>&1 || true
    fi
    if ${rollback_failed}; then
        echo "candidate failed and rollback did not become healthy" >&2
        return 1
    fi
    if ${previous_exists}; then
        echo "candidate failed; previous OTA release restored" >&2
    else
        echo "candidate failed; no previous OTA release existed" >&2
    fi
}

on_exit() {
    local exit_code=$?
    trap - EXIT INT TERM
    if ${deployment_complete}; then
        exit_code=0
    elif ((exit_code != 0)) && ${rollout_started}; then
        if ${previous_exists} && ! ${previous_renamed} \
            && docker container inspect "${container_name}.previous" >/dev/null 2>&1; then
            previous_renamed=true
            previous_stopped=true
        fi
        if ! rollback_transaction; then
            exit_code=70
        fi
    fi
    cleanup
    exit "${exit_code}"
}

trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p "$(dirname "${lock_file}")" "${work_dir}"
exec 9>"${lock_file}"
flock -n 9 || fail "another OTA deployment is running"
yrrp_require_build_lock_free || exit 1

require_command docker
require_command flock
require_command python3
[[ -S /var/run/docker.sock ]] || fail "Docker socket is unavailable"
[[ -x "${prepare_script}" ]] || fail "release preparer is missing or not executable: ${prepare_script}"
[[ ${OTA_PUBLIC_BASE_URL:-} == https://* ]] || fail "OTA_PUBLIC_BASE_URL must use HTTPS"
[[ -n ${OTA_BASE_IMAGE_REF:-} ]] || fail "OTA_BASE_IMAGE_REF is required"
docker version >/dev/null
docker buildx version >/dev/null
docker compose version >/dev/null
docker network inspect "${network}" >/dev/null

if docker container inspect "${container_name}" >/dev/null 2>&1; then
    previous_exists=true
    previous_label=$(docker inspect --format '{{ index .Config.Labels "io.yrrp.ota.release" }}' "${container_name}")
    [[ ${previous_label} == true ]] || fail "existing ${container_name} is not labeled as YRRP OTA release"
    previous_image=$(docker inspect --format '{{.Image}}' "${container_name}")
fi
if docker container inspect "${container_name}.previous" >/dev/null 2>&1; then
    fail "stale rollback container exists: ${container_name}.previous"
fi

report preparing-ota-release
docker pull "${OTA_BASE_IMAGE_REF}" >/dev/null
mapfile -t base_digests < <(
    docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${OTA_BASE_IMAGE_REF}" \
        | grep '@sha256:'
)
((${#base_digests[@]} == 1)) || fail "OTA base image must resolve to exactly one repository digest"
readonly base_digest=${base_digests[0]}
context=${work_dir}/release-${build_id}-$$
prepare_args=(
    --ota "${ota}"
    --target-files "${target_files}"
    --build-id "${build_id}"
    --public-base-url "${OTA_PUBLIC_BASE_URL}"
    --base-image-digest "${base_digest}"
    --output "${context}"
)
if [[ -n ${incremental} ]]; then
    prepare_args+=(--incremental "${incremental}")
fi
python3 "${prepare_script}" "${prepare_args[@]}" >/dev/null
if [[ -n ${incremental} ]]; then
    source_incremental=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["source_incremental"])' "${incremental}.json")
    [[ ${source_incremental} =~ ^[0-9]+$ ]] || fail "incremental meta has non-numeric source_incremental"
fi
cat > "${context}/Dockerfile" <<'EOF'
ARG OTA_BASE_IMAGE
FROM ${OTA_BASE_IMAGE}
COPY --chown=101:101 rootfs/ /srv/ota/
EOF

readonly image_tag=yrrp-ota-release:${build_id}
report building-ota-release-image
docker build \
    --build-arg "OTA_BASE_IMAGE=${base_digest}" \
    --label io.yrrp.ota.release=true \
    --label io.yrrp.ota.device=salami \
    --label "io.yrrp.ota.build-id=${build_id}" \
    --tag "${image_tag}" \
    "${context}" >/dev/null
candidate_image=$(docker image inspect --format '{{.Id}}' "${image_tag}")

report deploying-ota-release
rollout_started=true
if ${previous_exists}; then
    previous_stopped=true
    docker stop "${container_name}" >/dev/null
    docker rename "${container_name}" "${container_name}.previous"
    previous_renamed=true
fi

candidate_started=true
docker run -d \
    --name "${container_name}" \
    --hostname "${hostname}" \
    --network "${network}" \
    --network-alias "${network_alias}" \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,mode=1777 \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --restart unless-stopped \
    --label io.yrrp.ota.release=true \
    --label io.yrrp.ota.device=salami \
    --label "io.yrrp.ota.build-id=${build_id}" \
    "${image_tag}" >/dev/null

wait_for_health "${container_name}" || fail "candidate OTA container did not become healthy"
verify_candidate || fail "candidate OTA endpoint verification failed"

deployment_complete=true
report complete
if ${previous_exists}; then
    if ! docker rm -f "${container_name}.previous" >/dev/null; then
        echo "Warning: previous OTA container could not be removed" >&2
    fi
fi
if ${previous_exists} && [[ ${previous_image} != "${candidate_image}" ]]; then
    old_image_label=$(docker image inspect --format '{{ index .Config.Labels "io.yrrp.ota.release" }}' "${previous_image}" 2>/dev/null || true)
    old_image_users=$(docker ps -a --filter "ancestor=${previous_image}" --format '{{.ID}}')
    if [[ ${old_image_label} == true && -z ${old_image_users} ]]; then
        docker image rm "${previous_image}" >/dev/null || echo "Warning: previous OTA image could not be removed" >&2
    fi
fi
