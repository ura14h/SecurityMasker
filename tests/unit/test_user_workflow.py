"""利用者が通常運用で使用するCLI workflowを検証する。"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

from securitymasker.bootstrap import initialize_layout
from securitymasker.cli import main
from securitymasker.config import load_config
from securitymasker.integrations.client_config import client_setup_snippet

SYNTHETIC_PERSON = "佐々木健一"


def _disable_ner(config_path: Path) -> None:
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        text.replace("  japanese_ner:\n    enabled: true", "  japanese_ner:\n    enabled: false"),
        encoding="utf-8",
    )
    config_path.chmod(0o600)


def test_preview_uses_standard_ner_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    layout = initialize_layout(tmp_path, mode="chatgpt", port=45671)
    original_connect = socket.socket.connect

    def no_network(self: socket.socket, address: object) -> None:
        # Windowsのasyncioはevent loop内部のwake-upにloopback socketpairを使う。
        if (
            isinstance(address, tuple)
            and address
            and address[0] in {"127.0.0.1", "::1"}
        ):
            original_connect(self, address)
            return
        raise AssertionError(f"preview attempted network access: {type(address).__name__}")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    assert main(
        ["preview", f"担当者は{SYNTHETIC_PERSON}です。", "--config", str(layout.config)]
    ) == 0

    output = capsys.readouterr()
    assert SYNTHETIC_PERSON not in output.out + output.err
    assert "SM_PERSON_" in output.out
    assert "PERSON: 1" in output.out


@pytest.mark.parametrize(
    ("mode", "port", "expected", "unexpected"),
    [
        ("chatgpt", 45672, 'base_url = "http://127.0.0.1:45672"', "ANTHROPIC_BASE_URL"),
        ("claude", 45673, 'ANTHROPIC_BASE_URL="http://127.0.0.1:45673"', "wire_api"),
    ],
)
def test_client_config_prints_only_the_configured_mode_from_shared_generator(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    port: int,
    expected: str,
    unexpected: str,
) -> None:
    layout = initialize_layout(tmp_path, mode=mode, port=port)
    config = load_config(layout.config)

    assert main(["client-config", "--config", str(layout.config)]) == 0
    output = capsys.readouterr().out

    assert output == client_setup_snippet(config)
    rendered_expected = (
        'set "ANTHROPIC_BASE_URL=http://127.0.0.1:45673"'
        if os.name == "nt" and mode == "claude"
        else expected
    )
    assert rendered_expected in output
    assert unexpected not in output
    assert "\nmodel = " not in output


def test_doctor_is_read_only_and_does_not_expose_dictionary_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    layout = initialize_layout(tmp_path / "product", mode="chatgpt", port=45674)
    _disable_ner(layout.config)
    dictionary = layout.dictionary
    dictionary.write_text(
        dictionary.read_text(encoding="utf-8").replace(
            "合成テスト株式会社", "合成秘密会社サンプル"
        ),
        encoding="utf-8",
    )
    dictionary.chmod(0o600)

    codex_home = tmp_path / "client-home"
    codex_home.mkdir()
    config = load_config(layout.config)
    client_file = codex_home / "config.toml"
    client_file.write_text(client_setup_snippet(config).split("\n", 2)[2], encoding="utf-8")
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    assert main(["doctor", "--config", str(layout.config), "--json"]) == 0
    output = capsys.readouterr()
    parsed = json.loads(output.out)

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert not (layout.state_directory / "securitymasker.db").exists()
    assert parsed["ok"] is True
    assert "合成秘密会社サンプル" not in output.out + output.err
    statuses = {item["name"]: item["status"] for item in parsed["checks"]}
    assert statuses["dictionary"] == "ok"
    assert statuses["state"] == "ok"
    assert statuses["port"] in {"ok", "warn"}
    assert statuses["clients"] == "ok"
