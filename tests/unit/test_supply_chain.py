"""Supply-chain invariants that must not silently regress (doc/06 P2-3).

Pinning is easy to do once and easy to lose: the next person edits a Dockerfile,
drops the digest, and nothing complains. These are static checks over the repo
files so an unpinned image or a dev tool in the production image fails CI rather
than shipping.

They deliberately assert on the FILES, not on a built image: they must run
anywhere, without a Docker daemon.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "docker-compose.yml"
RUNTIME_LOCK = ROOT / "requirements.lock"
DEV_LOCK = ROOT / "requirements-dev.lock"

_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}\b")


# --- image pinning ------------------------------------------------------------------


def test_every_dockerfile_base_image_is_digest_pinned() -> None:
    froms = [
        line for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.strip().upper().startswith("FROM ")
    ]
    assert froms, "no FROM lines found"
    for line in froms:
        image = line.split()[1]
        # A stage that builds on a local stage (FROM runtime AS demo) inherits the
        # pinned base and needs no digest of its own.
        if image in {"runtime", "demo", "base"}:
            continue
        assert _DIGEST.search(image), f"unpinned base image: {line.strip()!r}"


def test_every_compose_image_is_digest_pinned() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    for name, service in compose.get("services", {}).items():
        image = service.get("image")
        if image is None:
            assert "build" in service, f"service {name} has neither image nor build"
            continue
        assert _DIGEST.search(image), f"service {name} uses an unpinned image: {image}"


def test_pinned_digests_keep_their_tag_for_readability() -> None:
    # tag@digest, not bare digest: the tag tells a human which line they are on.
    text = DOCKERFILE.read_text(encoding="utf-8") + COMPOSE.read_text(encoding="utf-8")
    for match in _DIGEST.finditer(text):
        prefix = text[:match.start()].rsplit(None, 1)[-1]
        assert ":" in prefix, f"digest without a tag for readability: {prefix}"


# --- production image contents --------------------------------------------------------


def test_production_stage_contains_no_test_code() -> None:
    """Test/mock code belongs to the demo stage only (doc/06 P2-3)."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    runtime_stage, _, demo_stage = text.partition("FROM runtime AS demo")
    assert "tests/" not in runtime_stage, "production stage copies test code"
    assert "devtools" not in runtime_stage, "production stage copies dev tooling"
    # The demo stage is where the mock upstream is allowed to appear.
    assert "devtools" in demo_stage


def test_production_stage_runs_as_non_root() -> None:
    runtime_stage = DOCKERFILE.read_text(encoding="utf-8").split("FROM runtime AS demo")[0]
    assert "USER securitymasker" in runtime_stage


def test_image_does_not_bake_the_public_bind_acknowledgement() -> None:
    # Baking it in would let `docker run -p 4000:4000` skip the safety prompt.
    runtime_stage = DOCKERFILE.read_text(encoding="utf-8").split("FROM runtime AS demo")[0]
    assert "SECURITYMASKER_ALLOW_PUBLIC_BIND=1" not in runtime_stage


def test_healthcheck_uses_readiness_not_liveness() -> None:
    # /health is up as soon as the process starts; only /ready reflects the store.
    text = DOCKERFILE.read_text(encoding="utf-8")
    healthcheck = text.split("HEALTHCHECK", 1)[1].split("\n\n")[0]
    assert "/ready" in healthcheck and "/health'" not in healthcheck


# --- dependency locks ------------------------------------------------------------------


def _lock_packages(path: Path) -> set[str]:
    return {
        line.split("==")[0].strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if "==" in line and not line.strip().startswith("#")
    }


def test_runtime_lock_is_fully_pinned() -> None:
    for line in RUNTIME_LOCK.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert "==" in stripped, f"unpinned runtime requirement: {stripped!r}"


def test_runtime_lock_carries_no_dev_tooling() -> None:
    # The production image installs this lock; test tooling must not ship in it.
    forbidden = {"pytest", "pytest-asyncio", "mypy", "ruff", "hypothesis", "coverage"}
    assert not (_lock_packages(RUNTIME_LOCK) & forbidden)


def test_dev_lock_is_a_superset_of_the_runtime_lock() -> None:
    runtime, dev = _lock_packages(RUNTIME_LOCK), _lock_packages(DEV_LOCK)
    missing = runtime - dev
    # CI installs the dev lock, so anything the runtime needs must be in it too;
    # otherwise CI tests a different dependency set than production ships.
    assert not missing, f"runtime packages absent from the dev lock: {sorted(missing)}"


def test_dockerfile_installs_from_the_lock_not_the_range_spec() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "-r requirements.lock" in text
    # `pip install .` without --no-deps would re-resolve the pyproject ranges and
    # quietly defeat the lock.
    assert "--no-deps" in text


@pytest.mark.parametrize("workflow", ["ci.yml"])
def test_ci_installs_from_the_lock(workflow) -> None:
    path = ROOT / ".github" / "workflows" / workflow
    if not path.exists():
        pytest.skip(f"{workflow} not present")
    text = path.read_text(encoding="utf-8")
    assert "requirements-dev.lock" in text, "CI does not install from the lock"


# --- compose environment hygiene ------------------------------------------------------


def _compose_env(*files: str, profile: str | None = None) -> dict:
    """Expand a Compose configuration and return each service's environment.

    Asserts on the EXPANDED result rather than the file text: variable
    interpolation and overlay merging are exactly where a stray secret-shaped
    value would appear.
    """
    import shutil
    import subprocess

    if shutil.which("docker") is None:
        pytest.skip("docker not available")
    cmd = ["docker", "compose"]
    for name in files:
        cmd += ["-f", name]
    if profile:
        cmd += ["--profile", profile]
    cmd.append("config")
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if done.returncode != 0:
        pytest.fail(f"compose config failed: {done.stderr[:300]}")
    parsed = yaml.safe_load(done.stdout)
    return {name: (svc.get("environment") or {})
            for name, svc in parsed.get("services", {}).items()}


def test_memory_compose_carries_no_redis_or_key_material() -> None:
    """The default setup must not hold values it never reads.

    A base file carrying a Redis URL and a master key invites someone to treat
    the demo key as configuration — and makes it impossible to tell, from the
    file, whether the store is actually shared.
    """
    env = _compose_env("docker-compose.yml")
    gateway = env.get("gateway", {})
    for name in ("SECURITYMASKER_REDIS_URL", "SECURITYMASKER_MASTER_KEY",
                 "SECURITYMASKER_STORE"):
        assert name not in gateway, f"{name} present in the memory-only compose file"


def test_memory_compose_uses_the_demo_dictionary_not_the_production_template() -> None:
    env = _compose_env("docker-compose.yml")
    config = env["gateway"]["SECURITYMASKER_CONFIG"]
    assert config.endswith("securitymasker.demo.yaml")


def test_memory_compose_has_no_env_backed_secret_placeholders() -> None:
    # The production template declares PROD_DB_HOST / INTERNAL_API_KEY via
    # value_from_env; the demo must not need to invent values for them.
    gateway = _compose_env("docker-compose.yml")["gateway"]
    assert "PROD_DB_HOST" not in gateway and "INTERNAL_API_KEY" not in gateway


def test_redis_overlay_supplies_the_store_configuration() -> None:
    env = _compose_env("docker-compose.yml", "docker-compose.redis.yml", profile="redis")
    gateway = env["gateway"]
    assert gateway["SECURITYMASKER_STORE"] == "redis"
    assert gateway["SECURITYMASKER_REDIS_URL"]
    assert gateway["SECURITYMASKER_MASTER_KEY"]


def test_redis_overlay_demo_key_is_a_valid_32_byte_key() -> None:
    """A malformed demo key makes the documented command fail closed at startup —
    which is exactly what happened before (it decoded to 33 bytes)."""
    import base64

    env = _compose_env("docker-compose.yml", "docker-compose.redis.yml", profile="redis")
    key = env["gateway"]["SECURITYMASKER_MASTER_KEY"]
    assert len(base64.b64decode(key, validate=True)) == 32


def test_demo_config_needs_no_environment_secrets() -> None:
    import yaml as _yaml

    config = _yaml.safe_load((ROOT / "config" / "securitymasker.demo.yaml").read_text(
        encoding="utf-8"))
    for entity in config.get("entities", []):
        assert "value_from_env" not in entity, entity["id"]


def test_compose_publishes_the_gateway_on_loopback_only() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    for port in compose["services"]["gateway"].get("ports", []):
        assert str(port).startswith("127.0.0.1:"), f"non-loopback publish: {port}"


def test_container_bind_acknowledgement_is_set_in_compose_not_the_image() -> None:
    # A container must bind 0.0.0.0 to be reachable, so the acknowledgement is
    # required — but it belongs next to the loopback publish, not in the image.
    env = _compose_env("docker-compose.yml")
    assert env["gateway"].get("SECURITYMASKER_ALLOW_PUBLIC_BIND") == "1"
    assert "SECURITYMASKER_ALLOW_PUBLIC_BIND=1" not in DOCKERFILE.read_text(encoding="utf-8")


# --- decisions the code cites must actually be written down ------------------------


def test_every_cited_adr_exists() -> None:
    """A comment pointing at a missing ADR is worse than no comment.

    Twenty places in the source cited ADR-0010 and ADR-0011 while `docs/adr/`
    stopped at 0009. Each of those citations read as "this was reasoned about and
    reviewed", and neither had been — including the model-supply-chain and
    detection-budget decisions that later turned out to be wrong.
    """
    have = {int(path.name[:4]) for path in (ROOT / "docs/adr").glob("[0-9]*.md")}
    cited: dict[int, set[str]] = {}
    for root in ("src", "tests", "docs", "doc"):
        for path in (ROOT / root).rglob("*"):
            if path.suffix not in {".py", ".md", ".toml", ".yaml", ".yml"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for number in re.findall(r"ADR-(\d{4})", text):
                cited.setdefault(int(number), set()).add(str(path.relative_to(ROOT)))

    missing = {n: sorted(files) for n, files in cited.items() if n not in have}
    assert not missing, f"cited but not written: {missing}"


def test_documented_redis_command_actually_switches_the_store() -> None:
    """The Redis instructions must name the overlay, not a shell prefix.

    `SECURITYMASKER_STORE=redis docker compose --profile redis up` starts Redis
    and leaves the gateway on its in-process store: Compose uses such a variable
    for YAML substitution, not as container environment. Following it produced a
    stack that looked like shared-store mode and was not — sessions would not be
    shared, and nobody would be told.
    """
    text = (ROOT / "docs/operations.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if "docker compose" not in stripped or "--profile redis" not in stripped:
            continue
        if stripped.startswith("#") or "does **not**" in stripped:
            continue
        assert "docker-compose.redis.yml" in stripped, (
            f"documented Redis command does not apply the overlay: {stripped!r}"
        )


def test_ci_fetches_a_model_that_is_actually_pinned() -> None:
    """The release gate's fetch command must name a model we have a manifest for.

    The gate exists to prove the shipped model verifies. A fetch command that
    names something else — or, as it did once, no model at all, which exits 2 —
    makes the whole job fail or prove the wrong thing.
    """
    from securitymasker.models_fetch import MANIFESTS

    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    models = re.findall(r"--model\s+(\S+)", workflow)
    revisions = re.findall(r"--revision\s+(\S+)", workflow)
    assert models and revisions, "the release gate no longer fetches a model"

    for model, revision in zip(models, revisions, strict=True):
        assert f"{model}@{revision}" in MANIFESTS, (
            f"CI fetches {model}@{revision}, which has no manifest"
        )


def test_release_gate_can_be_reached_by_a_tag_push() -> None:
    """A job gated on refs/tags must have a workflow that tags actually start.

    With only `branches:` under `push:`, a tag push does not trigger the workflow,
    so the release gate's condition is never evaluated and the gate silently never
    runs.
    """
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    # PyYAML reads the bare key `on` as the boolean True.
    triggers = workflow.get("on") or workflow.get(True)
    gate = workflow["jobs"]["release-gate"]
    if "refs/tags/" in str(gate.get("if", "")):
        assert triggers["push"].get("tags"), (
            "release-gate is conditioned on tags, but push.tags is not configured "
            "so a tag push never starts this workflow"
        )
