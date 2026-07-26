"""ユーザー指定patternに対するregex safety lint（§32、doc/06 P1-5）。

Python's ``re`` is a backtracking engine, so a pattern like ``(a+)+$`` degrades
exponentially: a few dozen characters of input can occupy a core for minutes. The
input-size cap does not help, because catastrophic patterns blow up well below it.

``re`` also cannot be interrupted mid-match, so the only reliable defences are
(a) refusing dangerous patterns at config load and (b) bounding how long we wait
for a detector before failing closed. This module is (a); the timeout is (b), in
``engine``.

The check is deliberately a conservative *lint*, not a proof — deciding whether an
arbitrary regex is safe is undecidable in general. It rejects the classic
exponential shapes (a quantified group whose body is itself quantified or an
alternation with overlapping branches) and lets everything else through, so a
pattern that passes is not certified safe, merely free of the known blow-ups.
"""

from __future__ import annotations

import re

# A quantifier applied to a group that already contains an unbounded quantifier:
# (a+)+  (a*)*  (a+)*  (\d+)+  (?:x*)+ ... — the classic exponential shape.
_NESTED_QUANTIFIER = re.compile(
    r"""\(          # group open
        (?:\?:)?    # optionally non-capturing
        [^()]*      # body without nesting
        [+*]        # ... containing an unbounded quantifier
        [^()]*
        \)
        \s*
        [+*]        # ... and the group itself is unbounded-quantified
    """,
    re.VERBOSE,
)

# An alternation of single-character branches under an unbounded quantifier, where
# the branches overlap: (a|a)* , (\d|[0-9])+ — also exponential on failure.
_QUANTIFIED_ALTERNATION = re.compile(r"\((?:\?:)?[^()]*\|[^()]*\)\s*[+*]")

# Nested bounded repetition with large counts, e.g. (a{100}){100}.
_LARGE_BOUNDED = re.compile(r"\{\s*(\d{3,})\s*(?:,\s*(\d{3,})?\s*)?\}")


class UnsafeRegexError(ValueError):
    """ユーザーpatternが既知のcatastrophic backtracking形式に一致した。"""


def check_regex_safety(pattern: str, *, rule_id: str) -> None:
    """``pattern``が既知の計算量爆発形式なら``UnsafeRegexError``を送出する。

    The message names only the rule id and the reason — never the pattern, which
    routinely embeds the very secret it matches (§25).
    """
    if _NESTED_QUANTIFIER.search(pattern):
        raise UnsafeRegexError(
            f"pattern {rule_id!r} nests an unbounded quantifier inside another "
            "(e.g. '(a+)+'), which backtracks exponentially; rewrite it to be "
            "unambiguous or anchor it"
        )
    if _QUANTIFIED_ALTERNATION.search(pattern):
        raise UnsafeRegexError(
            f"pattern {rule_id!r} applies an unbounded quantifier to an alternation "
            "(e.g. '(a|b)*'); if the branches can match the same text this "
            "backtracks exponentially — use a character class instead"
        )
    for match in _LARGE_BOUNDED.finditer(pattern):
        counts = [int(g) for g in match.groups() if g]
        if counts and max(counts) > 1000:
            raise UnsafeRegexError(
                f"pattern {rule_id!r} repeats more than 1000 times; bound it lower"
            )
