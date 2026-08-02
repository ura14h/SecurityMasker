"""Administrators所有の合成fileを製品security境界が拒否することを確認する。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from securitymasker.windows_security import (  # noqa: E402
    WindowsSecurityError,
    require_local_fixed_ntfs,
    require_no_reparse_points,
    require_private_dacl,
)


def main() -> int:
    if len(sys.argv) != 2:
        print("error: owner probe requires one fixture path", file=sys.stderr)
        return 2
    fixture = Path(sys.argv[1])
    require_local_fixed_ntfs(fixture)
    require_no_reparse_points(fixture)
    try:
        require_private_dacl(fixture, directory=False)
    except WindowsSecurityError as exc:
        if str(exc) != "managed path must be owned by the current user":
            raise
        print(json.dumps({"wrong_owner_rejected": True}, separators=(",", ":")))
        return 0
    print("error: wrong-owner fixture was accepted", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
