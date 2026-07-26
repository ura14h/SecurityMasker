"""組み込みformat recognizer：email、IPv4、credit card。

High-precision, deterministic. Credit cards require a valid Luhn checksum and
default to ``block``. IPv4 octets are range-validated. These run on the
normalized text after full-width ＠ and digits are folded by NFKC.
"""

from __future__ import annotations

import re

from securitymasker.detectors.base import DetectionContext
from securitymasker.models import DetectionResult, EntityType, ReplacementProfile, RestorePolicy

# EAI／国際化address（RFC 6531）：local partとdomainの両方がnon-ASCIIになり得る。
# non-ASCII, e.g. 山田＠例え.jp. NFKC upstream folds the full-width ＠ and digits,
# so only the character classes need to admit non-ASCII letters.
#
# 日本語にはword spaceがないため、無制限local partは直前のproseを飲み込む。
# prose: 「連絡先は山田＠example.co.jp」 would match from 連. Two patterns solve it
# without losing recall:
#   1. STRICT — the local part excludes hiragana, which is what glues Japanese
#      prose together (は/の/を/が). This finds 山田 inside 連絡先は山田＠… .
#   2. BOUNDED — hiragana IS allowed, but only when the address starts at a real
#      delimiter (line start, space, or an opening bracket/colon), so a hiragana
#      local part like たろう@example.jp is still found when it stands alone.
# 両patternの結果をmergeしspanで重複除去する。
_PUNCT = r"\s@,;:<>()\[\]\\\"'、。，．！？「」『』（）"
_HIRAGANA = r"぀-ゟ"
_DOMAIN = rf"[^{_PUNCT}]+\.(?:[A-Za-z]{{2,}}|[^{_PUNCT}.]{{2,}})"
_EMAIL_STRICT = re.compile(rf"[^{_PUNCT}{_HIRAGANA}]+@{_DOMAIN}")
_EMAIL_BOUNDED = re.compile(
    rf"(?:^|(?<=[\s:：「『（【>=,]))([^{_PUNCT}]+@{_DOMAIN})", re.MULTILINE
)
_IPV4 = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d.])")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


def _luhn_ok(digits: str) -> bool:
    if not (13 <= len(digits) <= 19) or not digits.isdigit():
        return False
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


# RFC 5737 documentation rangeは例示用に予約され、定義上real endpointではない。
# not anyone's real address — and they are exactly what we mint IPv4 aliases from
# Masking them adds no protection while burning alias space, so they
# are excluded from detection.
_DOC_IPV4_PREFIXES = ("192.0.2.", "198.51.100.", "203.0.113.")


def _valid_ipv4(text: str) -> bool:
    parts = text.split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return False
    # A leading zero is not IPv4 in dotted-quad notation — inet_aton would read it
    # as octal, so tools disagree about what it even means. What it usually IS is a
    # four-part version number: Claude Code sends "cc_version=2.1.212.057" on every
    # request, which the final leak gate then refused as an address. Requiring
    # canonical octets costs no real address and drops a whole class of these.
    if any(len(p) > 1 and p[0] == "0" for p in parts):
        return False
    return not text.startswith(_DOC_IPV4_PREFIXES)


class FormatsDetector:
    name = "formats"

    def __init__(self, *, credit_card_policy: str = RestorePolicy.BLOCK.value) -> None:
        self._card_policy = credit_card_policy

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        text = context.norm.normalized
        out: list[DetectionResult] = []

        # より狭く正確なSTRICT matchを優先する。
        # bounded pattern only fills in addresses the strict one cannot see at all
        # (a hiragana-only local part), identified by ending at the same place.
        spans: list[tuple[int, int]] = [(m.start(), m.end()) for m in _EMAIL_STRICT.finditer(text)]
        strict_ends = {end for _, end in spans}
        spans += [
            (m.start(1), m.end(1))
            for m in _EMAIL_BOUNDED.finditer(text)
            if m.end(1) not in strict_ends
        ]
        for start, end in sorted(spans):
            out.append(self._result(context, start, end, EntityType.EMAIL.value,
                                     ReplacementProfile.EMAIL.value, RestorePolicy.LITERAL.value, 0.95, 180))

        for m in _IPV4.finditer(text):
            if _valid_ipv4(m.group(1)):
                out.append(self._result(context, m.start(1), m.end(1), EntityType.IP_ADDRESS.value,
                                        ReplacementProfile.IPV4.value, RestorePolicy.LITERAL.value, 0.8, 165))

        for m in _CARD.finditer(text):
            digits = re.sub(r"[ -]", "", m.group(0))
            if _luhn_ok(digits):
                out.append(self._result(context, m.start(), m.end(), EntityType.CREDIT_CARD.value,
                                        ReplacementProfile.NUMERIC.value, self._card_policy, 0.9, 205))
        return out

    def _result(
        self,
        context: DetectionContext,
        start: int,
        end: int,
        etype: str,
        profile: str,
        policy: str,
        score: float,
        priority: int,
    ) -> DetectionResult:
        o_start, o_end = context.norm.to_original_span(start, end)
        return DetectionResult(
            entity_type=etype, start=o_start, end=o_end, score=score, detector=self.name,
            context_kind=context.context_kind, replacement_profile=profile, restore_policy=policy,
            original_value=context.norm.original[o_start:o_end],
            normalized_value=context.norm.normalized[start:end], metadata={"priority": priority},
        )
