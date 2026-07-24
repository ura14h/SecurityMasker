"""Unicode normalization with an offset map back to the original text (§12, §14.4).

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
    # src[i] = index in ``original`` of the code point that produced normalized[i].
    # Monotonic non-decreasing; length == len(normalized).
    _src: tuple[int, ...]

    def to_original_span(self, n_start: int, n_end: int) -> tuple[int, int]:
        """Map a ``[n_start, n_end)`` span in ``normalized`` to a span in ``original``."""
        if n_end <= n_start:
            at = self._src[n_start] if n_start < len(self._src) else len(self.original)
            return (at, at)
        orig_start = self._src[n_start]
        orig_end = self._src[n_end - 1] + 1
        return (orig_start, orig_end)

    def original_slice(self, n_start: int, n_end: int) -> str:
        s, e = self.to_original_span(n_start, n_end)
        return self.original[s:e]


def normalize(text: str, form: NormForm = "nfkc") -> NormalizedText:
    py_form = _FORMS.get(form)
    if py_form is None:
        raise ValueError(f"unsupported normalization form: {form!r}")
    buf: list[str] = []
    src: list[int] = []
    for i, ch in enumerate(text):
        nch = unicodedata.normalize(py_form, ch)
        for c in nch:
            buf.append(c)
            src.append(i)
    return NormalizedText(original=text, normalized="".join(buf), _src=tuple(src))


def normalize_value(text: str, form: NormForm = "nfkc") -> str:
    """Normalize a standalone value (no offset map needed), e.g. a dictionary term."""
    return unicodedata.normalize(_FORMS[form], text)
