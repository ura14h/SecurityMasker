"""Explicit, auditable model preparation (ADR-0009).

The runtime loads models ``local_files_only``, so it never downloads anything
while serving a request — user text can never trigger a call to the Hub. That
makes fetching a separate, deliberate step, which is what this module is.

It is also where the digests get checked. Pinning a revision stops the weights
changing under us; verifying the file digests additionally detects a corrupted or
substituted download. Both are recorded in ADR-0009 and in the config.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from securitymasker.errors import ConfigError

# Digests for the models we publish pins for. Recorded in ADR-0009; a model not
# listed here can still be fetched, but its files are not digest-verified and the
# caller is told so rather than being left to assume they were.
KNOWN_DIGESTS: dict[str, dict[str, str]] = {
    "tsmatz/xlm-roberta-ner-japanese@aba094e118d5ffc622e9b25e07edc49f9dd85feb": {
        "model.safetensors":
            "a042d71446dd23e16dc2dbb1c7bf5b56b616dd8a53cdbb9af26597ba978b40be",
        "sentencepiece.bpe.model":
            "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",
        "tokenizer.json":
            "62c24cdc13d4c9952d63718d6c9fa4c287974249e16b7ade6d5a85e7bbb75626",
    },
}


@dataclass
class FetchResult:
    model: str
    revision: str
    verified: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatched


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_digests(files: dict[str, Path], expected: dict[str, str]) -> FetchResult:
    """Compare downloaded files against the pinned digests."""
    result = FetchResult(model="", revision="")
    for name, path in files.items():
        want = expected.get(name)
        if want is None:
            result.unverified.append(name)
            continue
        if file_sha256(path) == want:
            result.verified.append(name)
        else:
            result.mismatched.append(name)
    return result


def fetch(model: str, revision: str) -> FetchResult:
    """Download ``model`` at ``revision`` into the local cache and verify digests.

    Raises ``ConfigError`` when a digest does not match: a mismatched artefact is
    not something to warn about and continue past.
    """
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise ConfigError(
            "fetching models needs the NER extra (pip install -e '.[ner]')"
        ) from exc

    # Only the file types we actually load. Notably excludes *.bin: we do not load
    # pickle weights (ADR-0009).
    local = Path(snapshot_download(
        repo_id=model,
        revision=revision,
        allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt"],
    ))

    expected = KNOWN_DIGESTS.get(f"{model}@{revision}", {})
    files = {p.name: p for p in local.iterdir() if p.is_file()}
    result = verify_digests(files, expected)
    result.model, result.revision = model, revision

    if result.mismatched:
        raise ConfigError(
            f"digest mismatch for {model}@{revision}: {sorted(result.mismatched)}. "
            "The downloaded artefact differs from the pinned one; refusing it."
        )
    return result
