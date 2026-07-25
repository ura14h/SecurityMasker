"""Doctor runtime checks (doc/06 P2-1).

Two things are being verified: that each check actually FAILS on the condition it
claims to detect (a diagnostic that always says "ok" is worse than none), and that
no check ever prints a secret — doctor output is exactly the sort of thing people
paste into tickets and chat.
"""

from __future__ import annotations

import base64
import secrets

import pytest

from securitymasker import doctor
from securitymasker.doctor import Status
from securitymasker.sessions.memory import InMemorySessionStore

CONFIG = "tests/integration/securitymasker.masking.yaml"


def _env(**kw) -> dict[str, str]:
    return {k: v for k, v in kw.items() if v is not None}


# --- environment ------------------------------------------------------------------


def test_python_version_check_passes_on_supported_runtime() -> None:
    assert doctor.check_python().status is Status.OK


def test_runtime_dependencies_are_present() -> None:
    assert doctor.check_runtime_dependencies().status is Status.OK


# --- configuration ------------------------------------------------------------------


def test_missing_config_fails() -> None:
    result, config = doctor.check_config(None)
    assert result.status is Status.FAIL and config is None


def test_valid_config_passes_and_returns_it() -> None:
    result, config = doctor.check_config(CONFIG)
    assert result.status is Status.OK and config is not None


def test_invalid_config_fails(tmp_path) -> None:
    bad = tmp_path / "c.yaml"
    bad.write_text("version: 999\n", encoding="utf-8")
    result, _ = doctor.check_config(str(bad))
    assert result.status is Status.FAIL


def test_missing_env_reference_fails_and_names_only_the_variable(tmp_path, monkeypatch) -> None:
    secret_value = "Zettai-Himitsu-Value-9876"
    monkeypatch.delenv("DOCTOR_TEST_SECRET", raising=False)
    path = tmp_path / "c.yaml"
    path.write_text(
        "version: 1\n"
        "entities:\n"
        "  - id: e1\n    type: API_KEY\n    value_from_env: DOCTOR_TEST_SECRET\n"
        "    replacement_profile: environment_reference\n"
        "    restore_policy: env_reference\n",
        encoding="utf-8")
    _, config = doctor.check_config(str(path))
    result = doctor.check_env_references(config)
    assert result.status is Status.FAIL
    assert "DOCTOR_TEST_SECRET" in result.detail      # the NAME is useful
    assert secret_value not in result.detail          # the VALUE never appears


def test_detectors_check_lists_active_detectors() -> None:
    _, config = doctor.check_config(CONFIG)
    result = doctor.check_detectors(config)
    assert result.status is Status.OK and "dictionary" in result.detail


def test_no_ner_configured_is_ok_not_a_failure() -> None:
    _, config = doctor.check_config(CONFIG)
    assert doctor.check_ner_models(config).status is Status.OK


def test_session_ttl_inversion_fails(tmp_path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("version: 1\ndefaults:\n  session_idle_ttl: 48h\n"
                    "  session_absolute_ttl: 1h\n", encoding="utf-8")
    _, config = doctor.check_config(str(path))
    assert doctor.check_session_ttls(config).status is Status.FAIL


# --- store ------------------------------------------------------------------------


def test_memory_store_is_ok() -> None:
    assert doctor.check_store_backend(_env()).status is Status.OK


def test_redis_without_url_fails() -> None:
    result = doctor.check_store_backend(_env(SECURITYMASKER_STORE="redis"))
    assert result.status is Status.FAIL


def test_unknown_store_backend_fails() -> None:
    assert doctor.check_store_backend(
        _env(SECURITYMASKER_STORE="postgres")).status is Status.FAIL


def test_master_key_skipped_for_memory_store() -> None:
    assert doctor.check_master_key(_env()).status is Status.SKIP


def test_master_key_missing_for_redis_fails() -> None:
    result = doctor.check_master_key(
        _env(SECURITYMASKER_STORE="redis", SECURITYMASKER_REDIS_URL="redis://h"))
    assert result.status is Status.FAIL


def test_master_key_wrong_length_fails_without_printing_it() -> None:
    key = base64.b64encode(b"too-short").decode()
    result = doctor.check_master_key(
        _env(SECURITYMASKER_STORE="redis", SECURITYMASKER_MASTER_KEY=key))
    assert result.status is Status.FAIL
    assert key not in result.detail       # never echo the key material


def test_valid_master_key_passes_without_printing_it() -> None:
    key = base64.b64encode(secrets.token_bytes(32)).decode()
    result = doctor.check_master_key(
        _env(SECURITYMASKER_STORE="redis", SECURITYMASKER_MASTER_KEY=key))
    assert result.status is Status.OK and key not in result.detail


def test_session_crypto_round_trip_passes() -> None:
    assert doctor.check_session_crypto().status is Status.OK


@pytest.mark.asyncio
async def test_store_probe_creates_and_cleans_up() -> None:
    store = InMemorySessionStore()
    result = await doctor.check_store_probe(store)
    assert result.status is Status.OK
    assert await store.list_ids() == [], "probe session was left behind"


@pytest.mark.asyncio
async def test_store_probe_reports_a_broken_store() -> None:
    class _Broken(InMemorySessionStore):
        async def get_or_create(self, session_id, **kw):
            raise RuntimeError("store down")

    assert (await doctor.check_store_probe(_Broken())).status is Status.FAIL


@pytest.mark.asyncio
async def test_store_probe_reports_cleanup_failure() -> None:
    class _NoDelete(InMemorySessionStore):
        async def delete(self, session_id):
            raise RuntimeError("cleanup failed")

    result = await doctor.check_store_probe(_NoDelete())
    assert result.status is Status.FAIL and "cleanup" in result.detail.lower()


# --- identity, network surface -------------------------------------------------------


def test_local_identity_mode_is_ok() -> None:
    assert doctor.check_identity_mode(_env()).status is Status.OK


def test_tenant_mode_without_secret_fails() -> None:
    assert doctor.check_identity_mode(
        _env(SECURITYMASKER_MODE="tenant")).status is Status.FAIL


def test_tenant_mode_warns_that_users_are_not_isolated() -> None:
    result = doctor.check_identity_mode(
        _env(SECURITYMASKER_MODE="tenant", SECURITYMASKER_TENANT_AUTH_SECRET="s"))
    assert result.status is Status.WARN and "tenant_user" in result.detail


def test_tenant_user_mode_with_secret_is_ok() -> None:
    assert doctor.check_identity_mode(
        _env(SECURITYMASKER_MODE="tenant_user",
             SECURITYMASKER_TENANT_AUTH_SECRET="s")).status is Status.OK


def test_upstream_with_embedded_credentials_fails_without_echoing_them() -> None:
    url = "https://user:hunter2@api.example/v1"
    result = doctor.check_upstreams(_env(SECURITYMASKER_OPENAI_UPSTREAM=url))
    assert result.status is Status.FAIL
    assert "hunter2" not in result.detail and url not in result.detail


def test_plaintext_http_to_remote_upstream_fails() -> None:
    assert doctor.check_upstreams(
        _env(SECURITYMASKER_OPENAI_UPSTREAM="http://api.example")).status is Status.FAIL


def test_loopback_http_upstream_is_allowed() -> None:
    assert doctor.check_upstreams(
        _env(SECURITYMASKER_OPENAI_UPSTREAM="http://127.0.0.1:8081",
             SECURITYMASKER_ANTHROPIC_UPSTREAM="http://127.0.0.1:8081")).status is Status.OK


def test_dev_transparent_is_flagged() -> None:
    assert doctor.check_dev_transparent(
        _env(SECURITYMASKER_DEV_TRANSPARENT="1")).status is Status.WARN


def test_public_bind_in_local_mode_fails() -> None:
    result = doctor.check_public_bind(_env(SECURITYMASKER_ALLOW_PUBLIC_BIND="1"))
    assert result.status is Status.FAIL


def test_public_bind_in_tenant_mode_warns() -> None:
    result = doctor.check_public_bind(
        _env(SECURITYMASKER_ALLOW_PUBLIC_BIND="1", SECURITYMASKER_MODE="tenant_user"))
    assert result.status is Status.WARN


def test_direct_provider_env_is_flagged_for_clients() -> None:
    result = doctor.check_client_proxy_config(
        _env(ANTHROPIC_API_URL="https://api.anthropic.com"))
    assert result.status is Status.WARN and "bypass" in result.detail


# --- orchestration and output --------------------------------------------------------


def test_run_checks_yields_every_named_check() -> None:
    results = list(doctor.run_checks(config_path=CONFIG, environ=_env(),
                                     gateway="http://127.0.0.1:1"))
    names = {r.name for r in results}
    for expected in ("python", "dependencies", "config", "config.env", "detectors",
                     "detectors.ner", "fail_mode", "session.ttl", "store",
                     "store.master_key", "crypto", "identity", "upstreams",
                     "dev_transparent", "bind", "gateway", "clients"):
        assert expected in names, f"check {expected} missing"


def test_gateway_unreachable_is_a_warning_not_a_hard_failure() -> None:
    # doctor is often run before the gateway starts; that must not be fatal.
    assert doctor.check_gateway_ready("http://127.0.0.1:1").status is Status.WARN


def test_json_output_is_machine_readable_and_secret_free() -> None:
    import json

    key = base64.b64encode(secrets.token_bytes(32)).decode()
    results = list(doctor.run_checks(
        config_path=CONFIG,
        environ=_env(SECURITYMASKER_STORE="redis", SECURITYMASKER_REDIS_URL="redis://h",
                     SECURITYMASKER_MASTER_KEY=key),
        gateway="http://127.0.0.1:1"))
    payload = doctor.render_json(results)
    parsed = json.loads(payload)
    assert isinstance(parsed["checks"], list) and "ok" in parsed
    assert key not in payload


def test_text_output_renders_every_result() -> None:
    results = list(doctor.run_checks(config_path=CONFIG, environ=_env(),
                                     gateway="http://127.0.0.1:1"))
    rendered = doctor.render(results)
    assert rendered.count("\n") == len(results) - 1
    for result in results:
        assert result.name in rendered


def test_cli_doctor_exits_non_zero_when_a_check_fails(monkeypatch, capsys) -> None:
    from securitymasker import cli

    monkeypatch.delenv("SECURITYMASKER_CONFIG", raising=False)
    args = cli.build_parser().parse_args(["doctor", "--gateway", "http://127.0.0.1:1"])
    assert cli.cmd_doctor(args) == 1          # no config -> FAIL -> non-zero
    assert "FAIL" in capsys.readouterr().out
