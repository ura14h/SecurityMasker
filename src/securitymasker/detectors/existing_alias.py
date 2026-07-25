"""Recognize SecurityMasker's own aliases so they are never re-masked (§11 step 1).

Makes masking idempotent: replaying conversation history that already contains
``SM_ORG_7F3A91`` must not turn it into ``SM_ORG_99AA12`` (§11). These hits are
reported as ``EXISTING_ALIAS`` and treated by ``policy`` as protected regions that
suppress any overlapping detection and are themselves left untouched.
"""

from __future__ import annotations

import re

from securitymasker.detectors.base import DetectionContext
from securitymasker.models import DetectionResult, EntityType, ReplacementProfile, RestorePolicy

_ALIAS_RX = re.compile(
    r"""(?x)
      SM_[A-Z]+_[0-9A-F]{6,}                                  # prose identifier
    | sm-host-[0-9a-f]{6,}\.example\.invalid                  # hostname
    | sm-user-[0-9a-f]{6,}@example\.invalid                   # email
    | \$\{SECURITYMASKER_SECRET_[0-9A-F]{6,}\}                # env reference
    """
)


def contains_alias_shape(text: str) -> bool:
    """True if ``text`` contains a token shaped like a SecurityMasker alias.

    Used by the gateway to refuse a request that carries prior-turn aliases when no
    stable session could be resolved — restoring them would be impossible and a
    silent new session would corrupt the turn (doc/06 P1-1).
    """
    return _ALIAS_RX.search(text) is not None


class ExistingAliasDetector:
    name = "existing_alias"

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        text = context.norm.normalized
        results: list[DetectionResult] = []
        for m in _ALIAS_RX.finditer(text):
            # Only protect aliases actually issued in THIS session (doc/06 P0-7);
            # an unissued alias-shaped token must fall through to real detectors.
            if m.group(0) not in context.issued_aliases:
                continue
            o_start, o_end = context.norm.to_original_span(m.start(), m.end())
            results.append(
                DetectionResult(
                    entity_type=EntityType.EXISTING_ALIAS.value,
                    start=o_start,
                    end=o_end,
                    score=1.0,
                    detector=self.name,
                    context_kind=context.context_kind,
                    replacement_profile=ReplacementProfile.PROSE_IDENTIFIER.value,
                    restore_policy=RestorePolicy.LITERAL.value,
                    original_value=context.norm.original[o_start:o_end],
                    normalized_value=m.group(0),
                    metadata={"priority": 300},
                )
            )
        return results
