"""原文へ戻るoffset mapを伴うUnicode正規化（§12、§14.4）。

Detection runs on the normalized text (default NFKC) so that full-width/half-width
and compatibility variants match a dictionary/regex; but replacement happens on the
ORIGINAL text so the surface form is preserved for restoration.

We normalize **per original code point** and record, for every normalized index,
which original index produced it. This trades away cross-boundary canonical
composition (rare, and never needed for span mapping) for an exact, monotonic
offset map. ``to_original_span`` always rounds outward to whole original code
points, so a detected span never partially covers an original character.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal

NormForm = Literal["nfkc", "nfc", "nfkd", "nfd"]
_PyForm = Literal["NFC", "NFD", "NFKC", "NFKD"]
_FORMS: dict[str, _PyForm] = {"nfkc": "NFKC", "nfc": "NFC", "nfkd": "NFKD", "nfd": "NFD"}


@dataclass(frozen=True)
class NormalizedText:
    original: str
    normalized: str
    # For normalized[i]: _src[i] = start index and _end[i] = end index (exclusive)
    # in ``original`` of the chunk that produced it. A chunk is a base code point
    # plus its following combining marks, so canonical composition across code
    # points (か + U+3099 -> が) is captured while spans still round out to whole
    # original chunks (doc/06 P0-7). Both tuples have length == len(normalized).
    _src: tuple[int, ...]
    _end: tuple[int, ...]

    def to_original_span(self, n_start: int, n_end: int) -> tuple[int, int]:
        """``normalized``の``[n_start, n_end)`` spanを``original``のspanへ戻す。"""
        if n_end <= n_start:
            at = self._src[n_start] if n_start < len(self._src) else len(self.original)
            return (at, at)
        return (self._src[n_start], self._end[n_end - 1])

    def original_slice(self, n_start: int, n_end: int) -> str:
        s, e = self.to_original_span(n_start, n_end)
        return self.original[s:e]


def normalize(text: str, form: NormForm = "nfkc") -> NormalizedText:
    py_form = _FORMS.get(form)
    if py_form is None:
        raise ValueError(f"unsupported normalization form: {form!r}")
    buf: list[str] = []
    src: list[int] = []
    end: list[int] = []
    n = len(text)
    i = 0
    while i < n:
        start = i
        i += 1
        # Absorb following combining marks so base+mark compose as a unit.
        while i < n and unicodedata.combining(text[i]) != 0:
            i += 1
        for c in unicodedata.normalize(py_form, text[start:i]):
            buf.append(c)
            src.append(start)
            end.append(i)
    return NormalizedText(
        original=text, normalized="".join(buf), _src=tuple(src), _end=tuple(end)
    )


def normalize_value(text: str, form: NormForm = "nfkc") -> str:
    """dictionary termなど単独値をoffset mapなしで正規化する。"""
    return unicodedata.normalize(_FORMS[form], text)
