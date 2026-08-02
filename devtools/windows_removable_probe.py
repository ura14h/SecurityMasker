"""実removable driveを製品のWindows volume境界が拒否することを確認する。"""

from __future__ import annotations

import ctypes
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from securitymasker.windows_security import (  # noqa: E402
    WindowsSecurityError,
    require_local_fixed_ntfs,
)

_DRIVE_REMOVABLE = 2


def _get_drive_type(root: str) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel32.GetDriveTypeW
    function.argtypes = [ctypes.c_wchar_p]
    function.restype = ctypes.c_uint
    return int(function(root))


def _is_ready(root: Path) -> bool:
    try:
        return root.is_dir()
    except OSError:
        return False


def select_removable_root(
    argument: str | None,
    *,
    drive_type: Callable[[str], int] = _get_drive_type,
    is_ready: Callable[[Path], bool] = _is_ready,
) -> Path:
    """指定drive、または一意な接続済みremovable driveのrootを返す。"""
    if argument is not None:
        if re.fullmatch(r"[A-Za-z]:\\?", argument) is None:
            raise ValueError("drive must be a drive letter such as E:")
        root = Path(f"{argument[0].upper()}:\\")
        if not is_ready(root):
            raise ValueError("specified removable drive is not ready")
        if drive_type(str(root)) != _DRIVE_REMOVABLE:
            raise ValueError("specified drive is not classified as removable")
        return root

    candidates = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        root = Path(f"{letter}:\\")
        if drive_type(str(root)) == _DRIVE_REMOVABLE and is_ready(root):
            candidates.append(root)
    if not candidates:
        raise ValueError("no ready removable drive was found")
    if len(candidates) > 1:
        raise ValueError("multiple removable drives found; specify one drive letter")
    return candidates[0]


def main() -> int:
    if os.name != "nt":
        print("error: removable drive gate requires Windows", file=sys.stderr)
        return 2
    if len(sys.argv) > 2:
        print("error: usage: windows_removable_probe.py [DRIVE:]", file=sys.stderr)
        return 2
    try:
        root = select_removable_root(sys.argv[1] if len(sys.argv) == 2 else None)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        require_local_fixed_ntfs(root)
    except WindowsSecurityError as exc:
        if str(exc) != "managed path must be on a local fixed drive":
            print(f"error: unexpected removable drive rejection: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"removable_drive_rejected": True}, separators=(",", ":")))
        return 0
    print("error: removable drive was accepted", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
