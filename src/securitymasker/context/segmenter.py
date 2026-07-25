"""Split a message body into typed spans (§17, doc/06 P1-7, ADR-0011).

A chat message is rarely one kind of text. It is prose *around* a fenced code
block, a shell transcript, a pasted diff, a JSON blob. Treating the whole body as
``prose`` makes fuzzy NER fire inside identifiers; treating it all as code would
silence NER exactly where a name most needs masking. So the body is segmented and
each span carries its own ``ContextKind``.

Design constraints:

- **Lossless.** The spans tile the input exactly: concatenating every
  ``segment.text`` reproduces the original byte-for-byte, fences and all.
  Detection offsets are absolute positions in the original string.
- **Linear.** Claims are collected, sorted once, and swept — never compared
  pairwise. The previous all-pairs overlap test was O(n²): 63 KB with 8,000
  inline-code spans took ~0.77 s and produced 16,001 segments.
- **Bounded.** A single request cannot be turned into unbounded work. Segment
  count is capped, and adjacent prose fragments are coalesced so a document
  alternating prose and code does not become thousands of separate detector
  invocations (see ``MAX_SEGMENTS``).
- **Conservative on failure.** Anything unrecognised stays ``prose``, the context
  with the FEWEST detectors disabled, so ambiguity errs toward more scanning.

It is a segmenter, not a parser: it recognises the shapes that matter for detector
policy and does not try to understand the code inside a fence.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

from securitymasker.errors import MaskingError
from securitymasker.models import ContextKind

# Upper bound on spans per body. Beyond this the caller stops segmenting and
# treats the remainder as one prose span: pathological input must not translate
# into unbounded detector work (ADR-0011). Generous for real documents.
MAX_SEGMENTS = 512

# Hard ceiling on structural claims found in one body. Capping alone keeps the work
# bounded, but an input engineered to produce tens of thousands of claims is not a
# document anyone wrote — it is an attempt to make us do work. Past this we refuse
# the request outright rather than silently degrade it, so the original never
# reaches an upstream on a path we did not fully analyse (invariant 4, ADR-0011).
MAX_CLAIMS = 4 * MAX_SEGMENTS

# ``` or ~~~ fence, optional info string, to the matching closing fence or EOF.
_FENCE = re.compile(
    r"^(?P<indent>[ \t]{0,3})(?P<fence>`{3,}|~{3,})(?P<info>[^\n`]*)\n"
    r"(?P<body>.*?)"
    r"(?:^(?P=indent)(?P=fence)`*[ \t]*$|\Z)",
    re.MULTILINE | re.DOTALL,
)
_INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)(?P<code>[^\n]+?)(?<!`)\1(?!`)")

_SHELL_LANGS = frozenset({"sh", "bash", "zsh", "shell", "console", "shell-session",
                          "ps", "powershell", "fish"})
_DIFF_LANGS = frozenset({"diff", "patch", "udiff"})
_JSON_LANGS = frozenset({"json", "jsonc", "json5", "geojson"})
_YAML_LANGS = frozenset({"yaml", "yml"})

# --- unfenced block shapes -------------------------------------------------------
# Recognised WITHOUT a fence, because people paste these raw. Each is anchored on a
# structural marker rather than on content, so ordinary prose cannot match.

# Unified diff: a git header, or ---/+++ followed by a hunk.
_DIFF_BLOCK = re.compile(
    r"(?:^diff --git .*\n(?:^(?!diff --git ).*\n?)+)"
    r"|(?:^--- .*\n^\+\+\+ .*\n(?:^@@ .*\n(?:^[ +\-\\].*\n?)*)+)",
    re.MULTILINE,
)
# OpenAI apply_patch envelope.
_APPLY_PATCH = re.compile(
    r"^\*\*\* Begin Patch\s*\n.*?(?:^\*\*\* End Patch\s*$|\Z)",
    re.MULTILINE | re.DOTALL,
)
# A shell transcript: consecutive lines that start with a prompt marker.
_SHELL_BLOCK = re.compile(r"(?:^[ \t]*[$#>][ \t]+\S.*\n?)+", re.MULTILINE)
# A bare command line with no prompt: a known binary followed by a pipe, a
# redirect, or a flag. Anchored on the COMMAND NAME so ordinary prose that merely
# contains "|" cannot match — the previous rule needed a "$ " prompt and so missed
# every pasted one-liner.
_SHELL_COMMAND = re.compile(
    r"^[ \t]*(?:sudo[ \t]+)?(?:grep|rg|awk|sed|cat|ls|cp|mv|rm|curl|wget|kubectl|"
    r"docker|git|psql|mysql|ssh|scp|tar|find|xargs|jq|echo|export|chmod|chown|"
    r"systemctl|journalctl|npm|pip|python|node)\b"
    r"(?=.*(?:[|><]|[ \t]-{1,2}[A-Za-z]))[^\n]*$",
    re.MULTILINE,
)
# A single SQL statement.
_SQL_STATEMENT = re.compile(
    r"^[ \t]*(?:SELECT|INSERT[ \t]+INTO|UPDATE|DELETE[ \t]+FROM|CREATE[ \t]+(?:TABLE|INDEX)|"
    r"ALTER[ \t]+TABLE|DROP[ \t]+TABLE)\b[^\n]*;[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)
# A single-line declaration/assignment in a C-family or scripting language.
_CODE_STATEMENT = re.compile(
    r"^[ \t]*(?:const|let|var|public|private|protected|static|final)[ \t]+"
    r"[A-Za-z_$][\w$]*[^\n]*[;{][ \t]*$",
    re.MULTILINE,
)
# A bare JSON object/array occupying whole lines.
_JSON_BLOCK = re.compile(r"^[ \t]*[{\[][\s\S]*?^[ \t]*[}\]][ \t]*$", re.MULTILINE)
# A YAML document or a run of `key: value` lines (2+, so a prose colon is safe).
_YAML_BLOCK = re.compile(
    r"(?:^---[ \t]*\n(?:^(?!---).*\n?)+)"
    r"|(?:^[ \t]*[A-Za-z_][\w.-]*:(?:[ \t].*)?\n){2,}",
    re.MULTILINE,
)
# Source code: a run of lines with language keywords at line start.
_SOURCE_BLOCK = re.compile(
    r"(?:^[ \t]*(?:def |class |function |func |import |from \S+ import |"
    r"public |private |const |let |var |package |#include|SELECT |INSERT |UPDATE )"
    r".*\n(?:^(?![ \t]*$).*\n?)*)",
    re.MULTILINE,
)


_LIMIT_MESSAGE = (
    f"input produced more than {MAX_CLAIMS} structural spans; refusing to process "
    "it rather than analysing it partially"
)


class SegmentationLimitError(MaskingError):
    """Input exceeded the structural-span ceiling; the request must fail closed."""


@dataclass(frozen=True)
class Segment:
    """One classified span of the original text."""

    start: int
    end: int
    kind: str
    text: str


def _fence_kind(info: str) -> str:
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
    return ContextKind.MARKDOWN_CODE.value


# Unfenced patterns in precedence order: the most structurally distinctive first,
# so a diff inside what also looks like source code is classified as a diff.
_UNFENCED: tuple[tuple[re.Pattern[str], str], ...] = (
    (_APPLY_PATCH, ContextKind.PATCH.value),
    (_DIFF_BLOCK, ContextKind.DIFF.value),
    (_JSON_BLOCK, ContextKind.JSON_STRING.value),
    (_YAML_BLOCK, ContextKind.YAML_SCALAR.value),
    (_SHELL_BLOCK, ContextKind.SHELL.value),
    (_SOURCE_BLOCK, ContextKind.SOURCE_CODE.value),
    # Single-line forms last: a multi-line block that already matched above keeps
    # its (more specific) classification.
    (_SQL_STATEMENT, ContextKind.SOURCE_CODE.value),
    (_CODE_STATEMENT, ContextKind.SOURCE_CODE.value),
    (_SHELL_COMMAND, ContextKind.SHELL.value),
)


def _resolve_claims(claims: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Keep non-overlapping claims by a single sorted sweep (linear, not all-pairs).

    Claims are added in precedence order, so a stable sort on (start, index) lets
    the earlier-added claim win any overlap.
    """
    ordered = sorted(range(len(claims)), key=lambda i: (claims[i][0], i))
    kept: list[tuple[int, int, str]] = []
    highest_end = -1
    for index in ordered:
        start, end, kind = claims[index]
        if start >= highest_end:          # disjoint from everything kept so far
            kept.append((start, end, kind))
            highest_end = end
    return kept


def segment(
    text: str,
    *,
    default_kind: str = ContextKind.PROSE.value,
    max_segments: int = MAX_SEGMENTS,
) -> list[Segment]:
    """Split ``text`` into non-overlapping, gap-free typed segments.

    Adjacent prose runs are emitted as ONE segment, and the total is capped at
    ``max_segments``; past the cap the remaining text becomes a single trailing
    prose segment. Both exist so one request cannot fan out into unbounded
    detector invocations (ADR-0011).
    """
    if not text:
        return []

    claims: list[tuple[int, int, str]] = []
    for match in _FENCE.finditer(text):
        claims.append((match.start(), match.end(), _fence_kind(match.group("info"))))
    fenced = _resolve_claims(list(claims))

    # Binary search over the sorted fence starts. A scan-from-the-front is O(n·m)
    # once there are many fences AND many candidates — the "linear" claim only
    # holds with an actual O(log n) lookup.
    fence_starts = [f_start for f_start, _, _ in fenced]

    def _outside_fence(start: int, end: int) -> bool:
        index = bisect_right(fence_starts, start) - 1
        if index >= 0 and start < fenced[index][1]:
            return False                     # begins inside a fence
        nxt = index + 1
        return not (nxt < len(fenced) and fenced[nxt][0] < end)   # overlaps the next

    def _over_ceiling() -> bool:
        # Checked DURING collection: finishing the scan first would do exactly the
        # work the ceiling exists to avoid.
        return len(claims) > MAX_CLAIMS

    for pattern, kind in _UNFENCED:
        for match in pattern.finditer(text):
            if match.end() > match.start() and _outside_fence(match.start(), match.end()):
                claims.append((match.start(), match.end(), kind))
                if _over_ceiling():
                    raise SegmentationLimitError(_LIMIT_MESSAGE)
    for match in _INLINE_CODE.finditer(text):
        if _outside_fence(match.start(), match.end()):
            claims.append((match.start(), match.end(),
                           ContextKind.MARKDOWN_INLINE_CODE.value))
            if _over_ceiling():
                raise SegmentationLimitError(_LIMIT_MESSAGE)

    kept = _resolve_claims(claims)

    out: list[Segment] = []
    cursor = 0
    for start, end, kind in kept:
        # Each iteration can emit up to TWO segments (a preceding prose gap and the
        # claim itself), so reserve room for both plus the trailing prose span.
        if len(out) + 2 > max_segments - 1:
            break
        if start > cursor:
            out.append(Segment(cursor, start, default_kind, text[cursor:start]))
        out.append(Segment(start, end, kind, text[start:end]))
        cursor = end
    if cursor < len(text):
        out.append(Segment(cursor, len(text), default_kind, text[cursor:]))
    return out


def coalesce_for_detection(segments: list[Segment]) -> list[Segment]:
    """Merge CONTIGUOUS same-kind segments.

    Note the limit, because it used to be overstated: this only merges spans that
    actually touch. In prose/code/prose/code alternation nothing is contiguous, so
    the count is unchanged — which is why the per-request detector budget in
    ``engine`` exists and is the thing that actually bounds the work. Merging is
    valid only for contiguous spans; anything else would corrupt offsets.
    """
    if not segments:
        return []
    merged: list[Segment] = [segments[0]]
    for seg in segments[1:]:
        last = merged[-1]
        if seg.kind == last.kind and seg.start == last.end:
            merged[-1] = Segment(last.start, seg.end, last.kind, last.text + seg.text)
        else:
            merged.append(seg)
    return merged


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
