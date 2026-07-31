#!/bin/sh
# macOS arm64のDocker Desktop上でLinux arm64 one-fileをnative build・検証する。
set -eu

SCRIPT_DIRECTORY=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIRECTORY=$(dirname -- "$SCRIPT_DIRECTORY")
IMAGE="${SM_LINUX_BINARY_GATE_IMAGE:-securitymasker-linux-arm64-binary-gate:local}"
OUTPUT="${SM_LINUX_BINARY_OUTPUT:-$PROJECT_DIRECTORY/dist/securitymasker-linux-arm64}"

if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' "error: Docker CLI was not found" >&2
    exit 2
fi
if [ -e "$OUTPUT" ]; then
    printf '%s\n' "error: output already exists: $OUTPUT" >&2
    exit 2
fi

cd "$PROJECT_DIRECTORY"

docker build \
    --platform linux/arm64 \
    --file docker/Dockerfile.binary-gate \
    --tag "$IMAGE" \
    .

ARCHITECTURE=$(docker image inspect --format '{{.Architecture}}' "$IMAGE")
if [ "$ARCHITECTURE" != "arm64" ]; then
    printf '%s\n' "error: binary gate image architecture is $ARCHITECTURE, not arm64" >&2
    exit 2
fi

printf '%s\n' "Running Python-free Linux arm64 network-none smoke."
docker run --rm \
    --network none \
    --read-only \
    --platform linux/arm64 \
    --tmpfs /tmp:rw,exec,mode=1777 \
    --tmpfs /work:rw,mode=1777 \
    --entrypoint /bin/sh \
    "$IMAGE" \
    -ec '
        test "$(uname -s)" = Linux
        test "$(uname -m)" = aarch64
        for network_interface in /sys/class/net/*; do
            test -d "$network_interface" || continue
            network_name=${network_interface##*/}
            network_flags=$(cat "$network_interface/flags")
            if [ "$network_name" != lo ] && [ $((network_flags & 1)) -ne 0 ]; then
                exit 1
            fi
        done
        while read -r route_interface destination route_rest; do
            if [ "$route_interface" != Iface ] \
                && [ "$route_interface" != lo ] \
                && [ "$destination" = 00000000 ]; then
                exit 1
            fi
        done < /proc/net/route
        while read -r destination prefix source source_prefix next_hop \
            metric reference use flags route_interface; do
            if [ "$route_interface" != lo ] \
                && [ "$destination" = 00000000000000000000000000000000 ] \
                && [ "$prefix" = 00 ]; then
                exit 1
            fi
        done < /proc/net/ipv6_route
        if command -v python3 >/dev/null 2>&1; then
            echo "error: clean runtime unexpectedly contains Python" >&2
            exit 1
        fi
        securitymasker --help >/dev/null
        securitymasker init --directory /work/product >/dev/null
        securitymasker config-check \
            --config /work/product/securitymasker.config >/dev/null
        preview=$(securitymasker preview "担当者は山田太郎です。" \
            --config /work/product/securitymasker.config)
        case "$preview" in *山田太郎*) exit 1;; esac
        case "$preview" in *SM_PERSON_*) :;; *) exit 1;; esac
    '

mkdir -p "$(dirname -- "$OUTPUT")"
CONTAINER_ID=$(docker create --platform linux/arm64 "$IMAGE")
cleanup() {
    docker rm --force "$CONTAINER_ID" >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM
docker cp "$CONTAINER_ID:/usr/local/bin/securitymasker" "$OUTPUT"
cleanup
trap - EXIT HUP INT TERM

chmod 0755 "$OUTPUT"
printf '%s\n' "Created $OUTPUT"
file "$OUTPUT"
wc -c "$OUTPUT"
shasum -a 256 "$OUTPUT"
printf '%s\n' "Linux arm64 one-file gate passed."
