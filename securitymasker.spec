# -*- mode: python ; coding: utf-8 -*-
"""SecurityMasker Lite／Full one-file build specification（ADR-0020）。"""

import json
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

project = Path.cwd()
source = project / "src"
# Repository rootのlauncher ``securitymasker.py`` が同名packageを隠さないよう、
# PyInstallerの解析でもsrc-layoutを最優先にする。
sys.path.insert(0, str(source))

from securitymasker.models_fetch import (
    ADOPTED_MODEL,
    ADOPTED_REVISION,
    cache_directory,
    manifest_for,
    require_verified,
)

binary_profile = os.environ.get("SECURITYMASKER_BINARY_PROFILE")
if binary_profile not in ("lite", "full"):
    raise SystemExit("SECURITYMASKER_BINARY_PROFILE must be lite or full")
metadata_value = os.environ.get("SECURITYMASKER_BINARY_METADATA")
if not metadata_value:
    raise SystemExit("SECURITYMASKER_BINARY_METADATA is required")
metadata_path = Path(metadata_value)
metadata_path.write_text(
    json.dumps({"distribution": "binary", "binary_profile": binary_profile}) + "\n",
    encoding="utf-8",
)

datas = collect_data_files("securitymasker")
datas += [(str(metadata_path), ".")]
if binary_profile == "full":
    model_directory = cache_directory(ADOPTED_MODEL, ADOPTED_REVISION)
    if model_directory is None:
        raise SystemExit("pinned NER model is not cached; run model-load first")
    require_verified(ADOPTED_MODEL, ADOPTED_REVISION, model_directory)
    manifest = manifest_for(ADOPTED_MODEL, ADOPTED_REVISION)
    if manifest is None:
        raise SystemExit("pinned NER model manifest is missing")
    datas += [
        (str(model_directory / artifact.name), "securitymasker_model")
        for artifact in manifest.artifacts
    ]
datas += [
    (str(project / "LICENSE"), "."),
    (str(project / "THIRD_PARTY_NOTICES.md"), "."),
    (str(project / "docs/reference/model-licenses.md"), "docs/reference"),
]
for distribution in (
    "securitymasker",
    "starlette",
    "uvicorn",
    "websockets",
    "httpx",
    "pydantic",
    "cryptography",
    "PyYAML",
    "transformers",
    "torch",
    "sentencepiece",
):
    datas += copy_metadata(distribution)

hiddenimports = [
    "securitymasker.cli",
    "securitymasker.gateway.app",
    "securitymasker.detectors.japanese_ner",
    "securitymasker.sessions.sqlite",
    "sentencepiece",
    "safetensors.torch",
    "transformers.models.xlm_roberta.modeling_xlm_roberta",
    "transformers.models.xlm_roberta.tokenization_xlm_roberta_fast",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
]

excludes = [
    "devtools",
    "pytest",
    "presidio_analyzer",
    "spacy",
    "tensorflow",
    "jax",
    "flax",
]

analysis = Analysis(
    [str(source / "securitymasker/_binary_entry.py")],
    pathex=[str(source)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name=f"securitymasker-{binary_profile}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
