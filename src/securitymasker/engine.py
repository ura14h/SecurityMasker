"""Masking engine — the non-streaming mask/unmask core (§18, §19).

Ties together normalization → detection → policy resolution → alias allocation →
structure-preserving replacement, and the inverse restoration. Protocol/streaming
concerns live elsewhere (Phase 2+); this operates on plain text and is the unit the
leakage tests target (§30.5).

Fail-closed: a ``block`` policy or a post-mask leak re-scan failure raises rather
than letting original data through (§26).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from securitymasker import policy
from securitymasker.aliases.factory import get_or_create_alias
from securitymasker.detectors.base import DetectionContext, SensitiveDataDetector
from securitymasker.errors import LeakageError, MaskingError
from securitymasker.models import DetectionResult, MaskingSession, RestorePolicy
from securitymasker.normalization import NormForm, normalize
from securitymasker.sessions.crypto import decrypt

REDACTION_MARK = "[REDACTED]"


@dataclass
class MaskResult:
    masked_text: str
    detections: list[DetectionResult] = field(default_factory=list)
    blocked: bool = False


def _apply_replacements(text: str, spans: list[tuple[int, int, str]]) -> str:
    """Rebuild ``text`` replacing each non-overlapping ``(start, end, repl)``."""
    spans = sorted(spans, key=lambda s: s[0])
    out: list[str] = []
    cursor = 0
    for start, end, repl in spans:
        if start < cursor:  # overlap guard (policy should prevent this)
            raise MaskingError("overlapping replacement spans")
        out.append(text[cursor:start])
        out.append(repl)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


class MaskingEngine:
    def __init__(
        self,
        detectors: list[SensitiveDataDetector],
        *,
        normalization: NormForm = "nfkc",
        merge_surface_forms: bool = False,
    ) -> None:
        self._detectors = detectors
        self._normalization = normalization
        self._merge_surface_forms = merge_surface_forms

    async def detect(self, text: str, *, context_kind: str = "prose") -> list[DetectionResult]:
        norm = normalize(text, self._normalization)
        ctx = DetectionContext(norm=norm, context_kind=context_kind)
        found: list[DetectionResult] = []
        for detector in self._detectors:
            found.extend(await detector.detect(ctx))
        return policy.resolve(found)

    async def mask_text(
        self,
        session: MaskingSession,
        text: str,
        *,
        context_kind: str = "prose",
        request_id: str | None = None,
    ) -> MaskResult:
        resolved = await self.detect(text, context_kind=context_kind)

        blocking = policy.blocking_entities(resolved)
        if blocking:
            raise MaskingError(
                "SecurityMasker blocked this request: entity type(s) "
                f"{sorted(set(blocking))} are policy-blocked."
            )

        spans: list[tuple[int, int, str]] = []
        for det in resolved:
            if det.restore_policy == RestorePolicy.REDACTED.value:
                spans.append((det.start, det.end, REDACTION_MARK))
                continue
            fp_value = det.normalized_value if self._merge_surface_forms else det.original_value
            mapping = get_or_create_alias(
                session,
                original_value=det.original_value,
                fingerprint_value=fp_value or det.original_value,
                entity_type=det.entity_type,
                replacement_profile=det.replacement_profile,
                restore_policy=det.restore_policy,
            )
            spans.append((det.start, det.end, mapping.alias))

        masked = _apply_replacements(text, spans)
        self._verify_no_leak(masked, resolved, request_id)
        return MaskResult(masked_text=masked, detections=resolved)

    def _verify_no_leak(
        self,
        masked: str,
        resolved: list[DetectionResult],
        request_id: str | None,
    ) -> None:
        """Pre-send re-scan (§18 step 11): no masked original may remain."""
        for det in resolved:
            if det.restore_policy == RestorePolicy.BLOCK.value:
                continue
            if det.original_value and det.original_value in masked:
                raise LeakageError(entity_type=det.entity_type, request_id=request_id)

    async def unmask_text(self, session: MaskingSession, text: str) -> str:
        """Restore only aliases created in THIS session with ``literal`` policy (§19).

        ``env_reference`` aliases stay as ``${...}`` (their real value is never
        returned); ``redacted`` values are irreversible.
        """
        restorable = {
            alias: m
            for alias, m in session.mappings_by_alias.items()
            if m.restore_policy == RestorePolicy.LITERAL.value
        }
        if not restorable:
            return text
        # Longest alias first so a shorter alias that prefixes a longer one can't
        # partially match (collision-lengthened tokens, §7).
        rx = re.compile("|".join(re.escape(a) for a in sorted(restorable, key=len, reverse=True)))

        def _sub(match: re.Match[str]) -> str:
            mapping = restorable[match.group(0)]
            return decrypt(
                session.aead_key,
                mapping.encrypted_original,
                aad=mapping.original_fingerprint.encode("ascii"),
            )

        return rx.sub(_sub, text)
