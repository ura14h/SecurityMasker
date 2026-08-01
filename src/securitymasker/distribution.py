"""source／binary配布形態を、埋め込みbuild metadataから識別する。"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

BinaryProfile = Literal["lite", "full"]
BUILD_METADATA_NAME = "securitymasker_build.json"


@dataclass(frozen=True)
class DistributionInfo:
    distribution: Literal["source", "binary"]
    binary_profile: BinaryProfile | None = None

    def version_suffix(self) -> str:
        if self.distribution == "source":
            return "source"
        if self.binary_profile is None:
            return "binary unknown-profile"
        return f"binary {self.binary_profile}"


def distribution_info() -> DistributionInfo:
    """PyInstallerが埋め込んだprofileだけを信頼し、未知値を推測しない。"""
    if not getattr(sys, "frozen", False):
        return DistributionInfo(distribution="source")

    bundle_root = getattr(sys, "_MEIPASS", None)
    if not isinstance(bundle_root, str):
        return DistributionInfo(distribution="binary")
    metadata = Path(bundle_root) / BUILD_METADATA_NAME
    try:
        parsed = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return DistributionInfo(distribution="binary")
    profile = parsed.get("binary_profile") if isinstance(parsed, dict) else None
    if profile not in ("lite", "full"):
        return DistributionInfo(distribution="binary")
    return DistributionInfo(distribution="binary", binary_profile=profile)


def version_text(version: str) -> str:
    info = distribution_info()
    return f"securitymasker {version} ({info.version_suffix()})"
