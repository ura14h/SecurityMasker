"""Masking engine — the non-streaming mask/unmask core (§18, §19).

Ties together normalization → detection → policy resolution → alias allocation →
structure-preserving replacement, and the inverse restoration. Protocol/streaming
concerns live elsewhere (Phase 2+); this operates on plain text and is the unit the
leakage tests target (§30.5).

Fail-closed: a ``block`` policy or a post-mask leak re-scan failure raises rather
than letting original data through (§26).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from typing import Any

from securitymasker import policy
from securitymasker.aliases.factory import get_or_create_alias
from securitymasker.context import coalesce_for_detection, is_code_like, segment
from securitymasker.detectors.base import DetectionContext, SensitiveDataDetector
from securitymasker.errors import DetectionError, LeakageError, MaskingError
from securitymasker.models import ContextKind, DetectionResult, MaskingSession, RestorePolicy
from securitymasker.normalization import NormForm, normalize, normalize_value
from securitymasker.sessions.crypto import decrypt
from securitymasker.tool_trust import ToolTrustPolicy

REDACTION_MARK = "[REDACTED]"

# In ``fail_mode: open`` only these fuzzy, best-effort detectors may be skipped on
# a runtime error; every other detector (dictionary, secrets, regex, formats, My
# Number, and anything new) always fails closed so critical secrets never leak on
# a detector fault (doc/06 P0-6, §26).
_FAIL_OPEN_ELIGIBLE = frozenset({"presidio", "jp_ner"})


def iter_strings(node: Any) -> Iterator[str]:
    """Yield every string leaf in a parsed JSON structure — dict keys included.

    Keys are yielded because a registered secret can be smuggled into a schema
    property name or other structural key, not only into a value (doc/06 P0-4).
    """
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                yield key
            yield from iter_strings(value)
    elif isinstance(node, list | tuple):
        for item in node:
            yield from iter_strings(item)


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
        registered_literals: tuple[str, ...] = (),
        leak_scanners: list[SensitiveDataDetector] | None = None,
        fail_mode: str = "closed",
        tool_trust: ToolTrustPolicy | None = None,
        inject_alias_instruction: bool = False,
        detector_timeout: float = 10.0,
        segment_contexts: bool = True,
    ) -> None:
        self._detector_timeout = detector_timeout
        self._detectors = detectors
        self._normalization = normalization
        self._merge_surface_forms = merge_surface_forms
        self._fail_mode = fail_mode
        self.tool_trust = tool_trust if tool_trust is not None else ToolTrustPolicy()
        self.inject_alias_instruction = inject_alias_instruction
        # Final-payload block-only guard inputs (doc/06 P0-4): registered secret
        # literals (pre-normalized) and the deterministic detectors.
        self._registered_literals = tuple(
            lit for lit in (normalize_value(v, normalization) for v in registered_literals) if lit
        )
        # Case-folded copies so a case variant of a registered value cannot slip
        # through the final guard in a field the adapter never masks (P0-4).
        self._registered_literals_ci = tuple(
            lit.casefold() for lit in self._registered_literals
        )
        self._leak_scanners = leak_scanners or []
        # Header scanning uses a NARROWER set than the body: headers legitimately
        # carry IPs and hostnames (Host, X-Forwarded-For), so scanning them with the
        # full PII set would block ordinary traffic. doc/06 P0-4 scopes headers to
        # "registered secrets" — registered literals plus the secret patterns.
        self._header_scanners = [
            d for d in self._leak_scanners
            if getattr(d, "name", "") in {"secret_patterns", "dictionary", "user_regex"}
        ]
        self._segment_contexts = segment_contexts

    @property
    def detectors(self) -> list[SensitiveDataDetector]:
        """The active pipeline. Exposed so diagnostics can inspect what was built
        instead of building a second copy (and loading the models twice)."""
        return list(self._detectors)

    @staticmethod
    def _skips_context(detector: SensitiveDataDetector, context_kind: str) -> bool:
        """Whether ``detector`` opts out of this context (§17, doc/06 P1-7).

        Only FUZZY detectors may opt out, and only in code-like contexts, where a
        model that has learned "capitalised token = name" fires on identifiers.
        The dictionary, secret patterns, and every deterministic recognizer run
        everywhere — a real secret pasted into a code fence is still a secret
        (invariant 8).
        """
        return bool(getattr(detector, "skip_code_contexts", False)) and is_code_like(
            context_kind
        )

    async def detect(
        self,
        text: str,
        *,
        context_kind: str = "prose",
        issued_aliases: frozenset[str] = frozenset(),
    ) -> list[DetectionResult]:
        """Detect over ``text``, segmenting mixed prose/code when asked to.

        For a prose body the text is split into typed spans first (§17), so the
        detector policy can differ inside a fenced block or a diff while the
        surrounding prose keeps full coverage. Callers that already know the exact
        kind (a tool argument, a JSON string) pass it and no segmentation happens.
        """
        if self._segment_contexts and context_kind == ContextKind.PROSE.value:
            return await self._detect_segmented(text, issued_aliases)
        return await self._detect_one(text, context_kind, issued_aliases)

    async def _detect_segmented(
        self, text: str, issued_aliases: frozenset[str]
    ) -> list[DetectionResult]:
        found: list[DetectionResult] = []
        # Coalesce adjacent same-kind spans first: without it a document
        # alternating prose and inline code runs the detectors (and any
        # model) once per gap (ADR-0011).
        for seg in coalesce_for_detection(segment(text)):
            for det in await self._detect_one(seg.text, seg.kind, issued_aliases):
                # Shift spans back into the ORIGINAL text's coordinates so every
                # downstream consumer (replacement, leak scan) sees absolute offsets.
                found.append(replace(det, start=det.start + seg.start,
                                     end=det.end + seg.start))
        return policy.resolve(found)

    async def _detect_one(
        self, text: str, context_kind: str, issued_aliases: frozenset[str]
    ) -> list[DetectionResult]:
        norm = normalize(text, self._normalization)
        ctx = DetectionContext(norm=norm, context_kind=context_kind, issued_aliases=issued_aliases)
        found: list[DetectionResult] = []
        for detector in self._detectors:
            if self._skips_context(detector, context_kind):
                continue
            try:
                if self._detector_timeout > 0:
                    # Bound how long any one detector may take. This does NOT stop
                    # a runaway detector — neither `re` nor CPU-bound model code
                    # can be interrupted — it stops us WAITING, so the request
                    # fails closed instead of hanging. Runaway work is contained
                    # separately: dangerous regexes are refused at config load, and
                    # model inference runs on a bounded pool (ADR-0011).
                    found.extend(
                        await asyncio.wait_for(
                            detector.detect(ctx), timeout=self._detector_timeout
                        )
                    )
                else:
                    found.extend(await detector.detect(ctx))
            except TimeoutError as exc:
                name = getattr(detector, "name", "detector")
                raise DetectionError(
                    f"detector {name!r} exceeded its {self._detector_timeout}s budget"
                ) from exc
            except DetectionError:
                name = getattr(detector, "name", "")
                if self._fail_mode == "open" and name in _FAIL_OPEN_ELIGIBLE:
                    continue  # best-effort detector skipped; critical ones never are
                raise
        return policy.resolve(found)

    async def mask_text(
        self,
        session: MaskingSession,
        text: str,
        *,
        context_kind: str = "prose",
        request_id: str | None = None,
    ) -> MaskResult:
        resolved = await self.detect(
            text,
            context_kind=context_kind,
            issued_aliases=frozenset(session.mappings_by_alias),
        )

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
        """Pre-send re-scan (§18 step 11): no masked original may remain.

        Deduplicate the originals first so a value repeated thousands of times in a
        large input is checked once, keeping this linear in the input size (§32).
        """
        seen: dict[str, str] = {}
        for det in resolved:
            if det.restore_policy == RestorePolicy.BLOCK.value or not det.original_value:
                continue
            seen.setdefault(det.original_value, det.entity_type)
        for original, entity_type in seen.items():
            if original in masked:
                raise LeakageError(entity_type=entity_type, request_id=request_id)

    async def assert_no_leak_in_payload(
        self,
        data: Any,
        *,
        session: MaskingSession | None = None,
        request_id: str | None = None,
    ) -> None:
        """Final block-only leakage guard over the WHOLE masked payload (doc/06 P0-4).

        The per-text ``_verify_no_leak`` only covers strings the protocol adapter
        routed through masking; it cannot see a registered secret smuggled into an
        unknown field, a structural field (``model``/``type``/``id``), image
        metadata, or a schema property name. This walks every string leaf and key
        of the parsed structure and BLOCKS (never mutates a structural field) if a
        registered literal or a high-precision secret survives. Scanning the parsed
        values — not the serialized bytes — means JSON escaping (``\\n``, ``\\"``,
        ``\\\\``) is already undone, so an escaped secret is caught the same way.
        """
        await self._assert_no_leak(data, self._leak_scanners, session, request_id)

    async def assert_no_leak_in_headers(
        self,
        headers: dict[str, str],
        *,
        session: MaskingSession | None = None,
        request_id: str | None = None,
    ) -> None:
        """Block if a *registered* secret appears in non-auth headers (doc/06 P0-4).

        Deliberately narrower than the body guard: headers legitimately carry IPs
        and hostnames, so only registered dictionary/regex values and secret
        patterns are grounds to block. Callers must exclude provider auth headers
        before calling — those are never scanned, logged, or stored (§25).
        """
        await self._assert_no_leak(headers, self._header_scanners, session, request_id)

    async def _assert_no_leak(
        self,
        data: Any,
        scanners: list[SensitiveDataDetector],
        session: MaskingSession | None,
        request_id: str | None,
    ) -> None:
        if not self._registered_literals and not scanners:
            return
        # Our own replacements must not self-trigger the scanners (an email-shaped
        # alias, a doc-range IPv4, a digit-preserving numeric alias ...).
        issued = frozenset(session.mappings_by_alias) if session is not None else frozenset()
        for text in iter_strings(data):
            norm = normalize(text, self._normalization)
            hay = norm.normalized
            hay_ci = hay.casefold()
            for literal, literal_ci in zip(
                self._registered_literals, self._registered_literals_ci, strict=True
            ):
                if literal in hay or literal_ci in hay_ci:
                    raise LeakageError(entity_type="registered", request_id=request_id)
            if not scanners:
                continue
            ctx = DetectionContext(norm=norm, request_id=request_id, issued_aliases=issued)
            for scanner in scanners:
                for hit in await scanner.detect(ctx):
                    if hit.original_value in issued or hit.normalized_value in issued:
                        continue  # one of this session's own aliases
                    raise LeakageError(entity_type=hit.entity_type, request_id=request_id)

    def literal_restorations(self, session: MaskingSession) -> dict[str, str]:
        """Alias→original map for THIS session's ``literal`` aliases only (§19).

        ``env_reference`` aliases stay as ``${...}`` (real value never returned);
        ``redacted`` values are irreversible. Decrypts each mapping once.
        """
        out: dict[str, str] = {}
        for alias, m in session.mappings_by_alias.items():
            if m.restore_policy != RestorePolicy.LITERAL.value:
                continue
            out[alias] = decrypt(
                session.aead_key, m.encrypted_original, aad=m.original_fingerprint.encode("ascii")
            )
        return out

    def make_restorer(self, session: MaskingSession) -> Callable[[str], str]:
        """Return a sync ``str→str`` that restores this session's literal aliases."""
        restorations = self.literal_restorations(session)
        if not restorations:
            return lambda text: text
        # Longest alias first so a shorter alias that prefixes a longer one can't
        # partially match (collision-lengthened tokens, §7).
        rx = re.compile("|".join(re.escape(a) for a in sorted(restorations, key=len, reverse=True)))
        return lambda text: rx.sub(lambda m: restorations[m.group(0)], text)

    async def unmask_text(self, session: MaskingSession, text: str) -> str:
        """Restore only aliases created in THIS session with ``literal`` policy (§19)."""
        return self.make_restorer(session)(text)
