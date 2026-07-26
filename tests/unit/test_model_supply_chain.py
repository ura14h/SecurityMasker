"""NER artifactのmanifestとdigest検証を確認する。

A pinned revision says which commit we asked for. It does NOT say the files on
disk are the ones we pinned: a partial download, a corrupted cache, or a swapped
file all leave a plausible-looking directory behind. These tests cover the gap —
missing artifacts, altered digests, pickle weights, and unknown models must all
fail closed, at fetch time AND at runtime load.

Most tests here check manifests against synthetic directories so the suite runs
anywhere. That is exactly what let a broken manifest ship: the three JSON entries
were hashed with their trailing newline stripped, so all three rejected the real
model, and no synthetic test could see it. The pinned-snapshot test below closes
that gap by verifying against the actual cached files, skipping (never passing)
when the model is not present.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from securitymasker.models_fetch import (
    ADOPTED_MODEL,
    ADOPTED_REVISION,
    MANIFESTS,
    UNSAFE_WEIGHT_SUFFIXES,
    Artifact,
    ArtifactVerificationError,
    ModelManifest,
    UnknownModelError,
    file_sha256,
    manifest_for,
    require_verified,
    verify_directory,
)

ADOPTED = ADOPTED_MODEL
ADOPTED_REV = ADOPTED_REVISION


def _manifest(tmp_path, contents: dict[str, bytes]) -> ModelManifest:
    """Write files and build a manifest that matches them exactly."""
    artifacts = []
    for name, data in contents.items():
        path = tmp_path / name
        path.write_bytes(data)
        artifacts.append(Artifact(name, file_sha256(path), len(data)))
    return ModelManifest("stub/model", "rev1", tuple(artifacts))


# --- the adopted model's manifest is complete ------------------------------------


def test_adopted_model_has_a_manifest() -> None:
    manifest = manifest_for(ADOPTED, ADOPTED_REV)
    assert manifest is not None
    names = manifest.required_names
    # Weights AND both tokenizer artifacts: loading needs all three, so a manifest
    # listing only the weights would pass on a half-downloaded cache.
    assert "model.safetensors" in names
    assert "tokenizer.json" in names
    assert "sentencepiece.bpe.model" in names


def test_adopted_model_manifest_records_provenance_and_licenses() -> None:
    manifest = manifest_for(ADOPTED, ADOPTED_REV)
    assert manifest is not None
    assert manifest.license_id == "MIT"
    assert manifest.base_model == "FacebookAI/xlm-roberta-base"
    assert manifest.base_model_license_id == "MIT"
    assert manifest.training_dataset == "stockmarkteam/ner-wikipedia-dataset"
    assert manifest.training_dataset_license_id == "CC-BY-SA-3.0"
    assert all(
        value.startswith("https://")
        for value in (
            manifest.license_url,
            manifest.base_model_license_url,
            manifest.training_dataset_license_url,
        )
    )


def test_no_manifest_lists_a_pickle_artifact() -> None:
    for manifest in MANIFESTS.values():
        for artifact in manifest.artifacts:
            assert not artifact.name.endswith(UNSAFE_WEIGHT_SUFFIXES), artifact.name


# --- verification outcomes ---------------------------------------------------------


def test_complete_directory_verifies(tmp_path) -> None:
    manifest = _manifest(tmp_path, {"model.safetensors": b"w", "tokenizer.json": b"t"})
    result = verify_directory(manifest, tmp_path)
    assert result.ok and sorted(result.verified) == ["model.safetensors", "tokenizer.json"]


def test_missing_required_artifact_fails(tmp_path) -> None:
    # The previous implementation iterated the DIRECTORY, so an absent artifact was
    # simply never checked and an incomplete download passed.
    manifest = _manifest(tmp_path, {"model.safetensors": b"w", "tokenizer.json": b"t"})
    (tmp_path / "tokenizer.json").unlink()
    result = verify_directory(manifest, tmp_path)
    assert not result.ok and "tokenizer.json" in result.missing


def test_altered_artifact_fails(tmp_path) -> None:
    manifest = _manifest(tmp_path, {"model.safetensors": b"w" * 16})
    (tmp_path / "model.safetensors").write_bytes(b"x" * 16)   # same size, new bytes
    result = verify_directory(manifest, tmp_path)
    assert not result.ok and "model.safetensors" in result.mismatched


def test_truncated_artifact_fails(tmp_path) -> None:
    manifest = _manifest(tmp_path, {"model.safetensors": b"w" * 32})
    (tmp_path / "model.safetensors").write_bytes(b"w" * 8)
    result = verify_directory(manifest, tmp_path)
    assert not result.ok and "model.safetensors" in result.mismatched


@pytest.mark.parametrize("suffix", [".bin", ".pt", ".pth", ".ckpt", ".pkl"])
def test_pickle_weights_are_refused_even_alongside_valid_safetensors(tmp_path, suffix) -> None:
    # Rejecting only when safetensors are ABSENT would still let transformers pick
    # the pickle file; the presence of one is itself disqualifying.
    manifest = _manifest(tmp_path, {"model.safetensors": b"w"})
    (tmp_path / f"pytorch_model{suffix}").write_bytes(b"pickled")
    result = verify_directory(manifest, tmp_path)
    assert not result.ok
    assert f"pytorch_model{suffix}" in result.unsafe


def test_failure_reason_names_the_artifacts_not_their_contents(tmp_path) -> None:
    manifest = _manifest(tmp_path, {"model.safetensors": b"secret-weights"})
    (tmp_path / "model.safetensors").write_bytes(b"different-value")
    reason = verify_directory(manifest, tmp_path).failure_reason()
    assert "model.safetensors" in reason
    assert "secret-weights" not in reason and "different-value" not in reason


# --- require_verified: the gate both fetch and load go through -----------------------


def test_unknown_model_is_refused_by_default(tmp_path) -> None:
    with pytest.raises(UnknownModelError):
        require_verified("someone/unpinned-model", "deadbeef", tmp_path)


def test_unknown_model_can_be_accepted_only_explicitly(tmp_path) -> None:
    result = require_verified("someone/unpinned-model", "deadbeef", tmp_path,
                              allow_unverified=True)
    assert result.verified == []        # nothing was verified, and it says so


def test_require_verified_raises_on_a_bad_directory(tmp_path, monkeypatch) -> None:
    manifest = _manifest(tmp_path, {"model.safetensors": b"w"})
    monkeypatch.setitem(MANIFESTS, "stub/model@rev1", manifest)
    (tmp_path / "model.safetensors").unlink()
    with pytest.raises(ArtifactVerificationError):
        require_verified("stub/model", "rev1", tmp_path)


def test_require_verified_passes_a_good_directory(tmp_path, monkeypatch) -> None:
    manifest = _manifest(tmp_path, {"model.safetensors": b"w"})
    monkeypatch.setitem(MANIFESTS, "stub/model@rev1", manifest)
    assert require_verified("stub/model", "rev1", tmp_path).ok


# --- runtime load re-verifies ---------------------------------------------------------


def test_detector_refuses_an_unverified_cache(tmp_path, monkeypatch) -> None:
    """A cache can change between fetch and the next start, so load re-checks."""
    pytest.importorskip("transformers")
    from securitymasker.detectors import japanese_ner as mod
    from securitymasker.errors import ConfigError

    manifest = _manifest(tmp_path, {"model.safetensors": b"w"})
    monkeypatch.setitem(MANIFESTS, "stub/model@rev1", manifest)
    (tmp_path / "model.safetensors").write_bytes(b"tampered")
    monkeypatch.setattr("securitymasker.models_fetch.cache_directory",
                        lambda m, r: tmp_path)
    with pytest.raises(ConfigError):
        mod.JapaneseNerDetector(model="stub/model", revision="rev1", required=True)


def test_detector_refuses_when_the_model_is_not_cached(monkeypatch) -> None:
    pytest.importorskip("transformers")
    from securitymasker.detectors import japanese_ner as mod
    from securitymasker.errors import ConfigError

    monkeypatch.setattr("securitymasker.models_fetch.cache_directory", lambda m, r: None)
    with pytest.raises(ConfigError) as exc:
        mod.JapaneseNerDetector(model=ADOPTED, revision=ADOPTED_REV, required=True)
    assert "models fetch" in str(exc.value)      # tells the operator what to do


def test_unverified_model_is_disabled_when_not_required(monkeypatch) -> None:
    pytest.importorskip("transformers")
    from securitymasker.detectors import japanese_ner as mod

    monkeypatch.setattr("securitymasker.models_fetch.cache_directory", lambda m, r: None)
    detector = mod.JapaneseNerDetector(model=ADOPTED, revision=ADOPTED_REV, required=False)
    assert detector.available is False


# --- configuration --------------------------------------------------------------------


def test_allow_unverified_model_defaults_to_false() -> None:
    from securitymasker.config import SecurityMaskerConfig

    config = SecurityMaskerConfig.model_validate({"version": 1})
    assert config.ner.allow_unverified_model is False


# --- the pinned manifest must accept the REAL model ---------------------------------


def _require_model() -> bool:
    """Whether a missing model is a FAILURE rather than a skip.

    Skipping is right on a developer laptop that has never fetched the model. It
    is wrong at a release gate: the whole point of these two tests is that the
    manifest accepts the real artifact, so a release that skips them has verified
    nothing about the model it ships. Set SM_REQUIRE_MODEL=1 there.
    """
    return os.environ.get("SM_REQUIRE_MODEL") == "1"


def _missing(reason: str) -> None:
    if _require_model():
        pytest.fail(f"SM_REQUIRE_MODEL=1 but {reason}")
    pytest.skip(reason)


def _snapshot() -> Path | None:
    """The cached directory for the pinned revision, or None if not fetched."""
    from securitymasker import models_fetch

    cached = models_fetch.cache_directory(ADOPTED, ADOPTED_REV)
    if cached is not None:
        return cached
    # huggingface_hub may be absent; fall back to the default cache layout.
    guess = (Path.home() / ".cache/huggingface/hub"
             / f"models--{ADOPTED.replace('/', '--')}" / "snapshots" / ADOPTED_REV)
    return guess if guess.is_dir() else None


def test_pinned_manifest_verifies_the_real_snapshot() -> None:
    """The manifest must accept the exact bytes of the revision it pins.

    A manifest is only useful if it says yes to the real artifact and no to
    everything else. Testing only the "no" half with synthetic files let a
    manifest ship that said no to BOTH — every fetch and every NER-enabled start
    failed. This asserts the "yes" half against the distributed bytes.
    """
    directory = _snapshot()
    if directory is None:
        _missing(f"{ADOPTED}@{ADOPTED_REV} is not in the local cache "
                 "(run: securitymasker models fetch)")
        return

    manifest = manifest_for(ADOPTED, ADOPTED_REV)
    assert manifest is not None
    result = verify_directory(manifest, directory)
    assert result.ok, (
        "the pinned manifest rejects the model it pins: " + result.failure_reason()
    )
    assert set(result.verified) == manifest.required_names


def test_pinned_manifest_records_whole_file_bytes() -> None:
    """Sizes must be the file's real length — the newline-stripping bug's signature.

    A digest mismatch alone does not say WHY. An off-by-one size against the real
    file is the fingerprint of hashing transformed content instead of the file, so
    it is worth asserting separately and by name.
    """
    directory = _snapshot()
    if directory is None:
        _missing("the pinned model is not in the local cache")
        return

    manifest = manifest_for(ADOPTED, ADOPTED_REV)
    assert manifest is not None
    for artifact in manifest.artifacts:
        path = directory / artifact.name
        assert path.stat().st_size == artifact.size, (
            f"{artifact.name}: manifest says {artifact.size} bytes, file is "
            f"{path.stat().st_size} — the manifest was not built from the file itself"
        )
        assert file_sha256(path) == artifact.sha256, artifact.name


def test_frozen_runtime_uses_only_the_bundled_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from securitymasker import models_fetch

    bundled = tmp_path / "securitymasker_model"
    bundled.mkdir()
    monkeypatch.setattr(models_fetch.sys, "frozen", True, raising=False)
    monkeypatch.setattr(models_fetch.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert models_fetch.cache_directory(ADOPTED, ADOPTED_REV) == bundled
    assert models_fetch.cache_directory("other/model", ADOPTED_REV) is None
