"""利用者向けCLIリファレンスとargparse定義の網羅性を検証する。"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from securitymasker.cli import build_parser

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "docs" / "reference" / "cli.md"


def _leaf_parsers(
    parser: argparse.ArgumentParser,
    path: tuple[str, ...] = ("securitymasker",),
) -> dict[tuple[str, ...], argparse.ArgumentParser]:
    subparsers = next(
        (
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ),
        None,
    )
    if subparsers is None:
        return {path: parser}
    leaves: dict[tuple[str, ...], argparse.ArgumentParser] = {}
    for name, child in subparsers.choices.items():
        leaves.update(_leaf_parsers(child, (*path, name)))
    return leaves


def _section(document: str, command: tuple[str, ...]) -> str:
    heading = f"## `{' '.join(command)}`"
    start = document.index(heading)
    next_heading = document.find("\n## `", start + len(heading))
    return document[start:] if next_heading < 0 else document[start:next_heading]


def test_reference_covers_every_leaf_command_and_argument() -> None:
    document = REFERENCE.read_text(encoding="utf-8")
    leaves = _leaf_parsers(build_parser())
    documented = {
        tuple(match.split())
        for match in re.findall(
            r"^## `(securitymasker(?: [a-z-]+)+)`$",
            document,
            re.MULTILINE,
        )
    }

    assert documented == set(leaves)
    for command, parser in leaves.items():
        section = _section(document, command)
        for action in parser._actions:
            if isinstance(action, argparse._HelpAction):
                continue
            names = [name for name in action.option_strings if name.startswith("--")]
            token = names[0] if names else action.dest.upper()
            assert token in section, (
                f"{' '.join(command)}の{token}がCLIリファレンスにありません"
            )


def test_reference_documents_common_help_version_and_exit_codes() -> None:
    document = REFERENCE.read_text(encoding="utf-8")

    for token in ("`-h`", "`--help`", "`--version`", "終了code", "`0`", "`1`", "`2`"):
        assert token in document
