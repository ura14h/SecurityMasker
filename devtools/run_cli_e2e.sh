#!/usr/bin/env bash
# Run the real-CLI E2E with egress actually blocked.
#
# The whole stack — pytest, the gateway, the mock upstream and the CLI — has to
# share one network namespace. Isolating only the CLI does not work: a namespace
# has its own loopback, so a CLI inside one cannot reach a gateway listening on
# the parent's 127.0.0.1. It would block egress and break the test at once.
#
# `unshare -r -n` gives this shell a fresh namespace whose only interface is `lo`,
# which starts down; bringing it up gives us loopback and nothing else. The test's
# own egress probe then fails, which is what lets the suite run at all.
#
# Linux network namespace専用。macOSではこの境界を同等に証明できないためrelease gateを実行しない。
# release gateではcodex/claudeの両方を必須とし、欠落や境界未確認を成功扱いしない。
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "error: needs a Linux network namespace for the complete E2E stack" >&2
    exit 2
fi

if ! command -v unshare >/dev/null 2>&1; then
    echo "error: 'unshare' not found (util-linux)." >&2
    exit 2
fi

PYTEST="${PYTEST:-python -m pytest}"
TARGET="${1:-tests/integration/test_real_cli_e2e.py}"

exec unshare --user --map-root-user --net -- sh -c '
    # Loopback exists but is down in a fresh namespace; everything talks over it.
    ip link set lo up 2>/dev/null || ifconfig lo up 2>/dev/null || true
    export SM_RUN_CLI_E2E=1
    export SM_REQUIRE_ALL_CLIS="${SM_REQUIRE_ALL_CLIS:-0}"
    exec '"$PYTEST"' "$@" -v
' sh "$TARGET"
