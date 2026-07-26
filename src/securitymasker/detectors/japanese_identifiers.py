"""日本の公的・業務identifier。

These numbers share a problem: their raw formats collide with ordinary numbers
(an order id, a build number, a customer reference). Detecting them on shape alone
would flood normal text with false positives and block legitimate requests. So each
recognizer here requires **a nearby context word** — the label that appears next to
the number in real documents and forms — and, where an official check digit exists,
that it validates.

Coverage and its limits are deliberate:

- 在留カード番号 has a published check digit, so it is verified.
- 旅券 (passport), 運転免許証, 基礎年金番号, 雇用保険被保険者番号, 健康保険記号番号
  and bank account numbers have a fixed shape but no public check digit we can
  verify, so they rely on format + context only. They will miss a number written
  with no surrounding label, and that limitation is documented rather than papered
  over with a bare-number regex.
- 法人番号 / 適格請求書番号 are PUBLIC registry data and live in their own
  opt-in detector (``japanese_corporate_number``), not here.

Test data must be synthetic: none of the fixtures are real numbers.
"""

from __future__ import annotations

import re

from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.context import has_context
from securitymasker.models import DetectionResult, EntityType, ReplacementProfile, RestorePolicy

_NUMERIC = ReplacementProfile.NUMERIC.value
_PROSE = ReplacementProfile.PROSE_IDENTIFIER.value
_LITERAL = RestorePolicy.LITERAL.value

# --- 在留カード番号 / 特別永住者証明書番号 -------------------------------------
# 2 letters + 8 digits + 2 letters, with a published modulus-11 check digit over
# the 8 digits (weights 7,6,5,4,3,2,7,6 -> the last digit is the check).
_RESIDENCE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2}\d{8}[A-Z]{2})(?![A-Za-z0-9])")
_RESIDENCE_CONTEXT = ("在留カード", "在留", "特別永住", "residence card", "在留資格")
_RESIDENCE_WEIGHTS = (7, 6, 5, 4, 3, 2, 7, 6)


def residence_check_digit(seven_digits: str) -> int:
    """在留card番号の先頭7桁に対するcheck digitを返す。"""
    total = sum(int(d) * w for d, w in zip(seven_digits, _RESIDENCE_WEIGHTS, strict=False))
    return total % 11


def is_valid_residence_card(value: str) -> bool:
    digits = value[2:10]
    if len(digits) != 8 or not digits.isdigit():
        return False
    return residence_check_digit(digits[:7]) == int(digits[7])


# --- other identifiers: format + required context ------------------------------
# (entity type, compiled pattern, context words, profile, score)
_SPECS: list[tuple[str, re.Pattern[str], tuple[str, ...], str, float]] = [
    (
        EntityType.JP_PASSPORT_NUMBER.value,
        re.compile(r"(?<![A-Za-z0-9])([A-Z]{2}\d{7})(?![A-Za-z0-9])"),
        ("旅券", "パスポート", "passport", "旅券番号"),
        _PROSE, 0.9,
    ),
    (
        EntityType.JP_DRIVER_LICENSE_NUMBER.value,
        re.compile(r"(?<!\d)(\d{12})(?!\d)"),
        ("運転免許", "免許証", "免許番号", "driver", "運転免許証番号"),
        _NUMERIC, 0.9,
    ),
    (
        EntityType.JP_PENSION_NUMBER.value,          # 基礎年金番号 4桁-6桁
        re.compile(r"(?<!\d)(\d{4}[- ]?\d{6})(?!\d)"),
        ("基礎年金", "年金番号", "年金手帳", "厚生年金", "国民年金"),
        _NUMERIC, 0.9,
    ),
    (
        EntityType.JP_EMPLOYMENT_INSURANCE_NUMBER.value,  # 雇用保険 4-6-1
        re.compile(r"(?<!\d)(\d{4}[- ]?\d{6}[- ]?\d)(?!\d)"),
        ("雇用保険", "被保険者番号", "ハローワーク", "失業保険"),
        _NUMERIC, 0.9,
    ),
    (
        EntityType.JP_HEALTH_INSURANCE_NUMBER.value,      # 記号・番号
        re.compile(r"(?<![\w])(\d{1,8}[- ]?\d{1,7})(?![\w])"),
        ("健康保険", "保険証", "被保険者証", "記号番号", "健保"),
        _NUMERIC, 0.85,
    ),
    (
        EntityType.JP_BANK_ACCOUNT.value,                 # 口座番号 7桁が一般的
        re.compile(r"(?<!\d)(\d{7})(?!\d)"),
        ("口座番号", "口座", "普通預金", "当座", "振込先", "支店"),
        _NUMERIC, 0.85,
    ),
]


class JapaneseIdentifierDetector:
    """context gate付きの日本の公的・業務番号recognizer。"""

    name = "jp_identifiers"

    def __init__(self, *, restore_policy: str = _LITERAL) -> None:
        self._policy = restore_policy

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        text = context.norm.normalized
        results: list[DetectionResult] = []

        for m in _RESIDENCE.finditer(text):
            value = m.group(1)
            if not is_valid_residence_card(value):
                continue  # wrong check digit -> not a residence card number
            if not has_context(text, m.start(1), m.end(1), _RESIDENCE_CONTEXT):
                continue
            results.append(self._hit(context, m, EntityType.JP_RESIDENCE_CARD.value,
                                     _PROSE, 0.95, 208))

        for entity_type, pattern, words, profile, score in _SPECS:
            for m in pattern.finditer(text):
                if not has_context(text, m.start(1), m.end(1), words):
                    continue  # bare number: refuse to guess for precision
                results.append(self._hit(context, m, entity_type, profile, score, 206))
        return results

    def _hit(
        self,
        context: DetectionContext,
        m: re.Match[str],
        entity_type: str,
        profile: str,
        score: float,
        priority: int,
    ) -> DetectionResult:
        o_start, o_end = context.norm.to_original_span(m.start(1), m.end(1))
        return DetectionResult(
            entity_type=entity_type,
            start=o_start,
            end=o_end,
            score=score,
            detector=self.name,
            context_kind=context.context_kind,
            replacement_profile=profile,
            restore_policy=self._policy,
            original_value=context.norm.original[o_start:o_end],
            normalized_value=m.group(1),
            metadata={"priority": priority},
        )
