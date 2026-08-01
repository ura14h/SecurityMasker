#!/bin/sh
# macOS arm64のDocker Desktop上でLinux arm64 one-fileをnative build・検証する。
set -eu

SCRIPT_DIRECTORY=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIRECTORY=$(dirname -- "$SCRIPT_DIRECTORY")
IMAGE_PREFIX="${SM_LINUX_BINARY_GATE_IMAGE_PREFIX:-securitymasker-linux-arm64-binary-gate}"
LITE_OUTPUT="${SM_LINUX_BINARY_LITE_OUTPUT:-$PROJECT_DIRECTORY/dist/securitymasker-linux-arm64-lite}"
FULL_OUTPUT="${SM_LINUX_BINARY_FULL_OUTPUT:-$PROJECT_DIRECTORY/dist/securitymasker-linux-arm64-full}"

if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' "error: Docker CLI was not found" >&2
    exit 2
fi
if [ -e "$LITE_OUTPUT" ]; then
    printf '%s\n' "error: output already exists: $LITE_OUTPUT" >&2
    exit 2
fi
if [ -e "$FULL_OUTPUT" ]; then
    printf '%s\n' "error: output already exists: $FULL_OUTPUT" >&2
    exit 2
fi

cd "$PROJECT_DIRECTORY"

CONTAINER_ID=
cleanup() {
    if [ -n "$CONTAINER_ID" ]; then
        docker rm --force "$CONTAINER_ID" >/dev/null 2>&1 || true
        CONTAINER_ID=
    fi
}
trap cleanup EXIT HUP INT TERM

run_profile() {
    PROFILE=$1
    IMAGE=$2
    OUTPUT=$3

    docker build \
        --platform linux/arm64 \
        --build-arg "BINARY_PROFILE=$PROFILE" \
        --file docker/Dockerfile.binary-gate \
        --tag "$IMAGE" \
        .

    ARCHITECTURE=$(docker image inspect --format '{{.Architecture}}' "$IMAGE")
    if [ "$ARCHITECTURE" != "arm64" ]; then
        printf '%s\n' \
            "error: $PROFILE binary gate image architecture is $ARCHITECTURE, not arm64" >&2
        exit 2
    fi

    printf '%s\n' "Running $PROFILE Python-free Linux arm64 network-none smoke."
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
        securitymasker --version
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
    docker cp "$CONTAINER_ID:/usr/local/bin/securitymasker" "$OUTPUT"
    cleanup

    chmod 0755 "$OUTPUT"
    printf '%s\n' "Created $OUTPUT"
    file "$OUTPUT"
    wc -c "$OUTPUT"
    shasum -a 256 "$OUTPUT"
}

run_profile lite "$IMAGE_PREFIX-lite:local" "$LITE_OUTPUT"
run_profile full "$IMAGE_PREFIX-full:local" "$FULL_OUTPUT"

trap - EXIT HUP INT TERM
printf '%s\n' "Linux arm64 Lite and Full one-file gates passed."
