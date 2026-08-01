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

from securitymasker.logging import get_logger

# hop-by-hop headerと再計算するheaderは両方向とも転送しない。
_REQ_STRIP = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding"}
_RESP_STRIP = {"content-length", "content-encoding", "transfer-encoding", "connection"}

_TIMEOUT = httpx.Timeout(connect=15.0, read=600.0, write=60.0, pool=15.0)
_log = get_logger(component="securitymasker.gateway.forwarder")


class StreamProcessor(Protocol):
    def feed(self, data: bytes) -> bytes: ...
    def flush(self) -> bytes: ...


def _fwd_req_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _REQ_STRIP}


def _resp_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _RESP_STRIP}


def response_media_type(
    content_type: str | None,
    *,
    status_code: int,
    has_processor: bool,
) -> str | None:
    """成功したResponses streamだけ、欠落したSSE media typeを補う。"""
    if content_type is None and has_processor and 200 <= status_code < 300:
        return "text/event-stream"
    return content_type


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
    try:
        upstream = await client.send(
            client.build_request(method, url, headers=_fwd_req_headers(headers), content=body),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        _log.warning("sm_upstream_network_error", reason=type(exc).__name__)
        raise
    _log.debug(
        "sm_upstream_stream_started",
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )
    content_type = upstream.headers.get("content-type")
    process_stream = (
        processor is not None
        and 200 <= upstream.status_code < 300
        and (content_type is None or is_event_stream(content_type))
    )

    async def relay() -> AsyncGenerator[bytes, None]:
        try:
            # responseのContent-Encodingはclientへ返さないため、httpxで展開済みの
            # bytesを処理・転送する。rawなgzip/brをSSE UTF-8として扱ってはいけない。
            async for chunk in upstream.aiter_bytes():
                if process_stream and processor is not None:
                    yield processor.feed(chunk)
                else:
                    yield chunk
            if process_stream and processor is not None:
                tail = processor.flush()
                if tail:
                    yield tail
                if on_complete is not None:
                    await on_complete(processor)
            _log.debug("sm_upstream_stream_completed", status_code=upstream.status_code)
        except Exception as exc:
            # 原文やevent payloadを出さず、実provider差分の型だけを診断可能にする。
            _log.debug(
                "sm_upstream_stream_processing_failed",
                reason=type(exc).__name__,
            )
            raise
        finally:
            await upstream.aclose()
            await client.aclose()

    media_type = response_media_type(
        content_type,
        status_code=upstream.status_code,
        has_processor=process_stream,
    )
    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        headers=_resp_headers(upstream.headers),
        media_type=media_type,
    )


async def forward_buffered(
    method: str, url: str, headers: Mapping[str, str], body: bytes
) -> tuple[int, dict[str, str], bytes]:
    """転送後、dict復元用に非streamingのresponse全文を返す。"""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(
                method, url, headers=_fwd_req_headers(headers), content=body
            )
    except httpx.HTTPError as exc:
        _log.warning("sm_upstream_network_error", reason=type(exc).__name__)
        raise
    _log.debug("sm_upstream_response_completed", status_code=resp.status_code)
    return resp.status_code, _resp_headers(resp.headers), resp.content


def is_event_stream(content_type: str | None) -> bool:
    return bool(content_type) and "text/event-stream" in (content_type or "")


def upstream_url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def redacted_headers(headers: Mapping[str, str]) -> dict[str, Any]:
    """安全にログ記録できるheader概要。認証情報とcookie値は含めない。"""
    redact = {"authorization", "cookie", "x-api-key", "proxy-authorization"}
    return {k: ("<redacted>" if k.lower() in redact else v) for k, v in headers.items()}
