"""日本の郵便番号（JP_POSTAL_CODE）recognizer。

``NNN-NNNN`` collides with product/ticket numbers, so it is only confident with a
``〒`` marker, the word 郵便番号, or a following prefecture name.
"""

from __future__ import annotations

import re

from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.context import has_context
from securitymasker.models import DetectionResult, EntityType, ReplacementProfile, RestorePolicy

_PATTERN = re.compile(r"〒?\s?(\d{3}-\d{4})")

_PREFECTURES = (
    "北海道", "青森", "岩手", "宮城", "秋田", "山形", "福島", "茨城", "栃木", "群馬",
    "埼玉", "千葉", "東京", "神奈川", "新潟", "富山", "石川", "福井", "山梨", "長野",
    "岐阜", "静岡", "愛知", "三重", "滋賀", "京都", "大阪", "兵庫", "奈良", "和歌山",
    "鳥取", "島根", "岡山", "広島", "山口", "徳島", "香川", "愛媛", "高知", "福岡",
    "佐賀", "長崎", "熊本", "大分", "宮崎", "鹿児島", "沖縄",
)
_CONTEXT = ("〒", "郵便番号", "郵便")


class JapanesePostalCodeDetector:
    name = "jp_postal_code"

    def __init__(self, *, restore_policy: str = RestorePolicy.LITERAL.value) -> None:
        self._restore_policy = restore_policy

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        text = context.norm.normalized
        results: list[DetectionResult] = []
        for m in _PATTERN.finditer(text):
            has_marker = "〒" in m.group(0)
            ctx = has_marker or has_context(text, m.start(), m.end(), _CONTEXT)
            # A prefecture name shortly after also confirms a postal code.
            following = text[m.end(1) : m.end(1) + 12]
            ctx = ctx or any(p in following for p in _PREFECTURES)
            if not ctx:
                continue
            o_start, o_end = context.norm.to_original_span(m.start(1), m.end(1))
            results.append(
                DetectionResult(
                    entity_type=EntityType.JP_POSTAL_CODE.value,
                    start=o_start,
                    end=o_end,
                    score=0.85,
                    detector=self.name,
                    context_kind=context.context_kind,
                    replacement_profile=ReplacementProfile.NUMERIC.value,
                    restore_policy=self._restore_policy,
                    original_value=context.norm.original[o_start:o_end],
                    normalized_value=m.group(1),
                    metadata={"priority": 160},
                )
            )
        return results
