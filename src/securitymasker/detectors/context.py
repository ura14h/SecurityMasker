"""Shared context-word scoring for Japanese recognizers (§14).

Japanese PII (phone, postal code, My Number, DOB) collides with ordinary numbers,
so a raw pattern match is only promoted to a confident detection when nearby text
contains supporting context words (§14.3, §14.5-7). This module provides a small
window-scan helper; each recognizer supplies its own vocabulary.
"""

from __future__ import annotations

_DEFAULT_WINDOW = 20


def has_context(
    text: str,
    start: int,
    end: int,
    keywords: tuple[str, ...],
    *,
    window: int = _DEFAULT_WINDOW,
) -> bool:
    """True if any keyword appears within ``window`` chars around ``[start, end)``."""
    lo = max(0, start - window)
    hi = min(len(text), end + window)
    around = text[lo:hi]
    return any(kw in around for kw in keywords)
