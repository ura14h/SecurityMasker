"""CLI tests (§12: must not print original sensitive values)."""

from __future__ import annotations

from pathlib import Path

import pytest

from securitymasker.cli import main

CONFIG = """
version: 1
entities:
  - id: person
    type: PERSON
    values: ["山田太郎"]
    replacement_profile: prose_identifier
    restore_policy: literal
"""


@pytest.fixture
def config_path(tmp_path: Path) -> str:
    p = tmp_path / "c.yaml"
    p.write_text(CONFIG, encoding="utf-8")
    return str(p)


def test_config_validate_ok(config_path: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "validate", "--config", config_path]) == 0
    assert "OK" in capsys.readouterr().out


def test_config_validate_bad_returns_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "b.yaml"
    bad.write_text("version: 1\nentities:\n  - id: x\n    type: PERSON\n    replacement_profile: nope\n", encoding="utf-8")
    assert main(["config", "validate", "--config", str(bad)]) == 1


def test_entities_test_masks_and_hides_original(config_path: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["entities", "test", "担当は山田太郎です", "--config", config_path]) == 0
    out = capsys.readouterr().out
    assert "山田太郎" not in out  # original never printed (§12)
    assert "SM_PERSON_" in out
    assert "PERSON: 1" in out


def test_entities_list_shows_counts_not_values(config_path: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["entities", "list", "--config", config_path]) == 0
    out = capsys.readouterr().out
    assert "山田太郎" not in out
    assert "variants=1" in out


def test_doctor_runs(config_path: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor", "--config", config_path]) == 0
    assert "securitymasker" in capsys.readouterr().out
