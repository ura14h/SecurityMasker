"""JP phone number recognizer (§14.3).

Matches common Japanese formats (landline, mobile, freedial, +81, parenthesized,
extension) on the NFKC-normalized text. Context words (電話/TEL/携帯/連絡先/内線…)
raise the score so build numbers / customer ids in code are less likely to be
misdetected (§14.3).
"""

from __future__ import annotations

import re

from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.context import has_context
from securitymasker.models import DetectionResult, EntityType, ReplacementProfile, RestorePolicy

# Ordered longest/most-specific first; central policy resolves overlaps anyway.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\+81[ -]?\d{1,4}[ -]?\d{1,4}[ -]?\d{3,4}"),
    re.compile(r"0120[ -]?\d{3}[ -]?\d{3}"),                       # freedial
    re.compile(r"0\d{1,4}[-(（]\d{1,4}[-)）]\d{3,4}"),              # grouped landline
    re.compile(r"0[789]0[ -]?\d{4}[ -]?\d{4}"),                    # mobile
    re.compile(r"0\d{1,4}[ -]\d{1,4}[ -]\d{3,4}"),                 # generic hyphenated
)
_EXTENSION = re.compile(r"内線[ ]?\d{1,5}")

_CONTEXT = ("電話", "TEL", "Tel", "tel", "携帯", "連絡先", "内線", "phone", "mobile", "fax", "FAX")


class JapanesePhoneDetector:
    name = "jp_phone"

    def __init__(self, *, restore_policy: str = RestorePolicy.LITERAL.value) -> None:
        self._restore_policy = restore_policy

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        text = context.norm.normalized
        results: list[DetectionResult] = []
        seen: set[tuple[int, int]] = set()
        for pattern in (*_PATTERNS, _EXTENSION):
            for m in pattern.finditer(text):
                span = (m.start(), m.end())
                if span in seen:
                    continue
                seen.add(span)
                has_ctx = has_context(text, m.start(), m.end(), _CONTEXT)
                # Extension numbers are ambiguous without context.
                if pattern is _EXTENSION and not has_ctx:
                    continue
                # A digit run with no separator (e.g. a build id) needs phone context
                # to avoid false positives (§14.3).
                has_sep = any(c in m.group(0) for c in "- （）()+")
                if not has_sep and not has_ctx:
                    continue
                score = 0.9 if has_ctx else 0.55
                o_start, o_end = context.norm.to_original_span(m.start(), m.end())
                results.append(
                    DetectionResult(
                        entity_type=EntityType.PHONE.value,
                        start=o_start,
                        end=o_end,
                        score=score,
                        detector=self.name,
                        context_kind=context.context_kind,
                        replacement_profile=ReplacementProfile.NUMERIC.value,
                        restore_policy=self._restore_policy,
                        original_value=context.norm.original[o_start:o_end],
                        normalized_value=m.group(0),
                        metadata={"priority": 160},
                    )
                )
        return results
