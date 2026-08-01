"""source／binary build metadataの識別を検証する。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from securitymasker.distribution import distribution_info, version_text


def test_source_distribution_is_explicit() -> None:
    assert distribution_info().distribution == "source"
    assert distribution_info().binary_profile is None
    assert version_text("1.2.3") == "securitymasker 1.2.3 (source)"


@pytest.mark.parametrize("profile", ["lite", "full"])
def test_frozen_distribution_reads_known_embedded_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    (tmp_path / "securitymasker_build.json").write_text(
        json.dumps({"distribution": "binary", "binary_profile": profile}),
        encoding="utf-8",
    )
    monkeypatch.setattr("securitymasker.distribution.sys.frozen", True, raising=False)
    monkeypatch.setattr("securitymasker.distribution.sys._MEIPASS", str(tmp_path), raising=False)

    info = distribution_info()

    assert info.distribution == "binary"
    assert info.binary_profile == profile
    assert version_text("1.2.3") == f"securitymasker 1.2.3 (binary {profile})"


@pytest.mark.parametrize(
    "content",
    ["not json", '{"binary_profile":"unexpected"}', "{}"],
)
def test_frozen_distribution_does_not_guess_unknown_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    (tmp_path / "securitymasker_build.json").write_text(content, encoding="utf-8")
    monkeypatch.setattr("securitymasker.distribution.sys.frozen", True, raising=False)
    monkeypatch.setattr("securitymasker.distribution.sys._MEIPASS", str(tmp_path), raising=False)

    info = distribution_info()

    assert info.distribution == "binary"
    assert info.binary_profile is None
    assert version_text("1.2.3") == "securitymasker 1.2.3 (binary unknown-profile)"
