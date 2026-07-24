"""Built-in format recognizers: email, IPv4, credit card (§11 step 6).

High-precision, deterministic. Credit cards require a valid Luhn checksum and
default to ``block`` (§10). IPv4 octets are range-validated. These run on the
normalized text (full-width ＠/digits already folded by NFKC, §14.4).
"""

from __future__ import annotations

import re

from securitymasker.detectors.base import DetectionContext
from securitymasker.models import DetectionResult, EntityType, ReplacementProfile, RestorePolicy

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_IPV4 = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d.])")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


def _luhn_ok(digits: str) -> bool:
    if not (13 <= len(digits) <= 19) or not digits.isdigit():
        return False
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _valid_ipv4(text: str) -> bool:
    parts = text.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


class FormatsDetector:
    name = "formats"

    def __init__(self, *, credit_card_policy: str = RestorePolicy.BLOCK.value) -> None:
        self._card_policy = credit_card_policy

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        text = context.norm.normalized
        out: list[DetectionResult] = []

        for m in _EMAIL.finditer(text):
            out.append(self._result(context, m.start(), m.end(), EntityType.EMAIL.value,
                                     ReplacementProfile.EMAIL.value, RestorePolicy.LITERAL.value, 0.95, 180))

        for m in _IPV4.finditer(text):
            if _valid_ipv4(m.group(1)):
                out.append(self._result(context, m.start(1), m.end(1), EntityType.IP_ADDRESS.value,
                                        ReplacementProfile.IPV4.value, RestorePolicy.LITERAL.value, 0.8, 165))

        for m in _CARD.finditer(text):
            digits = re.sub(r"[ -]", "", m.group(0))
            if _luhn_ok(digits):
                out.append(self._result(context, m.start(), m.end(), EntityType.CREDIT_CARD.value,
                                        ReplacementProfile.NUMERIC.value, self._card_policy, 0.9, 205))
        return out

    def _result(
        self,
        context: DetectionContext,
        start: int,
        end: int,
        etype: str,
        profile: str,
        policy: str,
        score: float,
        priority: int,
    ) -> DetectionResult:
        o_start, o_end = context.norm.to_original_span(start, end)
        return DetectionResult(
            entity_type=etype, start=o_start, end=o_end, score=score, detector=self.name,
            context_kind=context.context_kind, replacement_profile=profile, restore_policy=policy,
            original_value=context.norm.original[o_start:o_end],
            normalized_value=context.norm.normalized[start:end], metadata={"priority": priority},
        )
