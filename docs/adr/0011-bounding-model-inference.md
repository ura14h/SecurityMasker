# ADR-0011 — Bounding model inference without hiding text from it

- Status: Accepted
- Date: 2026-07-26
- Supersedes: the per-request "detector pass budget" introduced 2026-07-25
- Relates to: ADR-0009, ADR-0010, doc/06 §P1-5, §P1-7

## Context

Model-backed detection is expensive in a way the deterministic detectors are not.
Two independent pressures follow from that, and conflating them caused a
vulnerability.

**Pressure 1 — a request must not consume unbounded CPU.** Inference is
synchronous CPU-bound Python. It cannot be interrupted: `asyncio.wait_for` around
it ends *our wait*, not the work. A cancelled request leaves the worker running.

**Pressure 2 — mixed prose/code input is segmented.** Context segmentation
(§17) splits a body into typed spans so a fuzzy detector can skip code, where a
model that has learned "capitalised token = name" fires on identifiers. Running
every detector on every span means the number of model calls scales with how
finely the input happens to be chopped — and the input is attacker-controlled.

The first answer to pressure 2 was a per-request budget: full detector set for
the first N spans, deterministic detectors only after that. That is a leak. Text
past the budget is never seen by NER, and the result is indistinguishable from a
clean scan. Padding a request with inline-code spans and placing an unregistered
name after them defeated Japanese NER deterministically. Reproduced with 40
inline-code spans: zero model detections on the trailing name.

## Decision

**Bound the work by how much text there is, not by how it is divided.**

Deterministic detectors run on every span, individually, uncapped. They are
cheap, and a secret is a secret wherever it appears (invariant 8).

Model-backed detectors run **once per request**, over the fuzzy-eligible spans
joined together, with findings mapped back to absolute offsets. Code-like spans
are still excluded — that policy is unchanged. Cost is now a function of the
prose volume in the request, which no rearrangement of the same text can lower,
and which no rearrangement can use to hide anything.

A detector is selected for this pass by an explicit `fuzzy` marker, not by
`skip_code_contexts`. The latter is user-configurable and answers a different
question; reading it as "is a model" meant that turning it off silently changed
how the detector was scheduled.

**Window the input to what the model can actually read.** A transformer has a
hard input length (512 tokens). Exceeding it does not raise: the tokenizer warns
and the pipeline classifies the prefix, returning nothing for the rest. Text is
therefore cut into overlapping token-bounded windows and each is inferred
separately; the overlap prevents a name landing on a cut from being missed by
both halves, and duplicates are removed afterwards.

**Over the ceiling, refuse.** Past `defaults.max_fuzzy_chars` the request fails
closed. This is the one place a bound is allowed to change behaviour, and it must
be visible: scanning part of the text and reporting success is the failure this
ADR exists to remove.

**Bound concurrency, not duration.** Inference runs on a fixed thread pool with
an admission limit. Over the limit, requests are refused rather than queued
behind work nobody is waiting for. Abandoned work keeps its slot until it
finishes, because nothing can reclaim it.

## Consequences

- Model detections now see each span in the context of the surrounding prose
  rather than in isolation, which is closer to how the model was trained.
- A detection that straddles a join boundary is clipped to each span it covers
  and emitted once per span. Clipping can only mask more than the model asked
  for, never less.
- Very large prompts are refused rather than partially scanned. This is a real
  behaviour change and is documented as such.
- The per-request pass budget is gone. It bounded the wrong quantity.

## Alternatives considered

- **Keep the budget, fail closed past it.** Honest, and it was the auditor's
  minimum bar. Rejected as the primary answer because it makes a routine
  code-heavy prompt unusable while the joined pass handles it correctly for the
  same cost.
- **Run the model per span with no cap.** No blind spot, but cost scales with
  span count, which is attacker-controlled.
- **A timeout around inference.** Does not bound anything: the work continues
  after the wait ends.
