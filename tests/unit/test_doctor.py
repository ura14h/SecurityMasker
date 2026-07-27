"""version 2のローカル運用だけを対象にしたdoctor検証。"""

from __future__ import annotations

from pathlib import Path

import pytest

from securitymasker import doctor
from securitymasker.bootstrap import initialize_layout
from securitymasker.doctor import Status


def _layout(tmp_path: Path, *, port: int = 49153):
    return initialize_layout(tmp_path, mode="chatgpt", port=port)


def test_python_311_is_supported_but_310_is_not() -> None:
    assert doctor.check_python((3, 11, 0)).status is Status.OK
    unsupported = doctor.check_python((3, 10, 99))
    assert unsupported.status is Status.FAIL
    assert "3.11+ is required" in unsupported.detail


def test_v2_layout_passes_without_creating_database(tmp_path: Path) -> None:
    layout = _layout(tmp_path)

    results, artifacts = doctor.run_checks_with_engine(
        config_path=str(layout.config),
        environ={},
        gateway="http://127.0.0.1:1",
    )

    by_name = {result.name: result for result in results}
    assert by_name["config"].status is Status.OK
    assert by_name["dictionary"].status is Status.OK
    assert by_name["state"].status is Status.OK
    assert by_name["crypto"].status is Status.OK
    assert by_name["gateway"].status is Status.WARN
    assert artifacts.config.version == 2
    assert artifacts.engine is not None
    assert not (layout.state_directory / "securitymasker.db").exists()


def test_missing_dictionary_fails_without_echoing_values(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    secret = "合成機密値"
    layout.dictionary.write_text(secret, encoding="utf-8")
    layout.dictionary.unlink()

    results = list(
        doctor.run_checks(
            config_path=str(layout.config),
            environ={},
            gateway="http://127.0.0.1:1",
        )
    )

    rendered = doctor.render(results)
    assert any(result.failed for result in results)
    assert secret not in rendered


def test_non_v2_config_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "legacy.yaml"
    path.write_text("version: 1\n", encoding="utf-8")

    result, config, engine = doctor.check_config(str(path))

    assert result.status is Status.FAIL
    assert "version 2" in result.detail
    assert config.version == 1
    assert engine is None


@pytest.mark.parametrize(
    "url",
    [
        "ftp://provider.invalid",
        "http://provider.invalid",
        "https://user:password@provider.invalid",
    ],
)
def test_unsafe_upstream_is_rejected_without_echoing_url(url: str) -> None:
    result = doctor.check_upstreams({"SECURITYMASKER_OPENAI_UPSTREAM": url})

    assert result.status is Status.FAIL
    assert url not in result.detail


def test_require_ready_turns_unreachable_gateway_into_failure(tmp_path: Path) -> None:
    layout = _layout(tmp_path)

    results = list(
        doctor.run_checks(
            config_path=str(layout.config),
            environ={},
            gateway="http://127.0.0.1:1",
            require_ready=True,
        )
    )

    gateway = next(result for result in results if result.name == "gateway")
    assert gateway.status is Status.FAIL


def test_detector_pipeline_is_built_only_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)
    from securitymasker import config as config_module

    calls = 0
    original = config_module.build_detectors

    def counting(config):
        nonlocal calls
        calls += 1
        return original(config)

    monkeypatch.setattr(config_module, "build_detectors", counting)
    list(
        doctor.run_checks(
            config_path=str(layout.config),
            environ={},
            gateway="http://127.0.0.1:1",
        )
    )

    assert calls == 1
