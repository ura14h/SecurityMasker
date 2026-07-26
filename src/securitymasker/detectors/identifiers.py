"""Suppress PII matches that fall INSIDE a structural identifier.

A UUID is 32 hexadecimal digits, and roughly a third of them are decimal. Any
digit-shaped pattern will therefore eventually match part of one by chance —
which is exactly what happened in the real-CLI end-to-end test:

    019f9c69-1f5a-7803-9210-9274c7fa4f67
                    ^^^^^^^^^^^^^^^ read as the Japanese phone 03-9210-9274

Codex puts several UUIDs in every request (``session_id``, ``thread_id``,
``installation_id``, ``prompt_cache_key``, window and response ids). Measured over
3000 random UUIDv7 values, ~0.5% match PHONE, CREDIT_CARD or MY_NUMBER, so with
several identifiers per request and many requests per conversation, a session
being refused outright stops being unlikely and becomes a matter of time. The
failure is loud and total: the final leak gate blocks the request.

The rule is narrow on purpose:

- A detection **strictly inside** a UUID is discarded. Those digits are part of an
  opaque identifier; they are not a phone number.
- A detection that **covers the whole** UUID is kept. If an operator registers a
  UUID as a secret — an API key or a customer id often is one — that is a real
  finding, and suppressing it would be a leak.

So this only ever removes findings that are a proper substring of an identifier,
which is the one case where the digits provably belong to something else.
"""

from __future__ import annotations

import re

# 8-4-4-4-12 hex, the canonical form. Deliberately not a general "long hex run":
# a bare 16-digit token still has to reach the credit-card check.
_UUID = re.compile(
    r"(?<![0-9a-fA-F])"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"(?![0-9a-fA-F])"
)


def identifier_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges of the structural identifiers in ``text``."""
    return [(m.start(), m.end()) for m in _UUID.finditer(text)]


def inside_identifier(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    """Whether ``[start, end)`` is a PROPER substring of one of ``spans``.

    Equal-or-covering ranges return False, so a rule that matches the identifier
    itself still fires.
    """
    for span_start, span_end in spans:
        if start >= span_start and end <= span_end and (end - start) < (span_end - span_start):
            return True
    return False
