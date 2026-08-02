"""Windows nativeの別process SQLite lease／termination test helper。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from securitymasker.sessions.sqlite import SQLiteSessionStore

_SESSION_ID = "synthetic-windows-process-session"
_RESPONSE_ID = "synthetic-windows-process-response"


async def _prepare(store: SQLiteSessionStore) -> None:
    await store.get_or_create(_SESSION_ID, client_type="synthetic-test-client")
    await store.bind_response(_RESPONSE_ID, _SESSION_ID)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("key", type=Path)
    parser.add_argument("--mode", choices=["chatgpt", "claude"], default="chatgpt")
    args = parser.parse_args()

    store = SQLiteSessionStore(args.database, args.key, mode=args.mode)
    try:
        asyncio.run(_prepare(store))
        print("ready", flush=True)
        command = sys.stdin.readline().strip()
        if command not in {"", "close"}:
            return 2
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())

