"""Split a message body into typed spans (§17, doc/06 P1-7).

A chat message is rarely one kind of text. It is prose *around* a fenced code
block, a shell transcript, a diff someone pasted, a JSON blob. Treating the whole
body as ``prose`` makes fuzzy NER fire inside identifiers and code, and treating
it all as ``source_code`` would silence NER exactly where a name most needs
masking. Neither is acceptable, so the body is segmented and each span carries its
own ``ContextKind`` for the detector policy to act on.

Design constraints this module keeps:

- **Lossless.** The spans tile the input exactly: concatenating every
  ``segment.text`` in order reproduces the original byte-for-byte, including
  fences, blank lines, and trailing whitespace. Detection offsets are absolute
  positions in the original string, so nothing downstream has to translate twice.
- **Independent.** It knows nothing about detectors, HTTP, or sessions; it takes
  a string and returns spans. That keeps it unit-testable and keeps the masking
  core free of protocol concerns (§ architecture).
- **Conservative on failure.** Anything it cannot confidently classify stays
  ``prose``, which is the context with the FEWEST detectors disabled. Ambiguity
  therefore errs toward more scanning, never toward less (invariant 4).

It is a segmenter, not a parser: it recognises the shapes that matter for
detector policy, and deliberately does not try to understand the code inside a
fence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from securitymasker.models import ContextKind

# ``` or ~~~ fence, optional info string, to the matching closing fence or EOF.
_FENCE = re.compile(
    r"^(?P<indent>[ \t]{0,3})(?P<fence>`{3,}|~{3,})(?P<info>[^\n`]*)\n"
    r"(?P<body>.*?)"
    r"(?:^(?P=indent)(?P=fence)`*[ \t]*$|\Z)",
    re.MULTILINE | re.DOTALL,
)
# `code` / ``code`` on a single line.
_INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)(?P<code>[^\n]+?)(?<!`)\1(?!`)")

# Info strings that identify a fence's language, mapped to a context kind.
_SHELL_LANGS = frozenset({"sh", "bash", "zsh", "shell", "console", "shell-session",
                          "ps", "powershell", "fish"})
_DIFF_LANGS = frozenset({"diff", "patch", "udiff"})
_JSON_LANGS = frozenset({"json", "jsonc", "json5", "geojson"})
_YAML_LANGS = frozenset({"yaml", "yml"})

# A unified diff is recognisable without a fence: ---/+++ header then a @@ hunk.
_DIFF_BLOCK = re.compile(
    r"(?:^diff --git .*\n(?:^(?!diff --git ).*\n?)+)"
    r"|(?:^--- .*\n^\+\+\+ .*\n(?:^@@ .*\n(?:^[ +\-\\].*\n?)*)+)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class Segment:
    """One classified span of the original text."""

    start: int
    end: int
    kind: str
    text: str


def _fence_kind(info: str) -> str:
    """Context kind for a fence's info string (its declared language)."""
    lang = info.strip().split()[0].lower() if info.strip() else ""
    lang = lang.lstrip("{.").rstrip("}")
    if lang in _SHELL_LANGS:
        return ContextKind.SHELL.value
    if lang in _DIFF_LANGS:
        return ContextKind.DIFF.value
    if lang in _JSON_LANGS:
        return ContextKind.JSON_STRING.value
    if lang in _YAML_LANGS:
        return ContextKind.YAML_SCALAR.value
    if lang:
        return ContextKind.SOURCE_CODE.value
    # A fence with no language is still code, just unspecified.
    return ContextKind.MARKDOWN_CODE.value


def _claim(spans: list[tuple[int, int, str]], start: int, end: int, kind: str) -> None:
    if end > start:
        spans.append((start, end, kind))


def segment(text: str, *, default_kind: str = ContextKind.PROSE.value) -> list[Segment]:
    """Split ``text`` into non-overlapping, gap-free typed segments.

    ``default_kind`` is what unclaimed text becomes — ``prose`` for a chat body,
    but a caller that already knows it holds e.g. a tool result passes that, so
    the surrounding context is not silently downgraded.
    """
    if not text:
        return []

    claimed: list[tuple[int, int, str]] = []

    # Fenced blocks first: they win over everything inside them, including a diff
    # or inline backticks that happen to appear in the fenced body.
    for m in _FENCE.finditer(text):
        _claim(claimed, m.start(), m.end(), _fence_kind(m.group("info")))

    def _is_free(start: int, end: int) -> bool:
        return not any(s < end and start < e for s, e, _ in claimed)

    # Bare (unfenced) diffs pasted straight into a message.
    for m in _DIFF_BLOCK.finditer(text):
        if _is_free(m.start(), m.end()):
            _claim(claimed, m.start(), m.end(), ContextKind.DIFF.value)

    # Inline code outside fences and diffs.
    for m in _INLINE_CODE.finditer(text):
        if _is_free(m.start(), m.end()):
            _claim(claimed, m.start(), m.end(), ContextKind.MARKDOWN_INLINE_CODE.value)

    claimed.sort()

    # Tile the whole input: every gap between claims is the default kind, so the
    # segments concatenate back to the original exactly.
    out: list[Segment] = []
    cursor = 0
    for start, end, kind in claimed:
        if start < cursor:      # defensive: overlapping claims never escape here
            continue
        if start > cursor:
            out.append(Segment(cursor, start, default_kind, text[cursor:start]))
        out.append(Segment(start, end, kind, text[start:end]))
        cursor = end
    if cursor < len(text):
        out.append(Segment(cursor, len(text), default_kind, text[cursor:]))
    return out


def is_code_like(kind: str) -> bool:
    """True for contexts where fuzzy NER is unreliable (§17, doc/06 P1-7)."""
    return kind in _CODE_LIKE_KINDS


_CODE_LIKE_KINDS = frozenset({
    ContextKind.MARKDOWN_CODE.value,
    ContextKind.MARKDOWN_INLINE_CODE.value,
    ContextKind.SOURCE_CODE.value,
    ContextKind.SHELL.value,
    ContextKind.JSON_STRING.value,
    ContextKind.YAML_SCALAR.value,
    ContextKind.DIFF.value,
    ContextKind.PATCH.value,
})
