"""masking engine — 非streamingのmask／unmask core（§18、§19）。

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
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from securitymasker import policy
from securitymasker.aliases.factory import get_or_create_alias
from securitymasker.context import Segment, coalesce_for_detection, is_code_like, segment
from securitymasker.detectors.base import DetectionContext, SensitiveDataDetector
from securitymasker.detectors.identifiers import (
    identifier_spans,
    inside_identifier,
    is_guarded,
)
from securitymasker.errors import (
    DetectionError,
    DetectorTimeoutError,
    LeakageError,
    MaskingError,
)
from securitymasker.models import ContextKind, DetectionResult, MaskingSession, RestorePolicy
from securitymasker.normalization import NormForm, normalize, normalize_value
from securitymasker.sessions.crypto import decrypt
from securitymasker.tool_trust import ToolTrustPolicy

REDACTION_MARK = "[REDACTED]"

# ``fail_mode: open``でも障害時にskipできるのはbest-effortなfuzzy detectorだけ。
# a runtime error; every other detector (dictionary, secrets, regex, formats, My
# Number, and anything new) always fails closed so critical secrets never leak on
# a detector fault (doc/06 P0-6, §26).
_FAIL_OPEN_ELIGIBLE = frozenset({"jp_ner"})


def iter_strings(node: Any) -> Iterator[str]:
    """parse済みJSON構造の全string leafを列挙する。dict keyも含む。

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


def _apply_replacements(
    text: str,
    spans: list[tuple[int, int, str]],
    exact_replacements: dict[str, str] | None = None,
) -> str:
    """検出spanと、同じ原文の未検出な完全一致を置換する。

    Model detectorは長いtext内で同じ固有名詞の一部だけを返すことがある。検出済み原文が
    同じmask可能textに残ればleak guardが正しくblockするため、span間の未置換領域にある
    完全一致も同じaliasへ揃える。既に挿入したaliasへ再置換をかけないよう、各gapを原文の
    まま処理してからspanの置換値を連結する。
    """
    spans = sorted(spans, key=lambda s: s[0])
    replacements = {
        original: replacement
        for original, replacement in (exact_replacements or {}).items()
        if original
    }
    exact_pattern = (
        re.compile(
            "|".join(
                re.escape(original)
                for original in sorted(replacements, key=len, reverse=True)
            )
        )
        if replacements
        else None
    )

    def complete(fragment: str) -> str:
        if exact_pattern is None:
            return fragment
        return exact_pattern.sub(lambda match: replacements[match.group(0)], fragment)

    out: list[str] = []
    cursor = 0
    for start, end, repl in spans:
        if start < cursor:  # overlap guard (policy should prevent this)
            raise MaskingError("overlapping replacement spans")
        out.append(complete(text[cursor:start]))
        out.append(repl)
        cursor = end
    out.append(complete(text[cursor:]))
    return "".join(out)


# request全体のmodel pass一回分としてfuzzy対象spanを結合する。
# is a boundary no name or organisation crosses, so a detection that straddles one
# is an artefact of joining rather than a real entity.
_FUZZY_JOIN = "\n\n"


def _drop_inside_identifiers(
    found: list[DetectionResult], text: str
) -> list[DetectionResult]:
    """構造的identifierの真部分文字列になっているfindingを除く。"""
    if not found:
        return found
    spans = identifier_spans(text)
    if not spans:
        return found
    return [d for d in found
            if not (is_guarded(d.detector) and inside_identifier(spans, d.start, d.end))]


def _is_fuzzy(detector: SensitiveDataDetector) -> bool:
    """``detector``がmodel-backedでrequest全体にscheduleすべきかを返す。

    An explicit ``fuzzy`` attribute, not an inference from ``skip_code_contexts``:
    that flag is user-configurable and answers a different question (may this
    detector opt out of code spans?). Reading it as "is a model" meant turning it
    off silently promoted a model detector into the per-span deterministic pass.
    """
    return bool(getattr(detector, "fuzzy", False))


def _map_to_segments(
    det: DetectionResult, spans: Sequence[tuple[int, int, int]]
) -> list[DetectionResult]:
    """結合text上のdetectionを原文座標へ戻す。

    Normally a detection sits inside one span and this is a subtraction. A
    detection that overlaps the join is clipped to each span it covers and emitted
    once per span, so the covered characters are still masked. Clipping can only
    mask MORE than the model asked for, never less — the safe direction, and the
    reason this does not simply drop such detections.
    """
    out: list[DetectionResult] = []
    for j_start, j_end, o_start in spans:
        lo, hi = max(det.start, j_start), min(det.end, j_end)
        if lo >= hi:
            continue
        shift = o_start - j_start
        out.append(replace(det, start=lo + shift, end=hi + shift,
                           original_value=det.original_value[lo - det.start:hi - det.start]))
    return out


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
        max_fuzzy_chars: int = 200_000,
    ) -> None:
        # 一requestがmodel-backed detectorへ渡せるtext量の上限。
        # detectors. Over it the request is REFUSED — never partially scanned,
        # which the caller could not distinguish from a clean result. It bounds
        # span数ではなくtext量を制限する。旧span-pass budgetは検出回避を許した。
        # defeated by chopping a request into more pieces (ADR-0011).
        self._max_fuzzy_chars = max_fuzzy_chars
        self._detector_timeout = detector_timeout
        self._detectors = detectors
        self._normalization = normalization
        self._merge_surface_forms = merge_surface_forms
        self._fail_mode = fail_mode
        self.tool_trust = tool_trust if tool_trust is not None else ToolTrustPolicy()
        self.inject_alias_instruction = inject_alias_instruction
        # 最終payloadのblock-only guard入力（doc/06 P0-4）。
        # literals (pre-normalized) and the deterministic detectors.
        self._registered_literals = tuple(
            lit for lit in (normalize_value(v, normalization) for v in registered_literals) if lit
        )
        # 登録値の大文字小文字variantを逃さないためcase-fold済みcopyも保持する。
        # through the final guard in a field the adapter never masks (P0-4).
        self._registered_literals_ci = tuple(
            lit.casefold() for lit in self._registered_literals
        )
        self._leak_scanners = leak_scanners or []
        # header scanはbodyより狭いdetector集合を使う。
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
        """有効なpipeline。診断が二つ目を構築してmodelを二重loadせず検査できるよう公開する。"""
        return list(self._detectors)

    @staticmethod
    def _skips_context(detector: SensitiveDataDetector, context_kind: str) -> bool:
        """``detector``がこのcontextを対象外にするかを返す（§17、doc/06 P1-7）。

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
        """必要に応じてprose／code混在textをsegment化して検出する。

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
        """segmentationで情報を隠さずtyped span上を検出する。

        Two passes with different shapes, because the two kinds of detector have
        different costs and different rules:

        - **Deterministic** detectors run on EVERY span, individually. They are
          cheap, and a secret is a secret wherever it appears, so nothing may cap
          them (invariant 8).
        - **Fuzzy/model** detectors run ONCE, over the fuzzy-eligible spans joined
          together, then the findings are mapped back to absolute offsets.

        The previous version ran the full set on the first N spans and only the
        deterministic ones after that. That made segmentation into an attack: pad
        a request with inline-code spans and every unregistered name past the
        limit was invisible to NER, deterministically. Cost is now a function of
        how much prose the request contains, not how finely it is chopped, so
        there is no arrangement of the same text that scans less of it.
        """
        segments = coalesce_for_detection(segment(text))
        found: list[DetectionResult] = []

        for seg in segments:
            for det in await self._detect_one(seg.text, seg.kind, issued_aliases,
                                              deterministic_only=True):
                # 全後続処理が同じ座標系を使えるようspanを原文座標へ戻す。
                # downstream consumer (replacement, leak scan) sees absolute offsets.
                found.append(replace(det, start=det.start + seg.start,
                                     end=det.end + seg.start))

        found.extend(await self._detect_fuzzy(segments, issued_aliases))
        return policy.resolve(found)

    async def _detect_fuzzy(
        self, segments: Sequence[Segment], issued_aliases: frozenset[str]
    ) -> list[DetectionResult]:
        """各model-backed detectorを参照可能なspan全体へ一度だけ実行する。

        Whether a detector sees code-like spans is ITS setting, not this method's.
        Filtering code out here — before consulting `skip_code_contexts` — silently
        overrode operators who had turned it off to scan code too, and did so in
        the direction that scans less. So the detectors are grouped by that flag
        and each group gets its own joined pass: at most two, regardless of how
        many spans or detectors there are.
        """
        fuzzy = [d for d in self._detectors if _is_fuzzy(d)]
        if not fuzzy:
            # model-backed detectorが無効な既定構成では処理不要。
            # bound, so max_fuzzy_chars must not reject the request either.
            return []

        found: list[DetectionResult] = []
        for skips_code in (True, False):
            group = [d for d in fuzzy
                     if bool(getattr(d, "skip_code_contexts", False)) is skips_code]
            if not group:
                continue
            eligible = [seg for seg in segments
                        if not (skips_code and is_code_like(seg.kind))]
            found.extend(await self._detect_joined(eligible, group, issued_aliases))
        return found

    async def _detect_joined(
        self,
        segments: Sequence[Segment],
        detectors: Sequence[SensitiveDataDetector],
        issued_aliases: frozenset[str],
    ) -> list[DetectionResult]:
        """``segments``を一textとしてscanし、findingを実offsetへ戻す。

        Joining with a blank line — a boundary no name or organisation crosses —
        lets the model read each span in the context of its neighbours, and keeps
        the number of inference calls independent of how finely the request was
        segmented.
        """
        if not segments:
            return []

        joined_parts: list[str] = []
        # (start in joined text, end in joined text, start in original text)
        spans: list[tuple[int, int, int]] = []
        cursor = 0
        for seg in segments:
            spans.append((cursor, cursor + len(seg.text), seg.start))
            joined_parts.append(seg.text)
            cursor += len(seg.text) + len(_FUZZY_JOIN)
        joined = _FUZZY_JOIN.join(joined_parts)

        if len(joined) > self._max_fuzzy_chars:
            # prefixだけを黙ってscanせずfail-closedにする。
            # and it is indistinguishable from a clean result — the caller would
            # believe the text had been checked.
            raise DetectionError(
                f"request has {len(joined)} characters of scannable text, over the "
                f"{self._max_fuzzy_chars} limit for model-backed detection. Refusing "
                "rather than scanning only part of it."
            )

        raw = await self._detect_one(joined, ContextKind.PROSE.value, issued_aliases,
                                     only=detectors)
        return [mapped for det in raw for mapped in _map_to_segments(det, spans)]

    async def _detect_one(
        self, text: str, context_kind: str, issued_aliases: frozenset[str],
        *, deterministic_only: bool = False,
        only: Sequence[SensitiveDataDetector] | None = None,
    ) -> list[DetectionResult]:
        norm = normalize(text, self._normalization)
        ctx = DetectionContext(norm=norm, context_kind=context_kind, issued_aliases=issued_aliases)
        found: list[DetectionResult] = []
        for detector in (self._detectors if only is None else only):
            if self._skips_context(detector, context_kind):
                continue
            if deterministic_only and _is_fuzzy(detector):
                continue     # scanned once, request-wide, by _detect_fuzzy
            try:
                if self._detector_timeout > 0:
                    # detector単位の待ち時間を制限するが、実処理自体は停止しない。
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
                raise DetectorTimeoutError(
                    f"detector {name!r} exceeded its {self._detector_timeout}s budget"
                ) from exc
            except DetectionError:
                name = getattr(detector, "name", "")
                if self._fail_mode == "open" and name in _FAIL_OPEN_ELIGIBLE:
                    continue  # best-effort detector skipped; critical ones never are
                raise
        return policy.resolve(_drop_inside_identifiers(found, norm.normalized))

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
        exact_replacements: dict[str, str] = {}
        for det in resolved:
            if det.restore_policy == RestorePolicy.REDACTED.value:
                spans.append((det.start, det.end, REDACTION_MARK))
                exact_replacements.setdefault(det.original_value, REDACTION_MARK)
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
            exact_replacements.setdefault(det.original_value, mapping.alias)

        masked = _apply_replacements(text, spans, exact_replacements)
        self._verify_no_leak(
            masked,
            resolved,
            request_id,
            protected_replacements=frozenset(exact_replacements.values()),
        )
        return MaskResult(masked_text=masked, detections=resolved)

    def _verify_no_leak(
        self,
        masked: str,
        resolved: list[DetectionResult],
        request_id: str | None,
        protected_replacements: frozenset[str] = frozenset(),
    ) -> None:
        """送信前の再scan（§18 step 11）：マスク対象の原文を残さない。

        Deduplicate the originals first so a value repeated thousands of times in a
        large input is checked once, keeping this linear in the input size (§32).
        発行済みalias自体に短い原文が偶然含まれても漏えいではないため、置換値を除いた領域を
        検査する。原文側の未置換領域は``_apply_replacements``が完全一致補完済みである。
        """
        inspected = masked
        for replacement in sorted(protected_replacements, key=len, reverse=True):
            if replacement:
                inspected = inspected.replace(replacement, "")
        seen: dict[str, str] = {}
        for det in resolved:
            if det.restore_policy == RestorePolicy.BLOCK.value or not det.original_value:
                continue
            seen.setdefault(det.original_value, det.entity_type)
        for original, entity_type in seen.items():
            if original in inspected:
                raise LeakageError(entity_type=entity_type, request_id=request_id)

    async def assert_no_leak_in_payload(
        self,
        data: Any,
        *,
        session: MaskingSession | None = None,
        request_id: str | None = None,
    ) -> None:
        """マスク済みpayload全体に対する最終block-only leakage guard（doc/06 P0-4）。

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
        """認証以外のheaderに登録済みsecretがあればblockする（doc/06 P0-4）。

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
        # email形式aliasなど自前の置換値でscannerをself-triggerさせない。
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
            spans = identifier_spans(hay)
            for scanner in scanners:
                for hit in await scanner.detect(ctx):
                    if hit.original_value in issued or hit.normalized_value in issued:
                        continue  # one of this session's own aliases
                    if is_guarded(hit.detector) and inside_identifier(
                        spans, hit.start, hit.end
                    ):
                        # UUID内部の数字など、構造的identifier内の偶然の一致を除く。
                        # EVERY string, including the client's own session and
                        # thread ids, so without this a request is refused whenever
                        # a random identifier happens to look like a phone number.
                        continue
                    raise LeakageError(entity_type=hit.entity_type, request_id=request_id)

    def literal_restorations(self, session: MaskingSession) -> dict[str, str]:
        """このsessionの``literal`` aliasだけを対象にするalias→original map（§19）。

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
        """このsessionのliteral aliasを復元する同期``str→str``を返す。"""
        restorations = self.literal_restorations(session)
        if not restorations:
            return lambda text: text
        # 長いaliasから処理し、prefixとなる短いaliasの先取りを防ぐ。
        # partially match (collision-lengthened tokens, §7).
        rx = re.compile("|".join(re.escape(a) for a in sorted(restorations, key=len, reverse=True)))
        return lambda text: rx.sub(lambda m: restorations[m.group(0)], text)

    async def unmask_text(self, session: MaskingSession, text: str) -> str:
        """このsessionが``literal`` policyで作成したaliasだけを復元する（§19）。"""
        return self.make_restorer(session)(text)
