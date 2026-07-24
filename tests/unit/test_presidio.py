"""Presidio + spaCy Japanese NER tests (§13, §14.1).

Skipped unless presidio-analyzer and the ja_core_news_md model are installed
(``pip install -e '.[presidio]' && python -m spacy download ja_core_news_md``).
Verifies real NER value: names/orgs NOT in the dictionary are detected, and NER is
skipped in code contexts (§17). Synthetic names only (§30).
"""

from __future__ import annotations

import pytest

from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.presidio import PresidioDetector
from securitymasker.models import EntityType
from securitymasker.normalization import normalize

_detector = PresidioDetector()
pytestmark = pytest.mark.skipif(
    not _detector.available,
    reason="presidio-analyzer + ja_core_news_md not installed",
)


def ctx(text: str, kind: str = "prose") -> DetectionContext:
    return DetectionContext(norm=normalize(text, "nfkc"), context_kind=kind)


@pytest.mark.asyncio
async def test_detects_unregistered_person_and_org() -> None:
    hits = await _detector.detect(ctx("契約者は佐藤花子さん、勤務先は未来創研株式会社です。"))
    types = {h.entity_type for h in hits}
    assert EntityType.PERSON.value in types
    assert EntityType.ORGANIZATION.value in types
    # Least-trusted signal -> lowest priority so the dictionary wins overlaps.
    assert all(h.metadata["priority"] == 80 for h in hits)


@pytest.mark.asyncio
async def test_ner_skipped_in_code_context() -> None:
    assert await _detector.detect(ctx("class Sato: pass  # 佐藤花子", kind="source_code")) == []


@pytest.mark.asyncio
async def test_spans_map_back_to_original_surface() -> None:
    hits = await _detector.detect(ctx("担当は佐藤花子です"))
    person = [h for h in hits if h.entity_type == EntityType.PERSON.value]
    assert person and person[0].original_value == "佐藤花子"
