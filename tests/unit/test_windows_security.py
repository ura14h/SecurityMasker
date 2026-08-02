"""Windows nativeのDACL contractを実OS上で検証する。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native DACL contract")


def test_secure_file_and_directory_are_accepted(tmp_path: Path) -> None:
    from securitymasker.windows_security import require_private_dacl, secure_path

    root = tmp_path / "private"
    root.mkdir()
    secure_path(root, directory=True)
    secret = root / "secret.bin"
    secret.write_bytes(b"synthetic-value")
    secure_path(secret, directory=False)

    require_private_dacl(root, directory=True)
    require_private_dacl(secret, directory=False)


def test_inherited_permissive_acl_is_rejected(tmp_path: Path) -> None:
    from securitymasker.windows_security import WindowsSecurityError, require_private_dacl

    target = tmp_path / "inherited.txt"
    target.write_text("synthetic-value", encoding="utf-8")

    with pytest.raises(WindowsSecurityError, match="inheritance|unexpected principal"):
        require_private_dacl(target, directory=False)


def test_everyone_ace_is_rejected(tmp_path: Path) -> None:
    from securitymasker.windows_security import (
        WindowsSecurityError,
        require_private_dacl,
        secure_path,
    )

    target = tmp_path / "permissive.txt"
    target.write_text("synthetic-value", encoding="utf-8")
    secure_path(target, directory=False)
    completed = subprocess.run(
        ["icacls.exe", str(target), "/grant", "*S-1-1-0:(R)"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0

    with pytest.raises(WindowsSecurityError, match="unexpected principal"):
        require_private_dacl(target, directory=False)


def test_local_ntfs_volume_is_accepted(tmp_path: Path) -> None:
    from securitymasker.windows_security import require_local_fixed_ntfs

    require_local_fixed_ntfs(tmp_path)

