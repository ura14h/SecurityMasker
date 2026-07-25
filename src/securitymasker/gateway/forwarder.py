"""Transparent upstream forwarding via httpx (§5, §25).

Forwards the client's request (masked body) to the upstream, passing the client's
own auth and unknown headers through verbatim — the proxy never stores, decrypts,
or logs credentials (§25: ``Authorization``/``x-api-key`` are forwarded, never
logged). Streaming responses can be piped through a stateful processor that
restores aliases; non-streaming responses are buffered for dict restoration.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from typing import Any, Protocol

import httpx
from starlette.responses import StreamingResponse

# Hop-by-hop / recomputed headers not forwarded upstream or back downstream.
_REQ_STRIP = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding"}
_RESP_STRIP = {"content-length", "content-encoding", "transfer-encoding", "connection"}

_TIMEOUT = httpx.Timeout(connect=15.0, read=600.0, write=60.0, pool=15.0)


class StreamProcessor(Protocol):
    def feed(self, data: bytes) -> bytes: ...
    def flush(self) -> bytes: ...


def _fwd_req_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _REQ_STRIP}


def _resp_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _RESP_STRIP}


async def forward_streaming(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    processor: StreamProcessor | None = None,
    on_complete: Callable[[StreamProcessor], Awaitable[None]] | None = None,
) -> StreamingResponse:
    """Forward and stream the response back, optionally restored via ``processor``.

    ``on_complete`` runs once the upstream stream ends, giving the caller an async
    point to persist what the processor observed (e.g. binding the response id to
    this session, doc/06 P1-1) — the processor itself is sync and cannot await.
    """
    client = httpx.AsyncClient(timeout=_TIMEOUT)
    upstream = await client.send(
        client.build_request(method, url, headers=_fwd_req_headers(headers), content=body),
        stream=True,
    )

    async def relay() -> AsyncGenerator[bytes, None]:
        try:
            async for chunk in upstream.aiter_raw():
                yield processor.feed(chunk) if processor is not None else chunk
            if processor is not None:
                tail = processor.flush()
                if tail:
                    yield tail
                if on_complete is not None:
                    await on_complete(processor)
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        headers=_resp_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )


async def forward_buffered(
    method: str, url: str, headers: Mapping[str, str], body: bytes
) -> tuple[int, dict[str, str], bytes]:
    """Forward and return the full (non-streaming) response for dict restoration."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.request(method, url, headers=_fwd_req_headers(headers), content=body)
        return resp.status_code, _resp_headers(resp.headers), resp.content


def is_event_stream(content_type: str | None) -> bool:
    return bool(content_type) and "text/event-stream" in (content_type or "")


def upstream_url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def redacted_headers(headers: Mapping[str, str]) -> dict[str, Any]:
    """Header summary safe to log (§25): auth/cookie values are never included."""
    redact = {"authorization", "cookie", "x-api-key", "proxy-authorization"}
    return {k: ("<redacted>" if k.lower() in redact else v) for k, v in headers.items()}
