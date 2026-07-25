"""User-defined exact-match dictionary detector (§11 step 2, §12).

Highest-trust detector. Each entry may list several surface forms (spacing/width
variants). Matching runs on normalized text; overlap/longest-match resolution is
done centrally in ``policy`` after all detectors run, so this detector simply
reports every occurrence.

MVP uses per-term substring scanning. For very large dictionaries this should be
replaced by an Aho–Corasick automaton (§11) behind the same interface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from securitymasker.detectors.base import DetectionContext
from securitymasker.models import DetectionResult
from securitymasker.normalization import NormForm, normalize_value


def _is_cjk(text: str) -> bool:
    """True if every character is CJK/kana — where intra-name spacing is common."""
    return all(
        "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" or "ｦ" <= ch <= "ﾟ"
        for ch in text
    )


@dataclass(frozen=True)
class DictionaryEntry:
    entity_type: str
    values: tuple[str, ...]
    replacement_profile: str
    restore_policy: str
    priority: int = 100
    score: float = 1.0
    case_sensitive: bool = True


class DictionaryDetector:
    name = "dictionary"

    def __init__(
        self,
        entries: list[DictionaryEntry],
        *,
        normalization: NormForm = "nfkc",
        flexible_spacing: bool = True,
    ) -> None:
        self._normalization = normalization
        self._flexible_spacing = flexible_spacing
        # Pre-normalize each surface form once. Sort longest-first per entry so a
        # single value's longer variants are found before shorter substrings.
        self._terms: list[tuple[str, DictionaryEntry]] = []
        for entry in entries:
            for value in entry.values:
                norm = normalize_value(value, normalization)
                if norm:
                    self._terms.append((norm, entry))
        self._terms.sort(key=lambda t: len(t[0]), reverse=True)
        # Japanese names and organisations are routinely written with a space
        # between the surname and given name — 山田太郎 / 山田 太郎 / 山田　太郎 —
        # and NFKC folds the ideographic space to an ASCII one. Rather than making
        # operators enumerate every spacing variant (and silently missing the ones
        # they forget), each space-free term also gets a pattern that tolerates an
        # optional single space between its characters (doc/06 §5.3).
        self._spaced: list[tuple[re.Pattern[str], DictionaryEntry]] = []
        if flexible_spacing:
            for term, entry in self._terms:
                if " " in term or len(term) < 2 or not _is_cjk(term):
                    continue
                # Space only — never a newline, which would match across lines.
                pattern = " ?".join(re.escape(ch) for ch in term)
                flags = 0 if entry.case_sensitive else re.IGNORECASE
                self._spaced.append((re.compile(pattern, flags), entry))

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        haystack = context.norm.normalized
        hay_cmp = haystack if self._all_case_sensitive() else haystack.casefold()
        results: list[DetectionResult] = []
        for term, entry in self._terms:
            needle = term if entry.case_sensitive else term.casefold()
            target = haystack if entry.case_sensitive else hay_cmp
            start = target.find(needle)
            while start != -1:
                n_end = start + len(needle)
                o_start, o_end = context.norm.to_original_span(start, n_end)
                results.append(
                    DetectionResult(
                        entity_type=entry.entity_type,
                        start=o_start,
                        end=o_end,
                        score=entry.score,
                        detector=self.name,
                        context_kind=context.context_kind,
                        replacement_profile=entry.replacement_profile,
                        restore_policy=entry.restore_policy,
                        original_value=context.norm.original[o_start:o_end],
                        normalized_value=term,
                        metadata={"priority": entry.priority},
                    )
                )
                start = target.find(needle, start + 1)

        # Spacing variants (山田 太郎 for a registered 山田太郎). Overlaps with the
        # exact hits above are resolved centrally in ``policy`` (§11).
        for pattern, entry in self._spaced:
            for m in pattern.finditer(haystack):
                if " " not in m.group(0):
                    continue  # identical to the exact hit above; don't duplicate
                o_start, o_end = context.norm.to_original_span(m.start(), m.end())
                results.append(
                    DetectionResult(
                        entity_type=entry.entity_type,
                        start=o_start,
                        end=o_end,
                        score=entry.score,
                        detector=self.name,
                        context_kind=context.context_kind,
                        replacement_profile=entry.replacement_profile,
                        restore_policy=entry.restore_policy,
                        original_value=context.norm.original[o_start:o_end],
                        normalized_value=m.group(0),
                        metadata={"priority": entry.priority},
                    )
                )
        return results

    def _all_case_sensitive(self) -> bool:
        return all(entry.case_sensitive for _, entry in self._terms)
