"""実CLI E2Eのnetwork隔離構造判定。"""

from __future__ import annotations

from pathlib import Path

from tests.integration import test_real_cli_e2e


def _write_flags(sysfs: Path, name: str, flags: str) -> None:
    interface = sysfs / name
    interface.mkdir(parents=True)
    (interface / "flags").write_text(flags, encoding="ascii")


def test_down_kernel_tunnel_interfaces_are_not_active(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        test_real_cli_e2e.socket,
        "if_nameindex",
        lambda: [(1, "lo"), (2, "gre0"), (3, "erspan0")],
    )
    _write_flags(tmp_path, "gre0", "0x80\n")
    _write_flags(tmp_path, "erspan0", "0x1002\n")

    assert test_real_cli_e2e._active_non_loopback_interfaces(tmp_path) == []


def test_up_non_loopback_interface_is_active(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        test_real_cli_e2e.socket,
        "if_nameindex",
        lambda: [(1, "lo"), (2, "eth0")],
    )
    _write_flags(tmp_path, "eth0", "0x1003\n")

    assert test_real_cli_e2e._active_non_loopback_interfaces(tmp_path) == ["eth0"]


def test_unreadable_interface_state_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        test_real_cli_e2e.socket,
        "if_nameindex",
        lambda: [(1, "lo"), (2, "mystery0")],
    )

    assert test_real_cli_e2e._active_non_loopback_interfaces(tmp_path) == [
        "mystery0"
    ]
