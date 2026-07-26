"""httpxによるupstreamへの透過転送。

マスク済みrequest bodyと許可済みheaderをupstreamへ転送する。認証情報は保存・復号・
ログ記録しない。streaming responseは状態を持つprocessorでaliasを復元し、
非streaming responseは全体をbufferして復元処理へ渡す。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from typing import Any, Protocol

import httpx
from starlette.responses import StreamingResponse

# hop-by-hop headerと再計算するheaderは両方向とも転送しない。
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
    """requestを転送し、必要なら``processor``で復元しながらresponseをstreamで返す。

    ``on_complete``はupstream stream完了後に一度だけ実行する。同期processorが観測した
    response IDなどを、callerが非同期でsessionへ保存するために使う。
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
    """転送後、dict復元用に非streamingのresponse全文を返す。"""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.request(method, url, headers=_fwd_req_headers(headers), content=body)
        return resp.status_code, _resp_headers(resp.headers), resp.content


def is_event_stream(content_type: str | None) -> bool:
    return bool(content_type) and "text/event-stream" in (content_type or "")


def upstream_url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def redacted_headers(headers: Mapping[str, str]) -> dict[str, Any]:
    """安全にログ記録できるheader概要。認証情報とcookie値は含めない。"""
    redact = {"authorization", "cookie", "x-api-key", "proxy-authorization"}
    return {k: ("<redacted>" if k.lower() in redact else v) for k, v in headers.items()}
