"""複合的な日本語住所（JP_ADDRESS）recognizer（§14.2）。

A Japanese address is not reliably found by one signal, so this combines an
optional postal code, a prefecture, a locality run, and a 丁目/番地/号 tail (plus an
optional building/floor/room) into a single ``JP_ADDRESS`` span, so a partially
masked address can't be reconstructed from the remainder (§14.2). Heuristic and
bounded; the evaluation corpus (§31) measures precision/recall.
"""

from __future__ import annotations

import re

from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.context import has_context
from securitymasker.models import DetectionResult, EntityType, ReplacementProfile, RestorePolicy

_PREFECTURE = (
    r"(?:東京都|北海道|大阪府|京都府|"
    r"(?:青森|岩手|宮城|秋田|山形|福島|茨城|栃木|群馬|埼玉|千葉|神奈川|新潟|富山|"
    r"石川|福井|山梨|長野|岐阜|静岡|愛知|三重|滋賀|兵庫|奈良|和歌山|鳥取|島根|岡山|"
    r"広島|山口|徳島|香川|愛媛|高知|福岡|佐賀|長崎|熊本|大分|宮崎|鹿児島|沖縄)県)"
)

# postal? + prefecture + locality (no digits) + a 丁目/番地/号 block, plus an optional
# adjacent building/floor/room suffix.
_ADDRESS = re.compile(
    r"(?:〒?\s?\d{3}-\d{4}\s?)?"
    + _PREFECTURE
    + r"[^\s、。,「」（）()0-9]{1,20}?"
    + r"\d+(?:丁目|番地?|号|[-−])[0-9丁目番地号\-−]*"
    + r"(?:(?:ビル|マンション|アパート|ハイツ|荘|タワー)[^\s、。,「」（）()]{0,12})?"
)
_CONTEXT = ("住所", "所在地", "送付先", "宛先", "居住地")


class CompositeAddressDetector:
    name = "jp_address"

    def __init__(self, *, restore_policy: str = RestorePolicy.LITERAL.value) -> None:
        self._restore_policy = restore_policy

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        text = context.norm.normalized
        results: list[DetectionResult] = []
        for m in _ADDRESS.finditer(text):
            score = 0.9 if has_context(text, m.start(), m.end(), _CONTEXT, window=8) else 0.75
            o_start, o_end = context.norm.to_original_span(m.start(), m.end())
            results.append(
                DetectionResult(
                    entity_type=EntityType.JP_ADDRESS.value,
                    start=o_start,
                    end=o_end,
                    score=score,
                    detector=self.name,
                    context_kind=context.context_kind,
                    replacement_profile=ReplacementProfile.PROSE_IDENTIFIER.value,
                    restore_policy=self._restore_policy,
                    original_value=context.norm.original[o_start:o_end],
                    normalized_value=m.group(0),
                    metadata={"priority": 170},
                )
            )
        return results
