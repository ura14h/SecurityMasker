"""project licenseと第三者componentの境界が配布metadataで一致することを検証する。"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_project_license_is_consistently_mit() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    dockerfile = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / "docker" / "Dockerfile.dockerignore").read_text(
        encoding="utf-8"
    )
    spec = (ROOT / "securitymasker.spec").read_text(encoding="utf-8")

    assert project["project"]["license"] == "MIT"
    assert license_text.startswith("MIT License\n")
    assert "Copyright (c) 2026 Hiroki Ishiura" in license_text
    assert 'org.opencontainers.image.licenses="MIT"' in dockerfile
    assert "THIRD_PARTY_NOTICES.md" in dockerfile
    assert "!THIRD_PARTY_NOTICES.md" in dockerignore
    assert 'project / "THIRD_PARTY_NOTICES.md"' in spec
    assert "Apache License" not in license_text


def test_readme_distinguishes_project_and_third_party_licenses() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    assert "[MIT License](LICENSE)" in readme
    assert "[Third-party notices](THIRD_PARTY_NOTICES.md)" in readme
    assert "source archive" in notices
    assert "Binary release" in notices
    assert "CC BY-SA 3.0" in notices
