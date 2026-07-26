# ADR-0010 — Model supply chain: pinning, verification, and refused formats

- Status: Accepted
- Date: 2026-07-26
- Relates to: ADR-0009 (Japanese NER backend), doc/06 §P2-3

## Context

Adopting a Hugging Face NER model (ADR-0009) means a third-party binary artifact
becomes part of a security boundary. Two risks follow, and they are different:

1. **The wrong artifact loads.** A revision pin says which commit we asked for.
   It does not say the bytes on disk are that commit: a partial download, a
   corrupted cache, a cache poisoned between runs, or a manually "fixed" file all
   leave a directory that looks perfectly normal.
2. **Loading the artifact executes code.** `.bin`/`.pt`/`.pth`/`.ckpt`/`.pkl` are
   pickle formats. Loading one runs arbitrary code, in our process, with our
   credentials and our plaintext.

The masking proxy is exactly the wrong place to be relaxed about either.

## Decision

**Pin a complete manifest, not just a revision.** Every artifact the loader reads
is listed with its SHA-256 and its size. "Complete" means every file, not just
the weights — `config.json` carries `id2label`, so an unpinned config lets the
label schema be swapped, which changes *what gets detected* while every digest
check still passes. Tokenizer files decide how text is split, so they matter for
the same reason.

**Verify against the manifest, not against the directory.** Verification iterates
the manifest and reports a listed-but-absent artifact as missing. Walking the
directory instead only inspects files that happen to exist, so an incomplete
download passes.

**Digest the file, not a transformation of it.** Digests are of the distributed
bytes exactly, trailing newline included.

**Refuse pickle formats by name, and force `use_safetensors=True`.** Rejecting
only when safetensors are absent still lets transformers pick a pickle file that
is present; the presence of one is itself disqualifying.

**Refuse unknown models by default.** A model with no manifest cannot be
verified. Accepting one is possible but must be explicit
(`ner.allow_unverified_model=true`), and the result reports that nothing was
verified.

**Verify at load, not only at fetch.** A cache can change between the fetch and
the next process start, so the runtime re-checks before trusting it. Combined
with `local_files_only=True`, no user input can trigger a download at request
time.

**Never `trust_remote_code=True`.**

## Consequences

Adding or moving a model is deliberate work: fetch it, record six digests, write
them down. That is the intended cost.

A manifest can be wrong in two directions, and the second one bit us. Rejecting a
tampered model is what the tests were written for; **accepting the real one** was
never tested, because every test built its fixture by hashing files it had just
written. A manifest whose digests were taken after stripping the trailing newline
therefore rejected the genuine model, and passed the entire suite — fetching and
NER-enabled startup were both broken. `tests/unit/test_model_supply_chain.py` now
also verifies the pinned manifest against the real cached snapshot, skipping
(never passing) when the model is absent.

The generalisation: a check that only ever sees inputs it should reject is not
known to accept anything.

## Alternatives considered

- **Revision pin alone.** Cheapest, and the industry default. Rejected: it does
  not detect a modified cache, which is the realistic local threat.
- **Vendoring the weights into the repository.** 1.1 GB of binary in git, against
  an explicit instruction not to commit models.
- **Signature/provenance verification (sigstore, model signing).** Stronger than
  digests and the right eventual answer. Not implemented: it needs infrastructure
  the project does not yet have. Recorded as an open supply-chain gap in
  `docs/operations.md` rather than quietly skipped.
