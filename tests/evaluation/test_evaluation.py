"""評価corpusに対するprecision・recall・F1を検証する。

A detection matches a gold label when the entity type is equal and the spans
overlap (robust to composite-address boundary differences). Reports overall and
per-entity metrics and asserts baseline thresholds so regressions are caught.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import pytest

from securitymasker.config import (
    Defaults,
    EntityConfig,
    SecurityMaskerConfig,
    build_engine,
)
from securitymasker.engine import MaskingEngine
from securitymasker.models import ReplacementProfile, RestorePolicy
from tests.evaluation.corpus import ALL, DICTIONARY, Example


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def _build_eval_engine() -> MaskingEngine:
    entities = [
        EntityConfig(
            id=f"e{i}", type=etype, values=list(values),
            replacement_profile=ReplacementProfile.PROSE_IDENTIFIER.value,
            restore_policy=RestorePolicy.LITERAL.value,
        )
        for i, (etype, values) in enumerate(DICTIONARY.items())
    ]
    config = SecurityMaskerConfig(defaults=Defaults(), entities=entities)
    return build_engine(config)


async def _evaluate(engine: MaskingEngine, examples: list[Example]):
    overall = Counts()
    per_type: dict[str, Counts] = defaultdict(Counts)
    for ex in examples:
        dets = await engine.detect(ex.text, context_kind=ex.context)
        gold = [(etype, ex.text.find(s), ex.text.find(s) + len(s)) for etype, s in ex.gold]
        matched_det: set[int] = set()
        matched_gold: set[int] = set()
        for gi, (etype, gs, ge) in enumerate(gold):
            for di, d in enumerate(dets):
                if di in matched_det:
                    continue
                if d.entity_type == etype and d.start < ge and gs < d.end:
                    matched_gold.add(gi)
                    matched_det.add(di)
                    per_type[etype].tp += 1
                    break
        for gi, (etype, _s, _e) in enumerate(gold):
            if gi not in matched_gold:
                per_type[etype].fn += 1
        for di, d in enumerate(dets):
            if di not in matched_det:
                per_type[d.entity_type].fp += 1
        overall.tp += len(matched_gold)
        overall.fn += len(gold) - len(matched_gold)
        overall.fp += len(dets) - len(matched_det)
    return overall, per_type


@pytest.mark.asyncio
async def test_corpus_metrics_meet_baseline(capsys: pytest.CaptureFixture[str]) -> None:
    engine = _build_eval_engine()
    overall, per_type = await _evaluate(engine, ALL)

    with capsys.disabled():
        print(f"\nOverall  P={overall.precision:.2f} R={overall.recall:.2f} F1={overall.f1:.2f} "
              f"(tp={overall.tp} fp={overall.fp} fn={overall.fn})")
        for etype in sorted(per_type):
            c = per_type[etype]
            print(f"  {etype:16} P={c.precision:.2f} R={c.recall:.2f} F1={c.f1:.2f} "
                  f"(tp={c.tp} fp={c.fp} fn={c.fn})")

    # Recall matters most for not leaking secrets; precision matters for not
    # 誤検出はコードを壊すため、baselineで回帰を検出する。
    assert overall.recall >= 0.9, f"recall too low: {overall.recall}"
    assert overall.precision >= 0.85, f"precision too low: {overall.precision}"


@pytest.mark.asyncio
async def test_negatives_produce_no_detections() -> None:
    """negative exampleに対する誤検出がないことを確認する。"""
    engine = _build_eval_engine()
    from tests.evaluation.corpus import NEGATIVES

    for ex in NEGATIVES:
        dets = await engine.detect(ex.text, context_kind=ex.context)
        assert dets == [], f"false positive on negative example: {ex.text!r} -> {[d.entity_type for d in dets]}"
