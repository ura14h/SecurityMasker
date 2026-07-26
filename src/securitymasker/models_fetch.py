"""model artifactのmanifest、取得、検証。

The runtime loads models ``local_files_only``, so nothing a user types can trigger
a download. Fetching is therefore a separate, deliberate step — and this is where
the artifacts are pinned and checked.

Pinning a revision stops the weights changing under us. It does not, on its own,
tell us that the files on disk are the ones we pinned: a partial download, a
corrupted cache, or a swapped file all leave a plausible-looking directory. So a
model is described by a **complete manifest** — every artifact we require, with
its expected size and SHA-256 — and verification fails closed when any required
artifact is missing, mismatched, or of a weight format we refuse to load.

Three rules follow from "a NER model is untrusted code-adjacent input":

- **safetensors only.** ``.bin``/``.pt``/``.pth`` are pickle formats that execute
  arbitrary code on load. They are rejected by name, and ``use_safetensors=True``
  is forced at load time so transformers cannot fall back to one.
- **known models only.** A model with no manifest cannot be verified, so it is
  refused by default; allowing one is an explicit, argued-for choice.
- **verify at load, not just at fetch.** A cache can change between the fetch and
  the next process start, so the runtime re-checks before trusting it.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from securitymasker.errors import ConfigError

# load時に任意codeを実行するweight形式。常に拒否する。
UNSAFE_WEIGHT_SUFFIXES = (".bin", ".pt", ".pth", ".ckpt", ".pkl")

ADOPTED_MODEL: Final = "tsmatz/xlm-roberta-ner-japanese"
ADOPTED_REVISION: Final = "aba094e118d5ffc622e9b25e07edc49f9dd85feb"


@dataclass(frozen=True)
class Artifact:
    name: str
    sha256: str
    size: int
    required: bool = True


@dataclass(frozen=True)
class ModelManifest:
    """固定した一model revisionに必要なartifactの完全な集合。"""

    model: str
    revision: str
    artifacts: tuple[Artifact, ...]
    license_id: str = ""
    license_url: str = ""
    base_model: str = ""
    base_model_license_id: str = ""
    base_model_license_url: str = ""
    training_dataset: str = ""
    training_dataset_license_id: str = ""
    training_dataset_license_url: str = ""

    @property
    def key(self) -> str:
        return f"{self.model}@{self.revision}"

    @property
    def required_names(self) -> set[str]:
        return {a.name for a in self.artifacts if a.required}

    def expected(self, name: str) -> Artifact | None:
        return next((a for a in self.artifacts if a.name == name), None)


# 固定値を公開するmodelのmanifest。配布byteからdigestを取得する。
# the Hugging Face registry API at adoption time and are reproduced in the ADR.
MANIFESTS: dict[str, ModelManifest] = {
    f"{ADOPTED_MODEL}@{ADOPTED_REVISION}":
        ModelManifest(
            model=ADOPTED_MODEL,
            revision=ADOPTED_REVISION,
            # weightだけでなくtransformersが読む全fileを固定する。
            # carries id2label — the label schema we validate against — and the
            # architecture; the tokenizer files decide how text is split. Altering
            # any of them changes what gets detected, so leaving them unpinned made
            # "complete manifest" untrue.
            artifacts=(
                Artifact("model.safetensors",
                         "a042d71446dd23e16dc2dbb1c7bf5b56b616dd8a53cdbb9af26597ba978b40be",
                         1109868164),
                Artifact("sentencepiece.bpe.model",
                         "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",
                         5069051),
                Artifact("tokenizer.json",
                         "62c24cdc13d4c9952d63718d6c9fa4c287974249e16b7ade6d5a85e7bbb75626",
                         17082660),
                # digestは末尾newlineを含む配布byteそのものから計算する。
                # newline included. The first version of these three entries was
                # hashed after stripping it, so every one of them rejected the real
                # snapshot — see the pinned-snapshot test in
                # tests/unit/test_model_supply_chain.py, which is why that test now
                # checks the actual cached files rather than only synthetic ones.
                Artifact("config.json",
                         "27f6a6b15fa4f289aebf113e7ff91c492b41df0d177115267e98ac53ae3a5c3d",
                         1029),
                Artifact("special_tokens_map.json",
                         "06e405a36dfe4b9604f484f6a1e619af1a7f7d09e34a8555eb0b77b66318067f",
                         280),
                Artifact("tokenizer_config.json",
                         "ae42ec38b1bce8cda1432566534c207e7bf573d4fd0178af8ae31ce8551d097c",
                         451),
            ),
            license_id="MIT",
            license_url=(
                "https://huggingface.co/tsmatz/xlm-roberta-ner-japanese/"
                "blob/aba094e118d5ffc622e9b25e07edc49f9dd85feb/README.md"
            ),
            base_model="FacebookAI/xlm-roberta-base",
            base_model_license_id="MIT",
            base_model_license_url=(
                "https://huggingface.co/FacebookAI/xlm-roberta-base"
            ),
            training_dataset="stockmarkteam/ner-wikipedia-dataset",
            training_dataset_license_id="CC-BY-SA-3.0",
            training_dataset_license_url=(
                "https://github.com/stockmarkteam/ner-wikipedia-dataset"
            ),
        ),
}


def manifest_for(model: str, revision: str | None) -> ModelManifest | None:
    return MANIFESTS.get(f"{model}@{revision}")


@dataclass
class VerificationResult:
    model: str
    revision: str
    verified: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)
    unsafe: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing or self.mismatched or self.unsafe)

    def failure_reason(self) -> str:
        parts = []
        if self.missing:
            parts.append(f"missing required artifacts {sorted(self.missing)}")
        if self.mismatched:
            parts.append(f"digest mismatch for {sorted(self.mismatched)}")
        if self.unsafe:
            parts.append(f"refused unsafe weight format {sorted(self.unsafe)}")
        return "; ".join(parts)


class UnknownModelError(ConfigError):
    """このmodel@revisionにはmanifestがなく検証できない。"""


class ArtifactVerificationError(ConfigError):
    """必須artifactの欠落・改変、または拒否形式を示す。"""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_directory(manifest: ModelManifest, directory: Path) -> VerificationResult:
    """``directory``を``manifest``と照合する。

    Iterates the MANIFEST (not the directory), so an artifact that is simply
    absent is caught — the previous implementation only inspected files that
    happened to exist and therefore passed on an incomplete download.
    """
    result = VerificationResult(model=manifest.model, revision=manifest.revision)

    for artifact in manifest.artifacts:
        path = directory / artifact.name
        if not path.is_file():
            if artifact.required:
                result.missing.append(artifact.name)
            continue
        if artifact.size and path.stat().st_size != artifact.size:
            result.mismatched.append(artifact.name)
            continue
        if file_sha256(path) == artifact.sha256:
            result.verified.append(artifact.name)
        else:
            result.mismatched.append(artifact.name)

    # safetensorsがあってもdirectory内のpickle形式は無条件で拒否する。
    # required safetensors are present: transformers must not be able to pick it.
    for path in directory.iterdir():
        if path.is_file() and path.suffix.lower() in UNSAFE_WEIGHT_SUFFIXES:
            result.unsafe.append(path.name)

    return result


def require_verified(
    model: str, revision: str | None, directory: Path, *, allow_unverified: bool = False
) -> VerificationResult:
    """検証し、失敗時は例外を送出する。fetchとruntime loadが共有するgate。"""
    manifest = manifest_for(model, revision)
    if manifest is None:
        if not allow_unverified:
            raise UnknownModelError(
                f"{model}@{revision} has no artifact manifest, so its files cannot be "
                "verified. Add a manifest or set "
                "ner.allow_unverified_model=true to accept it UNVERIFIED."
            )
        return VerificationResult(model=model, revision=revision or "", verified=[])

    result = verify_directory(manifest, directory)
    if not result.ok:
        raise ArtifactVerificationError(
            f"{manifest.key} failed verification: {result.failure_reason()}"
        )
    return result


def cache_directory(model: str, revision: str) -> Path | None:
    """正確なrevisionが存在する場合、そのlocal cache位置を返す。"""
    # one-file binaryは検証済みmodelを一時展開先へ同梱する。利用者のcacheやnetworkを
    # 見る前に、そのbuild時に固定したdirectoryだけを返す。
    bundle_root = getattr(sys, "_MEIPASS", None) if getattr(sys, "frozen", False) else None
    if (
        bundle_root is not None
        and model == ADOPTED_MODEL
        and revision == ADOPTED_REVISION
    ):
        bundled = Path(bundle_root) / "securitymasker_model"
        return bundled if bundled.is_dir() else None
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        return None
    try:
        return Path(snapshot_download(repo_id=model, revision=revision,
                                      local_files_only=True))
    except Exception:  # noqa: BLE001 - not cached is a normal, expected outcome
        return None


def fetch(
    model: str, revision: str, *, allow_unverified: bool = False
) -> VerificationResult:
    """``revision``の``model``をdownloadし、manifestと照合する。

    Raises rather than warning: an unverifiable model is not something to note and
    carry on past.
    """
    if manifest_for(model, revision) is None and not allow_unverified:
        raise UnknownModelError(
            f"refusing to fetch {model}@{revision}: no artifact manifest on record. "
            "Add one or pass --allow-unverified to accept it UNVERIFIED."
        )
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise ConfigError(
            "fetching models needs the standard NER dependencies; run scripts/setup"
        ) from exc

    directory = Path(snapshot_download(
        repo_id=model,
        revision=revision,
        # load対象形式だけを取得する。`*.bin`だけのmodelは不完全downloadとして拒否される。
        # pickle artifact never lands in the cache to be picked up later.
        allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt"],
    ))
    return require_verified(model, revision, directory, allow_unverified=allow_unverified)
