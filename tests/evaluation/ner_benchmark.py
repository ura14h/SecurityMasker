"""Measure a Japanese NER backend against the synthetic corpus (doc/06 §5.3).

Development tooling, not production code: nothing in ``src/`` imports it. Run it
directly to produce the numbers recorded in ADR-0009.

    python -m tests.evaluation.ner_benchmark presidio
    python -m tests.evaluation.ner_benchmark hf <model-id>

Metrics are reported per entity because the trade-offs differ: for a masking
proxy, missing a PERSON is a leak, while flagging an identifier inside code
corrupts the user's code. Those two failures are not interchangeable, so they are
never averaged into one number.
"""

from __future__ import annotations

import asyncio
import resource
import sys
import time
from dataclasses import dataclass

from securitymasker.detectors.base import DetectionContext
from securitymasker.models import EntityType
from securitymasker.normalization import normalize
from tests.evaluation.ner_corpus import (
    NEGATIVES_CODE,
    NEGATIVES_PROSE,
    POSITIVES,
    NerExample,
)

# Our EntityType values mapped back to the corpus's coarse labels.
_TO_COARSE = {
    EntityType.PERSON.value: "PERSON",
    EntityType.ORGANIZATION.value: "ORGANIZATION",
    EntityType.LOCATION.value: "LOCATION",
    EntityType.JP_ADDRESS.value: "LOCATION",
}


@dataclass
class Score:
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


@dataclass
class Report:
    backend: str
    per_entity: dict[str, Score]
    prose_false_positives: int
    code_false_positives: int
    load_seconds: float
    infer_seconds: float
    peak_rss_mb: float

    def render(self) -> str:
        lines = [f"backend: {self.backend}"]
        for entity in ("PERSON", "ORGANIZATION", "LOCATION"):
            s = self.per_entity.get(entity, Score())
            lines.append(
                f"  {entity:<13} P={s.precision:.2f} R={s.recall:.2f} F1={s.f1:.2f} "
                f"(tp={s.tp} fp={s.fp} fn={s.fn})"
            )
        lines += [
            f"  prose false positives : {self.prose_false_positives}"
            f" / {len(NEGATIVES_PROSE)} examples",
            f"  code  false positives : {self.code_false_positives}"
            f" / {len(NEGATIVES_CODE)} examples",
            f"  load time             : {self.load_seconds:.2f}s",
            f"  inference time        : {self.infer_seconds:.2f}s"
            f" ({len(POSITIVES) + len(NEGATIVES_PROSE) + len(NEGATIVES_CODE)} examples)",
            f"  peak RSS              : {self.peak_rss_mb:.0f} MB",
        ]
        return "\n".join(lines)


def _peak_rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux kilobytes.
    return usage / (1024 * 1024) if sys.platform == "darwin" else usage / 1024


async def _detect(detector, example: NerExample) -> list[tuple[str, str]]:
    ctx = DetectionContext(norm=normalize(example.text, "nfkc"),
                           context_kind=example.context)
    out = []
    for hit in await detector.detect(ctx):
        coarse = _TO_COARSE.get(hit.entity_type)
        if coarse:
            out.append((coarse, hit.original_value))
    return out


def _overlaps(found: str, gold: str) -> bool:
    """Credit a hit when it overlaps the gold surface.

    Boundary conventions differ between backends (honorifics, spacing), and for
    masking purposes any hit covering the name is a win — the whole span gets
    replaced either way.
    """
    return found in gold or gold in found


async def evaluate(detector, backend: str, load_seconds: float) -> Report:
    per_entity: dict[str, Score] = {k: Score() for k in ("PERSON", "ORGANIZATION", "LOCATION")}
    started = time.monotonic()

    for example in POSITIVES:
        found = await _detect(detector, example)
        unmatched = list(found)
        for entity, surface in example.gold:
            hit = next((f for f in unmatched if f[0] == entity and _overlaps(f[1], surface)), None)
            if hit:
                unmatched.remove(hit)
                per_entity[entity].tp += 1
            else:
                per_entity[entity].fn += 1
        for entity, _ in unmatched:
            per_entity[entity].fp += 1

    prose_fp = 0
    for example in NEGATIVES_PROSE:
        hits = await _detect(detector, example)
        prose_fp += len(hits)
        for entity, _ in hits:
            per_entity[entity].fp += 1

    code_fp = 0
    for example in NEGATIVES_CODE:
        hits = await _detect(detector, example)
        code_fp += len(hits)
        for entity, _ in hits:
            per_entity[entity].fp += 1

    return Report(backend, per_entity, prose_fp, code_fp,
                  load_seconds, time.monotonic() - started, _peak_rss_mb())


def build_presidio(skip_code: bool = False):
    from securitymasker.detectors.presidio import PresidioDetector

    started = time.monotonic()
    detector = PresidioDetector(min_score=0.4, skip_code_contexts=skip_code)
    return detector, time.monotonic() - started


def build_hf(model: str, skip_code: bool = False):
    from securitymasker.detectors.japanese_ner import JapaneseNerDetector

    started = time.monotonic()
    detector = JapaneseNerDetector(model=model, min_score=0.5,
                                   skip_code_contexts=skip_code, local_files_only=False)
    return detector, time.monotonic() - started


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("presidio", "hf"):
        print(__doc__)
        return 2
    # skip_code_contexts is disabled here on purpose: the benchmark must MEASURE
    # each backend's raw behaviour in code, not the policy that hides it.
    if argv[0] == "presidio":
        detector, load = build_presidio()
        name = "presidio+ja_core_news_md"
    else:
        if len(argv) < 2:
            print("usage: ner_benchmark hf <model-id>")
            return 2
        detector, load = build_hf(argv[1])
        name = f"hf:{argv[1]}"
    if not getattr(detector, "available", False):
        print(f"{name}: NOT AVAILABLE (dependency or model missing)")
        return 1
    print(asyncio.run(evaluate(detector, name, load)).render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
