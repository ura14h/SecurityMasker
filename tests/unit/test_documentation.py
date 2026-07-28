"""READMEを入口にしたローカル文書導線を検証する。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_local_markdown_links_resolve() -> None:
    missing: list[str] = []
    documents = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]

    for document in documents:
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            path_text = target.split("#", 1)[0]
            if (
                not path_text
                or "://" in path_text
                or path_text.startswith(("mailto:", "#"))
            ):
                continue
            resolved = (document.parent / path_text).resolve()
            if not resolved.exists():
                missing.append(f"{document.relative_to(ROOT)} -> {target}")

    assert not missing, "存在しない文書リンク:\n" + "\n".join(missing)


def test_readme_routes_each_audience_without_repository_placeholder() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "docs/README.md" in readme
    assert "docs/development/codebase-guide.md" in readme
    assert "docs/adr/0016-reset-config-schema-version.md" in readme
    assert "<repository-url>" not in readme

    for document in (ROOT / "docs").rglob("*.md"):
        assert "<repository-url>" not in document.read_text(encoding="utf-8")
