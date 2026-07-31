"""Gatewayのterminal logが簡潔で、安全な一行形式になることを検証する。"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
import structlog

from securitymasker.cli import _serve_gateway
from securitymasker.logging import configure_logging, get_logger
from securitymasker.metrics import (
    AuditEvent,
    AuditRecord,
    BlockReason,
    Provider,
    StoreOperation,
    StreamErrorReason,
    emit_audit,
)


@pytest.fixture(autouse=True)
def _reset_structlog() -> Iterator[None]:
    yield
    structlog.reset_defaults()


def test_console_log_is_plain_compact_and_written_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging()

    get_logger().info(
        "request_masked",
        provider="openai",
        entity_count=3,
        session_fp="3ed714a9735a",
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} "
        r"\[info\] request_masked entity_count=3 "
        r"session_fp=3ed714a9735a\n",
        captured.err,
    )
    assert "provider=" not in captured.err
    assert "\x1b" not in captured.err
    assert "  " not in captured.err


@pytest.mark.parametrize(
    ("threshold", "shown", "hidden"),
    [
        ("DEBUG", ("debug", "info", "warning", "error"), ()),
        ("INFO", ("info", "warning", "error"), ("debug",)),
        ("WARNING", ("warning", "error"), ("debug", "info")),
        ("ERROR", ("error",), ("debug", "info", "warning")),
    ],
)
def test_console_log_threshold(
    threshold: str,
    shown: tuple[str, ...],
    hidden: tuple[str, ...],
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(threshold)
    log = get_logger()

    log.debug("debug_event")
    log.info("info_event")
    log.warning("warning_event")
    log.error("error_event")

    output = capsys.readouterr().err
    for level in shown:
        assert f"{level}_event" in output
    for level in hidden:
        assert f"{level}_event" not in output


def test_unknown_console_log_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported log level"):
        configure_logging("TRACE")


def test_audit_events_use_product_impact_levels(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("DEBUG")

    emit_audit(
        AuditRecord(AuditEvent.REQUEST_MASKED, Provider.OPENAI, entity_count=2)
    )
    emit_audit(
        AuditRecord(
            AuditEvent.REQUEST_BLOCKED,
            Provider.OPENAI,
            reason=BlockReason.LEAK_GUARD,
        )
    )
    emit_audit(
        AuditRecord(
            AuditEvent.STREAM_ERROR,
            Provider.OPENAI,
            reason=StreamErrorReason.NETWORK,
        )
    )
    emit_audit(
        AuditRecord(
            AuditEvent.STORE_ERROR,
            Provider.OPENAI,
            reason=StoreOperation.REQUEST,
        )
    )

    output = capsys.readouterr().err
    assert "[info] request_masked" in output
    assert "[warning] request_blocked" in output
    assert "[warning] stream_error" in output
    assert "[error] store_error" in output


def test_gateway_lifecycle_logs_start_and_stop(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import uvicorn

    observed: dict[str, object] = {}

    class FakeSocket:
        def close(self) -> None:
            observed["closed"] = True

    class FakeConfig:
        def __init__(self, app: object, **kwargs: object) -> None:
            observed["app"] = app
            observed.update(kwargs)

        def bind_socket(self) -> FakeSocket:
            return FakeSocket()

    class FakeServer:
        def __init__(self, config: FakeConfig) -> None:
            observed["config"] = config

        def run(self, *, sockets: list[FakeSocket]) -> None:
            observed["sockets"] = sockets

    monkeypatch.setattr(uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    configure_logging("INFO")

    assert (
        _serve_gateway(
            object(),
            host="127.0.0.1",
            port=4010,
            mode="chatgpt",
            max_message_bytes=1024,
        )
        == 0
    )

    output = capsys.readouterr().err
    assert "[info] gateway_started url=http://127.0.0.1:4010 mode=chatgpt" in output
    assert "[info] gateway_stopped mode=chatgpt" in output
    assert observed["log_level"] == "critical"
    assert observed["access_log"] is False
    assert observed["closed"] is True


def test_gateway_bind_failure_is_error_without_false_start(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import uvicorn

    class FakeConfig:
        def __init__(self, app: object, **kwargs: object) -> None:
            del app, kwargs

        def bind_socket(self) -> object:
            raise SystemExit(1)

    monkeypatch.setattr(uvicorn, "Config", FakeConfig)
    configure_logging("INFO")

    assert (
        _serve_gateway(
            object(),
            host="127.0.0.1",
            port=4011,
            mode="chatgpt",
            max_message_bytes=1024,
        )
        == 1
    )

    output = capsys.readouterr().err
    assert "[error] gateway_bind_failed host=127.0.0.1 port=4011" in output
    assert "gateway_started" not in output
    assert "gateway_stopped" not in output
