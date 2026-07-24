"""JP_MY_NUMBER (マイナンバー / 個人番号) recognizer (§14.5).

12 digits with an official check digit. We normalize full-width digits (done
upstream by NFKC), allow space/hyphen grouping, verify the check digit, and only
emit when the checksum is valid — a wrong checksum is NOT detected (§14.5). Context
words raise the score; without them the score stays lower so a plain 12-digit
business id is less likely to be masked as a My Number.

Test data must use synthetic numbers with a computed valid check digit, never a
real person's number (§14.5, §30).
"""

from __future__ import annotations

import re

from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.context import has_context
from securitymasker.models import DetectionResult, EntityType, ReplacementProfile, RestorePolicy

# 12 digits, optionally grouped as 4-4-4 by space or hyphen.
_PATTERN = re.compile(r"(?<!\d)(\d{4}[ -]?\d{4}[ -]?\d{4})(?!\d)")

_CONTEXT = (
    "マイナンバー", "個人番号", "個人番号カード", "番号カード",
    "扶養控除", "源泉徴収", "税", "社会保障", "マイナ",
)


def is_valid_my_number(digits: str) -> bool:
    """Validate the 12-digit My Number check digit (official algorithm)."""
    if len(digits) != 12 or not digits.isdigit():
        return False
    d = [int(c) for c in digits]
    body, check = d[:11], d[11]
    # n counts from the rightmost body digit (n=1 -> d11). P_n = n+1 (1..6), n-5 (7..11).
    total = 0
    for n in range(1, 12):
        q = body[11 - n]
        p = (n + 1) if n <= 6 else (n - 5)
        total += p * q
    r = total % 11
    expected = 0 if r <= 1 else 11 - r
    return expected == check


def check_digit(body11: str) -> int:
    """Compute the check digit for an 11-digit body (helper for synthetic test data)."""
    d = [int(c) for c in body11]
    total = sum(((n + 1) if n <= 6 else (n - 5)) * d[11 - n] for n in range(1, 12))
    r = total % 11
    return 0 if r <= 1 else 11 - r


class JapaneseMyNumberDetector:
    name = "jp_my_number"

    def __init__(self, *, restore_policy: str = RestorePolicy.BLOCK.value) -> None:
        # Default block: My Number is high-sensitivity (§10 policy default).
        self._restore_policy = restore_policy

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        text = context.norm.normalized
        results: list[DetectionResult] = []
        for m in _PATTERN.finditer(text):
            digits = re.sub(r"[ -]", "", m.group(1))
            if not is_valid_my_number(digits):
                continue  # wrong checksum -> not detected (§14.5)
            score = 0.95 if has_context(text, m.start(), m.end(), _CONTEXT) else 0.5
            o_start, o_end = context.norm.to_original_span(m.start(1), m.end(1))
            results.append(
                DetectionResult(
                    entity_type=EntityType.JP_MY_NUMBER.value,
                    start=o_start,
                    end=o_end,
                    score=score,
                    detector=self.name,
                    context_kind=context.context_kind,
                    replacement_profile=ReplacementProfile.NUMERIC.value,
                    restore_policy=self._restore_policy,
                    original_value=context.norm.original[o_start:o_end],
                    normalized_value=digits,
                    metadata={"priority": 210},
                )
            )
        return results
