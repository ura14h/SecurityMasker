"""Windows removable drive gateのselectionとrunner契約を検査する。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from devtools.windows_removable_probe import select_removable_root

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/windows-removable-gate.cmd"


def _type_for(types: dict[str, int]) -> Callable[[str], int]:
    return lambda root: types.get(root, 1)


def test_explicit_ready_removable_drive_is_selected() -> None:
    root = select_removable_root(
        "e:",
        drive_type=_type_for({"E:\\": 2}),
        is_ready=lambda _root: True,
    )

    assert root == Path("E:\\")


@pytest.mark.parametrize(
    ("argument", "drive_types", "ready", "message"),
    [
        ("relative", {}, True, "drive letter"),
        ("E:", {"E:\\": 2}, False, "not ready"),
        ("E:", {"E:\\": 3}, True, "not classified as removable"),
    ],
)
def test_invalid_explicit_drive_is_rejected(
    argument: str,
    drive_types: dict[str, int],
    ready: bool,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        select_removable_root(
            argument,
            drive_type=_type_for(drive_types),
            is_ready=lambda _root: ready,
        )


def test_only_ready_removable_drive_is_auto_selected() -> None:
    root = select_removable_root(
        None,
        drive_type=_type_for({"E:\\": 2, "F:\\": 2}),
        is_ready=lambda candidate: candidate == Path("F:\\"),
    )

    assert root == Path("F:\\")


def test_missing_or_ambiguous_removable_drive_is_rejected() -> None:
    with pytest.raises(ValueError, match="no ready removable"):
        select_removable_root(None, drive_type=_type_for({}), is_ready=lambda _root: True)
    with pytest.raises(ValueError, match="multiple removable"):
        select_removable_root(
            None,
            drive_type=_type_for({"E:\\": 2, "F:\\": 2}),
            is_ready=lambda _root: True,
        )


def test_cmd_runner_uses_read_only_probe_and_optional_drive_argument() -> None:
    source = RUNNER.read_text(encoding="utf-8")

    assert "windows_removable_probe.py" in source
    assert '"%PYTHON%" "%PROBE%" %*' in source
    assert 'for /d %%D in ("%LOCALAPPDATA%\\Python\\pythoncore-*-64")' in source
    assert "powershell.exe" not in source
    assert "Remove-Item" not in source
