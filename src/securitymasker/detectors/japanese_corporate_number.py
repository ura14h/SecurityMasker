"""法人番号（JP corporate number）recognizer。

13 digits: a 12-digit base plus a leading check digit. Unlike My Number, the
corporate number is public, registered information, so masking is opt-in and its
default restore policy is ``literal`` (pseudonymize-but-restorable), not block.

To keep precision high on a bare 13-digit run, a detection requires BOTH a valid
check digit AND a nearby context word (or the ``T`` invoice-registration prefix).
Test data uses synthetic numbers with a computed valid check digit only (§30).
"""

from __future__ import annotations

import re

from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.context import has_context
from securitymasker.models import DetectionResult, EntityType, ReplacementProfile, RestorePolicy

# 13 digits, optionally with a leading `T` (適格請求書 registration number form).
_PATTERN = re.compile(r"(?<![0-9A-Za-z])T?(\d{13})(?![0-9])")

_CONTEXT = (
    "法人番号", "会社", "株式会社", "有限会社", "合同会社", "登記", "法人",
    "適格請求書", "インボイス", "登録番号", "事業者",
)


def corporate_check_digit(base12: str) -> int:
    """Check digit for the 12-digit base (official 法人番号 algorithm)."""
    d = [int(c) for c in base12]  # d[0] most significant of the 12-digit base
    # n = position from the right (1..12); Pn = 2 if n even else 1.
    total = sum((2 if n % 2 == 0 else 1) * d[12 - n] for n in range(1, 13))
    return 9 - (total % 9)


def is_valid_corporate_number(digits13: str) -> bool:
    if len(digits13) != 13 or not digits13.isdigit():
        return False
    return int(digits13[0]) == corporate_check_digit(digits13[1:])


class JapaneseCorporateNumberDetector:
    name = "jp_corporate_number"

    def __init__(self, *, restore_policy: str = RestorePolicy.LITERAL.value) -> None:
        self._restore_policy = restore_policy

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        text = context.norm.normalized
        results: list[DetectionResult] = []
        for m in _PATTERN.finditer(text):
            digits = m.group(1)
            has_t_prefix = m.group(0).startswith("T")
            if not is_valid_corporate_number(digits):
                continue
            if not has_t_prefix and not has_context(text, m.start(), m.end(), _CONTEXT):
                continue  # bare 13-digit without any signal -> skip (precision)
            o_start, o_end = context.norm.to_original_span(m.start(1), m.end(1))
            results.append(
                DetectionResult(
                    entity_type=EntityType.JP_CORPORATE_NUMBER.value,
                    start=o_start,
                    end=o_end,
                    score=0.9,
                    detector=self.name,
                    context_kind=context.context_kind,
                    replacement_profile=ReplacementProfile.NUMERIC.value,
                    restore_policy=self._restore_policy,
                    original_value=context.norm.original[o_start:o_end],
                    normalized_value=digits,
                    metadata={"priority": 205},
                )
            )
        return results
