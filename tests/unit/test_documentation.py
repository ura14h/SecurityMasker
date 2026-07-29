"""READMEを入口にしたローカル文書導線を検証する。"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]


def _documents() -> list[Path]:
    root_documents = (
        ROOT / "README.md",
        ROOT / "SECURITY.md",
        ROOT / "THIRD_PARTY_NOTICES.md",
        ROOT / "CHANGELOG.md",
    )
    return [*root_documents, *sorted((ROOT / "docs").rglob("*.md"))]


def _heading_anchors(document: Path) -> set[str]:
    anchors: set[str] = set()
    occurrences: dict[str, int] = {}
    in_fence = False
    for line in document.read_text(encoding="utf-8").splitlines():
        if re.match(r"^\s*`{3,}", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match is None:
            continue
        heading = re.sub(r"<[^>]+>", "", match.group(1))
        heading = re.sub(r"[^\w\s-]", "", heading.lower(), flags=re.UNICODE)
        base = re.sub(r"\s+", "-", heading.strip())
        occurrence = occurrences.get(base, 0)
        occurrences[base] = occurrence + 1
        anchors.add(base if occurrence == 0 else f"{base}-{occurrence}")
    return anchors


def test_local_markdown_links_resolve() -> None:
    missing: list[str] = []

    for document in _documents():
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            path_text, separator, fragment = target.partition("#")
            if (
                not path_text
                or "://" in path_text
                or path_text.startswith(("mailto:", "#"))
            ):
                continue
            resolved = (document.parent / path_text).resolve()
            if not resolved.exists():
                missing.append(f"{document.relative_to(ROOT)} -> {target}")
                continue
            if (
                separator
                and resolved.suffix == ".md"
                and unquote(fragment) not in _heading_anchors(resolved)
            ):
                missing.append(
                    f"{document.relative_to(ROOT)} -> {target} "
                    "(存在しない見出し)"
                )

    assert not missing, "存在しない文書リンク:\n" + "\n".join(missing)


def test_readme_routes_each_audience_without_repository_placeholder() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "docs/README.md" in readme
    assert "docs/getting-started.md" in readme
    assert "docs/security/safe-use.md" in readme
    assert "docs/guides/customize-dictionary.md" in readme
    assert "<repository-url>" not in readme

    for document in (ROOT / "docs").rglob("*.md"):
        assert "<repository-url>" not in document.read_text(encoding="utf-8")


def test_reader_entry_points_do_not_depend_on_history_or_release_status() -> None:
    entry_points = (
        ROOT / "README.md",
        ROOT / "docs" / "getting-started.md",
        ROOT / "docs" / "security" / "safe-use.md",
    )

    for document in entry_points:
        text = document.read_text(encoding="utf-8")
        assert "docs/adr/" not in text
        assert "development/status.md" not in text
        assert "development/testing.md" not in text


def test_obsolete_reader_taxonomy_is_empty() -> None:
    assert not list((ROOT / "docs" / "user").glob("*.md"))
    assert not list((ROOT / "docs" / "design").glob("*.md"))
