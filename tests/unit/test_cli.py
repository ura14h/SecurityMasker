"""CLIが元の機密値を表示せず安全に動作することを検証する。"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from securitymasker.bootstrap import initialize_layout
from securitymasker.cli import main


@pytest.fixture
def config_path(tmp_path: Path) -> str:
    return str(initialize_layout(tmp_path, mode="chatgpt", port=49154).config)


def test_config_check_ok(config_path: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config-check", "--config", config_path]) == 0
    assert "OK" in capsys.readouterr().out


def test_config_check_bad_returns_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "b.yaml"
    bad.write_text("version: 1\nentities:\n  - id: x\n    type: PERSON\n    replacement_profile: nope\n", encoding="utf-8")
    assert main(["config-check", "--config", str(bad)]) == 1


def test_preview_masks_and_hides_original(config_path: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["preview", "担当は山田太郎です", "--config", config_path]) == 0
    out = capsys.readouterr().out
    assert "山田太郎" not in out  # 元の機密値は表示しない
    assert "SM_PERSON_" in out
    assert "PERSON: 1" in out


def test_preview_reads_standard_input(
    config_path: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original = "担当は山田太郎です\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(original))

    assert main(["preview", "--config", config_path]) == 0
    output = capsys.readouterr()
    assert original.strip() not in output.out + output.err
    assert "SM_PERSON_" in output.out
    assert "PERSON: 1" in output.out


@pytest.mark.parametrize("standard_input", [io.StringIO(""), pytest.param(None, id="tty")])
def test_preview_rejects_missing_standard_input(
    config_path: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    standard_input: io.StringIO | None,
) -> None:
    class InteractiveInput(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(
        sys,
        "stdin",
        standard_input if standard_input is not None else InteractiveInput(),
    )

    assert main(["preview", "--config", config_path]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "TEXT or non-empty standard input" in output.err


def test_entities_shows_counts_not_values(config_path: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["entities", "--config", config_path]) == 0
    out = capsys.readouterr().out
    assert "山田太郎" not in out
    assert "variants=2" in out
    assert "variants=3" in out


def test_model_load_prepares_explicit_model(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, str, bool]] = []

    def prepare(model: str, revision: str, *, allow_unverified: bool) -> SimpleNamespace:
        calls.append((model, revision, allow_unverified))
        return SimpleNamespace(
            model=model,
            revision=revision,
            verified=["model.safetensors"],
            ok=True,
        )

    monkeypatch.setattr("securitymasker.models_fetch.fetch", prepare)

    assert main(
        ["model-load", "--model", "example/model", "--revision", "fixed-revision"]
    ) == 0
    assert calls == [("example/model", "fixed-revision", False)]
    assert "model ready: example/model@fixed-revision" in capsys.readouterr().out


def test_doctor_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    layout = initialize_layout(tmp_path, mode="chatgpt", port=49152)
    assert main(["doctor", "--config", str(layout.config)]) == 0
    assert "[ok  ] config:" in capsys.readouterr().out
