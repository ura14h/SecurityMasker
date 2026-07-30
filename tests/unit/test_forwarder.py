"""Upstream転送のprotocol境界を検証する。"""

from securitymasker.gateway.forwarder import response_media_type


def test_missing_success_content_type_is_treated_as_event_stream() -> None:
    assert (
        response_media_type(None, status_code=200, has_processor=True)
        == "text/event-stream"
    )


def test_explicit_content_type_is_preserved() -> None:
    assert (
        response_media_type(
            "application/json",
            status_code=200,
            has_processor=True,
        )
        == "application/json"
    )


def test_missing_error_content_type_is_not_treated_as_event_stream() -> None:
    assert response_media_type(None, status_code=400, has_processor=True) is None


def test_unprocessed_stream_does_not_invent_content_type() -> None:
    assert response_media_type(None, status_code=200, has_processor=False) is None
