"""Gatewayのterminal logが簡潔で、安全な一行形式になることを検証する。"""

from __future__ import annotations

import re

import pytest
import structlog

from securitymasker.logging import configure_logging, get_logger


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
    structlog.reset_defaults()
