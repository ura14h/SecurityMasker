# ADR-0009 — Japanese NER backend: comparison and adoption

- Status: Accepted
- Date: 2026-07-25
- Relates to: ADR-0004 (Presidio in-process), doc/06 §5.3

## Context

The dictionary catches registered names; everything unregistered — a new
customer, a supplier, a colleague not in the list — is invisible to it. Repeated
audits flagged this as the central Japanese-language product gap.

The obvious move was "add Hugging Face NER", but that pulls torch into the
product, so the instruction was to **measure rather than assume**. All numbers
below come from `tests/evaluation/ner_benchmark.py` over
`tests/evaluation/ner_corpus.py` — 17 positive examples (35 gold spans) and 18
negatives, all synthetic (§30), covering kanji/hiragana/katakana/romaji names,
spacing variants, honorifics, invented organisations, place names, ambiguous
words (さくら/葵/ひかり), and identifiers in code/shell/JSON/YAML/diff.

Metrics are reported per entity and code false positives are counted separately,
because the two failure modes are not interchangeable: a missed PERSON is a leak,
while a hit on an identifier inside a code fence corrupts the user's code.

## Measurements

Apple Silicon, CPU/MPS, warm model cache. `skip_code_contexts` disabled during
measurement so each backend's *raw* behaviour in code is visible rather than the
policy that hides it.

| backend | PERSON F1 | ORG F1 | LOC F1 | prose FP | code FP | load | infer (35 ex) | peak RSS |
|---|---|---|---|---|---|---|---|---|
| presidio + `ja_core_news_md` | 0.80 | **0.46** | 0.89 | 2 | **5** | 0.9 s | 0.25 s | 481 MB |
| `tsmatz/xlm-roberta-ner-japanese` @0.5 | 0.95 | 0.71 | 1.00 | 5 | 0 | 3.4 s | 0.58 s | 825 MB |
| **`tsmatz/xlm-roberta-ner-japanese` @0.7** | **1.00** | **1.00** | **1.00** | **0** | **0** | 3.4 s | 0.58 s | 825 MB |
| `tsmatz/…` @0.9 | 0.75 (R=0.60) | 1.00 | 1.00 | 0 | 0 | — | — | — |
| `Mizuiro-sakura/luke-japanese-base-finetuned-ner` | **refused** | — | — | — | — | 72 s | — | 1320 MB |
| `jurabi/bert-ner-japanese` | **not evaluated** | — | — | — | — | — | — | — |

Notes on the two rejected candidates:

- **LUKE** was refused by our own loader, and finding out *why* was the most
  valuable result of the exercise. Its tokenizer reports `start`/`end` as `None`,
  so detections cannot be mapped to character spans — there is nothing to
  replace. The original code crashed mid-request on this; it now fails at
  startup. Separately, its labels are Japanese (`人名`, `法人名`, `地名`), which
  the old mapping did not know: it produced **zero detections and looked like a
  clean run**. Under-detection that reads as success is precisely how a leak
  hides, and that discovery drove the schema validation described below.
- **jurabi/bert-ner-japanese** was excluded before evaluation on supply-chain
  grounds: it publishes `pytorch_model.bin` only (no safetensors), and our rules
  forbid loading pickle weights. Its CC-BY-SA-3.0 licence would also impose
  share-alike obligations we do not want to inherit.

## Decision

**Adopt `tsmatz/xlm-roberta-ner-japanese` as an optional, default-OFF extra**,
with Presidio retained as the zero-extra-dependency alternative.

Presidio is not merely worse on aggregate: its ORG precision (0.38 raw, F1 0.46)
means most organisation hits are wrong, and it fires on 5 of 10 code negatives.
The adopted model reaches perfect scores on this corpus at `min_score=0.7` with
zero false positives in either prose or code, and it is the only candidate that
was clean in code *before* the skip-code policy is applied.

Conditions of adoption, all enforced in code:

1. **Optional extra, default OFF.** `pip install -e '.[ner]'`; `ner.model` unset
   means the detector does not exist. Torch is never a runtime dependency.
2. **Pinned.** `ner.revision` is *required* whenever `ner.model` is set — an
   unpinned id silently follows `main`, so the weights doing the detecting could
   change between deploys. Adopted pin:
   - model: `tsmatz/xlm-roberta-ner-japanese`
   - revision: `aba094e118d5ffc622e9b25e07edc49f9dd85feb`
   - `model.safetensors` sha256 `a042d71446dd23e16dc2dbb1c7bf5b56b616dd8a53cdbb9af26597ba978b40be`
   - `sentencepiece.bpe.model` sha256 `cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865`
   - `tokenizer.json` sha256 `62c24cdc13d4c9952d63718d6c9fa4c287974249e16b7ade6d5a85e7bbb75626`
3. **Offline at request time.** `local_files_only=True` by default: the model is
   fetched by an explicit preparation step (`securitymasker models fetch`) or
   baked into the image, so **user text never reaches the Hub**.
4. **Safetensors only, no remote code.** `trust_remote_code` is never set.
5. **Label schema validated at load.** The model's `id2label` is checked against
   our mapping; a model with no mappable label is refused, and unmappable labels
   are recorded on the detector rather than dropped per-token.
6. **Offsets validated at load** with a synthetic probe, so a tokenizer that
   cannot report spans fails at startup instead of mid-request.
7. **Off the event loop.** Inference runs via `asyncio.to_thread`, so the
   engine's per-detector timeout genuinely bounds it and one request cannot stall
   every other connection.
8. **`min_score` defaults to 0.7**, the measured optimum. Below it prose false
   positives appear; above it PERSON recall collapses to 0.60.
9. **LOC is its own entity type.** `LOCATION` is now distinct from `JP_ADDRESS`:
   "Yokohama" is not somebody's home address, and conflating them would let a
   coarse NER hit inherit an address's sensitivity.

## Consequences

- Deployments that want unregistered-name coverage install one extra and pin one
  revision. Everyone else is unaffected: no torch, no model, no behaviour change.
- ~825 MB resident and ~3.4 s startup when enabled. Acceptable for a long-lived
  proxy; unacceptable to impose by default, hence the extra.
- The corpus is synthetic and written by the same author as the detectors, so
  **F1 = 1.00 at 0.7 does not mean 1.00 in production.** It means the model is
  clean on the failure shapes we could think of. Treat it as a regression
  baseline, not a performance claim.
- NER remains the least-trusted signal: lowest priority, loses every overlap to
  the dictionary and the deterministic detectors, and skipped in code contexts.
  **It is never the reason something is called safe** (invariant 9).

## Residual risk

- A model can be wrong in ways a synthetic corpus does not reveal, particularly
  for rare surnames and for organisations without a legal-form suffix.
- Pinning by revision + digest prevents silent substitution but not a
  compromise-at-source: we are trusting the model publisher's training data. This
  is why NER only *widens* recall and never relaxes another control.
- The Hub is a third party. Fetching happens in an explicit, auditable step; if
  that is unacceptable, mirror the artefacts and point the cache at the mirror.
