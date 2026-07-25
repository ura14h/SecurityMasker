"""Enumerated runtime diagnostics (doc/06 P2-1).

`doctor` used to check the config and stop there, which meant it could report a
healthy system while Redis was unreachable, the master key was malformed, or the
proxy was about to bind publicly without an authenticator. Every check below is a
separate unit with a name and a verdict, so what was actually verified is
explicit — "fully wired" is not a claim anyone can check.

Two rules shape the output:

- **No secrets, ever.** Not the master key, not URL credentials, not dictionary
  values, not session mappings, not an identity proof. Checks report shape and
  reachability; they quote configuration keys, never configuration values (§25).
- **Never talk to a real provider.** Upstreams are validated syntactically —
  scheme, host, loopback-ness. `doctor` sends no request body anywhere.

Checks are pure functions over an already-built runtime where possible, so the
same code path can be unit-tested without a live gateway.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlsplit


class Status(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str          # secret-free, safe to print, log, and paste into a ticket

    @property
    def failed(self) -> bool:
        return self.status is Status.FAIL


def _ok(name: str, detail: str) -> CheckResult:
    return CheckResult(name, Status.OK, detail)


def _fail(name: str, detail: str) -> CheckResult:
    return CheckResult(name, Status.FAIL, detail)


def _warn(name: str, detail: str) -> CheckResult:
    return CheckResult(name, Status.WARN, detail)


def _skip(name: str, detail: str) -> CheckResult:
    return CheckResult(name, Status.SKIP, detail)


# --- environment -------------------------------------------------------------------


def check_python() -> CheckResult:
    version = sys.version_info
    text = f"{version.major}.{version.minor}.{version.micro}"
    if version < (3, 12):
        return _fail("python", f"{text} — 3.12+ is required")
    return _ok("python", text)


def check_runtime_dependencies() -> CheckResult:
    from importlib.metadata import PackageNotFoundError, version

    required = ("starlette", "uvicorn", "httpx", "pydantic", "cryptography", "PyYAML")
    found, missing = [], []
    for name in required:
        try:
            found.append(f"{name} {version(name)}")
        except PackageNotFoundError:
            missing.append(name)
    if missing:
        return _fail("dependencies", f"missing: {', '.join(missing)}")
    return _ok("dependencies", ", ".join(found))


# --- configuration ------------------------------------------------------------------


def check_config(path: str | None) -> tuple[CheckResult, Any]:
    """Load AND build, so the same validation the gateway performs runs here."""
    if not path:
        return _fail("config", "no masking config given (--config or "
                               "SECURITYMASKER_CONFIG); the gateway requires one"), None
    from securitymasker.config import build_engine, load_config
    from securitymasker.errors import SecurityMaskerError

    try:
        config = load_config(path)
    except SecurityMaskerError as exc:
        return _fail("config", f"invalid: {exc}"), None
    try:
        # Env references, detector models, regex safety — the startup checks.
        build_engine(config)
    except SecurityMaskerError as exc:
        return _fail("config.build", f"detectors could not be built: {exc}"), config
    return _ok("config", f"{len(config.entities)} entities, {len(config.patterns)} "
                         f"patterns, fail_mode={config.defaults.fail_mode}"), config


def check_env_references(config: Any) -> CheckResult:
    """Each ``value_from_env`` must resolve; report NAMES only, never values."""
    if config is None:
        return _skip("config.env", "no config loaded")
    import os

    missing = [
        e.value_from_env for e in config.entities
        if e.value_from_env and not os.environ.get(e.value_from_env)
    ]
    if missing:
        return _fail("config.env", f"unset environment variables: {sorted(missing)}")
    declared = sum(1 for e in config.entities if e.value_from_env)
    noun = "entity" if declared == 1 else "entities"
    return _ok("config.env", f"{declared} env-backed {noun} resolved")


def check_detectors(config: Any) -> CheckResult:
    if config is None:
        return _skip("detectors", "no config loaded")
    from securitymasker.config import build_detectors

    try:
        detectors = build_detectors(config)
    except Exception as exc:  # noqa: BLE001
        return _fail("detectors", f"could not be built: {type(exc).__name__}")
    names = [getattr(d, "name", "?") for d in detectors]
    return _ok("detectors", f"{len(names)} active: {', '.join(names)}")


def check_ner_models(config: Any) -> CheckResult:
    """Presidio/HF availability, matching what the config actually asks for."""
    if config is None:
        return _skip("detectors.ner", "no config loaded")
    notes = []
    if config.presidio.enabled:
        from securitymasker.detectors.presidio import PresidioDetector

        presidio: Any = PresidioDetector(language=config.presidio.language,
                                         model_name=config.presidio.model_name)
        if not presidio.available:
            return _fail("detectors.ner",
                         f"presidio enabled but model {config.presidio.model_name!r} "
                         "is unavailable")
        notes.append(f"presidio:{config.presidio.model_name}")
    if config.ner.model:
        from securitymasker.detectors.japanese_ner import JapaneseNerDetector

        hf: Any = JapaneseNerDetector(model=config.ner.model, revision=config.ner.revision,
                                      local_files_only=config.ner.local_files_only)
        if not hf.available:
            return _fail("detectors.ner",
                         f"ner.model {config.ner.model!r}@{config.ner.revision} is not "
                         "available locally — run 'securitymasker models fetch'")
        notes.append(f"hf:{config.ner.model}@{(config.ner.revision or '')[:8]}")
    if not notes:
        return _ok("detectors.ner", "no NER configured (dictionary + deterministic only)")
    return _ok("detectors.ner", ", ".join(notes))


def check_fail_mode(config: Any) -> CheckResult:
    if config is None:
        return _skip("fail_mode", "no config loaded")
    mode = config.defaults.fail_mode
    if mode == "open":
        return _warn("fail_mode", "open — fuzzy detector faults are skipped rather "
                                  "than blocking (critical detectors still fail closed)")
    return _ok("fail_mode", "closed")


def check_session_ttls(config: Any) -> CheckResult:
    if config is None:
        return _skip("session.ttl", "no config loaded")
    from securitymasker.config import parse_duration

    idle = parse_duration(config.defaults.session_idle_ttl)
    absolute = parse_duration(config.defaults.session_absolute_ttl)
    if idle > absolute:
        return _fail("session.ttl", "idle TTL exceeds absolute TTL")
    return _ok("session.ttl", f"idle={config.defaults.session_idle_ttl}, "
                              f"absolute={config.defaults.session_absolute_ttl}")


# --- store ---------------------------------------------------------------------------


def check_store_backend(environ: dict[str, str]) -> CheckResult:
    backend = environ.get("SECURITYMASKER_STORE", "memory").lower()
    if backend == "memory":
        return _ok("store", "memory (single process; not shared across workers)")
    if backend != "redis":
        return _fail("store", f"unknown backend {backend!r}")
    try:
        import redis  # noqa: F401
    except ImportError:
        return _fail("store", "redis selected but the 'redis' package is not installed")
    if not environ.get("SECURITYMASKER_REDIS_URL"):
        return _fail("store", "redis selected but SECURITYMASKER_REDIS_URL is unset")
    return _ok("store", "redis (configured)")


def check_master_key(environ: dict[str, str]) -> CheckResult:
    """Validate the key's SHAPE. The key itself is never printed or logged."""
    backend = environ.get("SECURITYMASKER_STORE", "memory").lower()
    raw = environ.get("SECURITYMASKER_MASTER_KEY")
    if backend != "redis":
        return _skip("store.master_key", "not required for the memory store")
    if not raw:
        return _fail("store.master_key", "SECURITYMASKER_MASTER_KEY is required for redis")
    import base64

    try:
        decoded = base64.b64decode(raw, validate=True)
    except Exception:  # noqa: BLE001
        return _fail("store.master_key", "not valid base64")
    if len(decoded) != 32:
        return _fail("store.master_key", f"must decode to 32 bytes, got {len(decoded)}")
    return _ok("store.master_key", "present, 32 bytes")


def check_session_crypto() -> CheckResult:
    """Round-trip a synthetic value through the session AEAD (§8)."""
    from securitymasker.errors import CryptoError
    from securitymasker.sessions.crypto import decrypt, encrypt, generate_session_keys

    probe = "securitymasker-doctor-probe"     # synthetic, never a real secret
    try:
        _, aead_key = generate_session_keys()
        sealed = encrypt(aead_key, probe, aad=b"doctor")
        if decrypt(aead_key, sealed, aad=b"doctor") != probe:
            return _fail("crypto", "session encryption round-trip did not match")
    except CryptoError as exc:
        return _fail("crypto", f"session encryption failed: {type(exc).__name__}")
    return _ok("crypto", "AES-GCM session round-trip OK")


async def check_store_probe(store: Any) -> CheckResult:
    """Create/read/delete a synthetic session, then confirm the cleanup."""
    import secrets

    probe_id = f"__doctor_probe_{secrets.token_hex(8)}"   # random, non-sensitive
    try:
        await store.get_or_create(probe_id)
    except Exception as exc:  # noqa: BLE001
        return _fail("store.probe", f"write failed: {type(exc).__name__}")
    try:
        await store.delete(probe_id)
    except Exception as exc:  # noqa: BLE001
        return _fail("store.probe", f"cleanup FAILED — probe {probe_id} may remain: "
                                    f"{type(exc).__name__}")
    try:
        leftover = await store.get(probe_id)
    except Exception:  # noqa: BLE001
        leftover = None
    if leftover is not None:
        return _fail("store.probe", "cleanup did not remove the probe session")
    return _ok("store.probe", "write/read/delete OK, probe removed")


# --- identity + network surface --------------------------------------------------------


def check_identity_mode(environ: dict[str, str]) -> CheckResult:
    from securitymasker.gateway.identity import MODE_LOCAL, MODE_TENANT, VALID_MODES

    mode = environ.get("SECURITYMASKER_MODE", MODE_LOCAL)
    if mode == "multitenant":
        mode = MODE_TENANT
    if mode not in VALID_MODES:
        return _fail("identity", f"unknown SECURITYMASKER_MODE {mode!r}")
    if mode == MODE_LOCAL:
        return _ok("identity", "local (single caller; no tenant/user isolation)")
    if not environ.get("SECURITYMASKER_TENANT_AUTH_SECRET"):
        return _fail("identity", f"{mode} requires SECURITYMASKER_TENANT_AUTH_SECRET")
    if mode == MODE_TENANT:
        return _warn("identity", "tenant — users WITHIN a tenant share an alias table; "
                                 "use tenant_user for mutually distrusting users")
    return _ok("identity", "tenant_user (tenant and user isolated)")


def check_upstreams(environ: dict[str, str]) -> CheckResult:
    """Syntactic validation only — doctor never contacts a provider."""
    from securitymasker.gateway.runtime import (
        DEFAULT_ANTHROPIC_UPSTREAM,
        DEFAULT_OPENAI_UPSTREAM,
    )

    problems = []
    for name, default in (("SECURITYMASKER_OPENAI_UPSTREAM", DEFAULT_OPENAI_UPSTREAM),
                          ("SECURITYMASKER_ANTHROPIC_UPSTREAM", DEFAULT_ANTHROPIC_UPSTREAM)):
        url = environ.get(name, default)
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            problems.append(f"{name}: scheme must be http/https")
        elif not parts.hostname:
            problems.append(f"{name}: no host")
        elif parts.username or parts.password:
            # Never echo the URL itself here: it contains the credential.
            problems.append(f"{name}: contains embedded credentials — remove them")
        elif parts.scheme == "http" and parts.hostname not in ("127.0.0.1", "localhost", "::1"):
            problems.append(f"{name}: plaintext http to a non-loopback host")
    if problems:
        return _fail("upstreams", "; ".join(problems))
    return _ok("upstreams", "scheme/host valid (not contacted)")


def check_dev_transparent(environ: dict[str, str]) -> CheckResult:
    if environ.get("SECURITYMASKER_DEV_TRANSPARENT") != "1":
        return _ok("dev_transparent", "disabled")
    if environ.get("SECURITYMASKER_CONFIG"):
        return _warn("dev_transparent", "set, but a config is present so masking wins")
    return _warn("dev_transparent",
                 "ENABLED — bodies are forwarded UNMASKED (loopback upstreams only)")


def check_public_bind(environ: dict[str, str]) -> CheckResult:
    from securitymasker.gateway.identity import MODE_LOCAL

    if environ.get("SECURITYMASKER_ALLOW_PUBLIC_BIND") != "1":
        return _ok("bind", "loopback only")
    mode = environ.get("SECURITYMASKER_MODE", MODE_LOCAL)
    if mode == MODE_LOCAL:
        return _fail("bind", "public bind acknowledged while in local mode — every "
                             "caller would share one alias table")
    return _warn("bind", "public bind acknowledged; an authenticator must front the proxy")


def check_gateway_ready(gateway: str) -> CheckResult:
    from securitymasker.integrations.readiness import check_readiness

    status = check_readiness(gateway)
    return _ok("gateway", status.detail) if status.ok else _warn("gateway", status.detail)


def check_client_proxy_config(environ: dict[str, str]) -> CheckResult:
    """Report whether the local clients look routed. Advisory, never fatal."""
    notes = []
    base = environ.get("ANTHROPIC_BASE_URL")
    notes.append(f"claude: ANTHROPIC_BASE_URL={'set' if base else 'unset'}")
    direct = [n for n in ("ANTHROPIC_API_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE")
              if environ.get(n)]
    if direct:
        return _warn("clients", f"{', '.join(direct)} set — traffic would bypass the "
                                f"proxy ({'; '.join(notes)})")
    notes.append("codex: routed per-process by `securitymasker run`")
    return _ok("clients", "; ".join(notes))


# --- orchestration ---------------------------------------------------------------------


def run_checks(
    *, config_path: str | None, environ: dict[str, str], gateway: str,
) -> Iterator[CheckResult]:
    """Yield every check in a stable order. Pure except for the store probe."""
    yield check_python()
    yield check_runtime_dependencies()

    config_result, config = check_config(config_path)
    yield config_result
    yield check_env_references(config)
    yield check_detectors(config)
    yield check_ner_models(config)
    yield check_fail_mode(config)
    yield check_session_ttls(config)

    yield check_store_backend(environ)
    yield check_master_key(environ)
    yield check_session_crypto()

    yield check_identity_mode(environ)
    yield check_upstreams(environ)
    yield check_dev_transparent(environ)
    yield check_public_bind(environ)
    yield check_gateway_ready(gateway)
    yield check_client_proxy_config(environ)


_SYMBOL: dict[Status, str] = {
    Status.OK: "ok  ", Status.WARN: "warn", Status.FAIL: "FAIL", Status.SKIP: "skip",
}


def render(results: list[CheckResult]) -> str:
    return "\n".join(f"[{_SYMBOL[r.status]}] {r.name}: {r.detail}" for r in results)


def render_json(results: list[CheckResult]) -> str:
    """Machine-readable form. Contains only names, statuses and safe details."""
    import json

    return json.dumps(
        {"checks": [{"name": r.name, "status": r.status.value, "detail": r.detail}
                    for r in results],
         "ok": not any(r.failed for r in results)},
        ensure_ascii=False, indent=2,
    )


ProbeRunner = Callable[[], Any]
