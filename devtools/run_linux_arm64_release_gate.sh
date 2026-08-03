#!/bin/sh
# macOS arm64のDocker Desktop上でLinux arm64 source release gateを実行する。
#
# online phaseは固定した合成値だけを実OpenAIへ送り、offline phaseは同じimageを
# `--network none`で起動して実Codex／Claude Code CLIをlocal mockへ接続する。
# hostのCodex認証はonline containerへread-only mountし、imageへは含めない。
set -eu

SCRIPT_DIRECTORY=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIRECTORY=$(dirname -- "$SCRIPT_DIRECTORY")
IMAGE="${SM_LINUX_GATE_IMAGE:-securitymasker-linux-arm64-gate:local}"
CODEX_AUTH_FILE="${CODEX_HOME:-$HOME/.codex}/auth.json"

if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' "error: Docker CLI was not found" >&2
    exit 2
fi
if [ ! -f "$CODEX_AUTH_FILE" ]; then
    printf '%s\n' "error: Codex authentication file was not found" >&2
    exit 2
fi

cd "$PROJECT_DIRECTORY"

docker build \
    --platform linux/arm64 \
    --file docker/Dockerfile.release-gate \
    --tag "$IMAGE" \
    .

ARCHITECTURE=$(docker image inspect --format '{{.Architecture}}' "$IMAGE")
if [ "$ARCHITECTURE" != "arm64" ]; then
    printf '%s\n' "error: release gate image architecture is $ARCHITECTURE, not arm64" >&2
    exit 2
fi

printf '%s\n' "Running Linux arm64 online source gate."
docker run --rm \
    --platform linux/arm64 \
    --mount "type=bind,source=$CODEX_AUTH_FILE,target=/home/gate/.codex/auth.json,readonly" \
    "$IMAGE" \
    /bin/sh -ec '
        test "$(uname -s)" = Linux
        test "$(uname -m)" = aarch64
        test "$(python3.12 -c "import platform; print(platform.machine())")" = aarch64
        codex --version
        claude --version
        .venv/bin/ruff check src tests devtools
        .venv/bin/mypy src
        SM_REQUIRE_MODEL=1 .venv/bin/python -m pytest tests/unit tests/evaluation -q
        SM_RUN_LIVE=1 .venv/bin/python -m pytest tests/integration/test_live_gateway.py -q
        SM_RUN_OPENAI_E2E=1 SM_OPENAI_E2E_COMPARE_HTTP=1 SM_OPENAI_E2E_TOOL_CALLS=8 \
            .venv/bin/python -m pytest tests/integration/test_real_openai_e2e.py -q -s
    '

if [ "${SM_RUN_EXTENDED_CLI_E2E:-0}" = "1" ]; then
    printf '%s\n' "Running optional Linux arm64 network-none real-CLI gate."
    docker run --rm \
        --network none \
        --platform linux/arm64 \
        "$IMAGE" \
        /bin/sh -ec '
            test "$(uname -s)" = Linux
            test "$(uname -m)" = aarch64
            SM_RUN_CLI_E2E=1 SM_REQUIRE_ALL_CLIS=1 \
                .venv/bin/python -m pytest tests/integration/test_real_cli_e2e.py -v
        '
else
    printf '%s\n' "Skipping optional network-none real-CLI compatibility gate."
fi

printf '%s\n' "Linux arm64 source release gate passed."
