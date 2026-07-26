"""生年月日（DATE_OF_BIRTH）recognizer。

Distinguishes a birth date from an ordinary date by context: a Japanese date is
only promoted to ``DATE_OF_BIRTH`` when 生年月日/誕生日/生まれ/DOB/年齢 appears nearby
Plain release/announcement dates are left alone.
"""

from __future__ import annotations

import re

from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.context import has_context
from securitymasker.models import DetectionResult, EntityType, ReplacementProfile, RestorePolicy

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\d{4}年\s?\d{1,2}月\s?\d{1,2}日"),
    re.compile(r"(?:明治|大正|昭和|平成|令和)\s?\d{1,2}年\s?\d{1,2}月\s?\d{1,2}日"),
    re.compile(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}"),
)
_CONTEXT = ("生年月日", "誕生日", "生まれ", "DOB", "dob", "年齢", "才", "歳")


class DateOfBirthDetector:
    name = "date_of_birth"

    def __init__(self, *, restore_policy: str = RestorePolicy.LITERAL.value) -> None:
        self._restore_policy = restore_policy

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        text = context.norm.normalized
        results: list[DetectionResult] = []
        seen: set[tuple[int, int]] = set()
        for pattern in _PATTERNS:
            for m in pattern.finditer(text):
                if (m.start(), m.end()) in seen:
                    continue
                # Only a birth date if birth context is present.
                if not has_context(text, m.start(), m.end(), _CONTEXT, window=12):
                    continue
                seen.add((m.start(), m.end()))
                o_start, o_end = context.norm.to_original_span(m.start(), m.end())
                results.append(
                    DetectionResult(
                        entity_type=EntityType.DATE_OF_BIRTH.value,
                        start=o_start,
                        end=o_end,
                        score=0.85,
                        detector=self.name,
                        context_kind=context.context_kind,
                        replacement_profile=ReplacementProfile.PROSE_IDENTIFIER.value,
                        restore_policy=self._restore_policy,
                        original_value=context.norm.original[o_start:o_end],
                        normalized_value=m.group(0),
                        metadata={"priority": 155},
                    )
                )
        return results
