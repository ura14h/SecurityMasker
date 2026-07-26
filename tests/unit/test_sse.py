"""SSEの複数行data、comment、event、retryの解析と直列化を検証する。"""

from __future__ import annotations

from securitymasker.protocols.sse import SSEParser, parse_sse, serialize_event


def test_parse_simple_data_event() -> None:
    events = parse_sse("data: hello\n\n")
    assert len(events) == 1
    assert events[0].data_text == "hello"


def test_multiline_data_joined_with_newline() -> None:
    events = parse_sse("data: line1\ndata: line2\n\n")
    assert events[0].data == ["line1", "line2"]
    assert events[0].data_text == "line1\nline2"


def test_event_id_retry_and_comment_preserved() -> None:
    raw = ":keep-alive\nevent: message\nid: 42\nretry: 1000\ndata: {}\n\n"
    ev = parse_sse(raw)[0]
    assert ev.event == "message"
    assert ev.id == "42"
    assert ev.retry == 1000
    assert ev.comments == ["keep-alive"]


def test_done_sentinel_preserved() -> None:
    ev = parse_sse("data: [DONE]\n\n")[0]
    assert ev.data_text == "[DONE]"


def test_roundtrip_serialize_parse() -> None:
    raw = "event: response.completed\ndata: {\"type\": \"x\"}\n\n"
    ev = parse_sse(raw)[0]
    reparsed = parse_sse(serialize_event(ev))[0]
    assert reparsed.event == ev.event
    assert reparsed.data_text == ev.data_text


def test_unknown_event_passes_through() -> None:
    ev = parse_sse("event: some.future.event\ndata: payload\n\n")[0]
    assert ev.event == "some.future.event"
    assert ev.data_text == "payload"


def test_incremental_parsing_across_chunks() -> None:
    parser = SSEParser()
    collected = []
    for piece in ["event: mes", "sage\nda", "ta: hi\n", "\nevent: two\ndata: yo\n\n"]:
        collected.extend(parser.feed(piece))
    collected.extend(parser.flush())
    assert [e.event for e in collected] == ["message", "two"]
    assert [e.data_text for e in collected] == ["hi", "yo"]


def test_crlf_normalized() -> None:
    events = parse_sse("data: a\r\ndata: b\r\n\r\n")
    assert events[0].data == ["a", "b"]
