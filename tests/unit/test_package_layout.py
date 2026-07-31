"""config、辞書、init、source launcherの利用者向け構成を検証する。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import securitymasker
from securitymasker import bootstrap
from securitymasker.cli import main
from securitymasker.config import load_config, resolve_config_path
from securitymasker.errors import ConfigError, SessionError
from securitymasker.sessions.sqlite import SQLiteSessionStore

ROOT = Path(__file__).resolve().parents[2]


def _write_private(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def _write_layout(root: Path, *, mode: str = "chatgpt", port: int = 4000) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    state = root / "securitymasker.state"
    state.mkdir(mode=0o700)
    _write_private(state / "securitymasker.key", "k" * 32)
    _write_private(
        root / "securitymasker.dict",
        """
version: 1
entities:
  - id: example_person
    type: PERSON
    values: ["山田太郎"]
    replacement_profile: prose_identifier
    restore_policy: literal
patterns: []
""",
    )
    config = root / "securitymasker.config"
    _write_private(
        config,
        f"""
version: 1
runtime:
  mode: {mode}
  host: 127.0.0.1
  port: {port}
state:
  database: ./securitymasker.state/securitymasker.db
  key: ./securitymasker.state/securitymasker.key
dictionary: ./securitymasker.dict
detectors:
  secrets:
    enabled: true
  formats:
    enabled: true
  japanese_ner:
    enabled: true
""",
    )
    return config


def test_loads_one_dictionary_and_resolves_paths_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = tmp_path / "layout"
    layout.mkdir()
    config_path = _write_layout(layout, mode="claude", port=4001)
    decoy = tmp_path / "elsewhere"
    decoy.mkdir()
    monkeypatch.chdir(decoy)

    config = load_config(config_path)

    assert config.version == 1
    assert config.runtime is not None
    assert config.runtime.mode == "claude"
    assert config.runtime.port == 4001
    assert config.dictionary == (layout / "securitymasker.dict").resolve()
    assert config.state is not None
    assert config.state.database == (layout / "securitymasker.state/securitymasker.db").resolve()
    assert [entry.id for entry in config.entities] == ["example_person"]


def test_removed_flat_schema_is_not_inferred_from_version_one(tmp_path: Path) -> None:
    config_path = tmp_path / "removed-flat.config"
    _write_private(
        config_path,
        """
version: 1
entities:
  - id: old
    type: PERSON
    values: ["合成人物"]
    replacement_profile: prose_identifier
""",
    )

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_discovery_does_not_read_current_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = tmp_path / "bin" / "securitymasker.py"
    launcher.parent.mkdir()
    launcher.touch()
    adjacent = _write_layout(launcher.parent)
    decoy = tmp_path / "cwd"
    decoy.mkdir()
    _write_layout(decoy, mode="claude", port=4999)
    monkeypatch.chdir(decoy)
    monkeypatch.setattr(sys, "argv", [str(launcher)])
    monkeypatch.delenv("SECURITYMASKER_CONFIG", raising=False)

    assert resolve_config_path() == adjacent.resolve()


def test_discovery_priority_is_cli_then_environment_then_adjacent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit.config"
    environment = tmp_path / "environment.config"
    launcher = tmp_path / "bin" / "securitymasker.py"
    launcher.parent.mkdir()
    adjacent = launcher.parent / "securitymasker.config"
    for path in (explicit, environment, adjacent):
        path.touch()
    monkeypatch.setattr(sys, "argv", [str(launcher)])
    monkeypatch.setenv("SECURITYMASKER_CONFIG", str(environment))

    assert resolve_config_path(explicit) == explicit.resolve()
    assert resolve_config_path() == environment.resolve()
    monkeypatch.delenv("SECURITYMASKER_CONFIG")
    assert resolve_config_path() == adjacent.resolve()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
@pytest.mark.parametrize("target", ["config", "dictionary", "key"])
def test_refuses_files_readable_by_other_users(tmp_path: Path, target: str) -> None:
    config_path = _write_layout(tmp_path)
    paths = {
        "config": config_path,
        "dictionary": tmp_path / "securitymasker.dict",
        "key": tmp_path / "securitymasker.state/securitymasker.key",
    }
    paths[target].chmod(0o644)

    with pytest.raises(ConfigError, match="unsafe permissions"):
        load_config(config_path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_refuses_state_directory_accessible_by_other_users(tmp_path: Path) -> None:
    config_path = _write_layout(tmp_path)
    (tmp_path / "securitymasker.state").chmod(0o755)

    with pytest.raises(ConfigError, match="unsafe permissions"):
        load_config(config_path)


def test_refuses_unknown_config_and_dictionary_fields(tmp_path: Path) -> None:
    config_path = _write_layout(tmp_path)
    with config_path.open("a", encoding="utf-8") as stream:
        stream.write("unknown: true\n")
    with pytest.raises(ConfigError):
        load_config(config_path)

    config_path = _write_layout(tmp_path / "second")
    dictionary = tmp_path / "second/securitymasker.dict"
    with dictionary.open("a", encoding="utf-8") as stream:
        stream.write("unknown: true\n")
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_init_creates_private_config_dictionary_and_key_but_not_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "SecurityMasker"
    assert main(["init", "--directory", str(target), "--mode", "claude", "--port", "4001"]) == 0
    output = capsys.readouterr()

    config_path = target / "securitymasker.config"
    dictionary = target / "securitymasker.dict"
    state = target / "securitymasker.state"
    key = state / "securitymasker.key"
    assert config_path.is_file()
    assert dictionary.is_file()
    assert state.is_dir()
    assert len(key.read_bytes()) == 32
    assert not (state / "securitymasker.db").exists()
    assert "state database will be created" in output.out
    assert key.read_bytes().hex() not in output.out + output.err

    config = load_config(config_path)
    assert config.runtime is not None
    assert (config.runtime.mode, config.runtime.port) == ("claude", 4001)
    if os.name == "posix":
        assert (config_path.stat().st_mode & 0o777) == 0o600
        assert (dictionary.stat().st_mode & 0o777) == 0o600
        assert (key.stat().st_mode & 0o777) == 0o600
        assert (state.stat().st_mode & 0o777) == 0o700


@pytest.mark.parametrize(
    "existing",
    [
        "securitymasker.config",
        "securitymasker.dict",
        "securitymasker.state",
    ],
)
def test_init_refuses_existing_targets_without_overwriting(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    existing: str,
) -> None:
    target = tmp_path / "SecurityMasker"
    target.mkdir()
    path = target / existing
    if existing == "securitymasker.state":
        path.mkdir()
    else:
        path.write_text("sentinel\n", encoding="utf-8")

    assert main(["init", "--directory", str(target)]) == 1
    captured = capsys.readouterr()
    assert "already exists" in captured.err
    if path.is_file():
        assert path.read_text(encoding="utf-8") == "sentinel\n"
    assert not (target / "securitymasker.state/securitymasker.key").exists()


def test_init_force_requires_explicit_directory(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["init", "--force"]) == 1

    captured = capsys.readouterr()
    assert "requires an explicit --directory" in captured.err


def test_init_force_replaces_config_dictionary_database_and_key(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "SecurityMasker"
    assert main(["init", "--directory", str(target)]) == 0
    capsys.readouterr()
    dictionary = target / "securitymasker.dict"
    state = target / "securitymasker.state"
    database = state / "securitymasker.db"
    key = state / "securitymasker.key"
    old_key = key.read_bytes()
    dictionary.write_text("synthetic discarded dictionary\n", encoding="utf-8")
    dictionary.chmod(0o600)
    store = SQLiteSessionStore(database, key, mode="chatgpt")
    store.close()
    assert database.is_file()

    assert main([
        "init",
        "-f",
        "--directory",
        str(target),
        "--mode",
        "claude",
        "--port",
        "4001",
    ]) == 0
    captured = capsys.readouterr()

    assert "reset SecurityMasker" in captured.out
    assert "sessions, aliases and master key were deleted" in captured.out
    assert old_key.hex() not in captured.out + captured.err
    assert key.read_bytes() != old_key
    assert not database.exists()
    assert {path.name for path in state.iterdir()} == {"securitymasker.key"}
    assert "synthetic discarded dictionary" not in dictionary.read_text(encoding="utf-8")
    config = load_config(target / "securitymasker.config")
    assert config.runtime is not None
    assert (config.runtime.mode, config.runtime.port) == ("claude", 4001)
    if os.name == "posix":
        assert ((target / "securitymasker.config").stat().st_mode & 0o777) == 0o600
        assert (dictionary.stat().st_mode & 0o777) == 0o600
        assert (key.stat().st_mode & 0o777) == 0o600
        assert (state.stat().st_mode & 0o777) == 0o700


def test_init_force_does_not_follow_paths_from_old_config(
    tmp_path: Path,
) -> None:
    target = tmp_path / "SecurityMasker"
    target.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    external_dictionary = external / "custom.dict"
    external_database = external / "custom.db"
    external_key = external / "custom.key"
    external_dictionary.write_text("external dictionary\n", encoding="utf-8")
    external_database.write_bytes(b"external database")
    external_key.write_bytes(b"e" * 32)
    config = target / "securitymasker.config"
    config.write_text(
        "\n".join([
            "version: 1",
            "runtime: {mode: chatgpt}",
            f"state: {{database: {external_database}, key: {external_key}}}",
            f"dictionary: {external_dictionary}",
        ]),
        encoding="utf-8",
    )

    assert main(["init", "--force", "--directory", str(target)]) == 0

    assert external_dictionary.read_text(encoding="utf-8") == "external dictionary\n"
    assert external_database.read_bytes() == b"external database"
    assert external_key.read_bytes() == b"e" * 32
    assert load_config(target / "securitymasker.config").dictionary == (
        target / "securitymasker.dict"
    )


def test_init_force_refuses_state_owned_by_running_gateway(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "SecurityMasker"
    assert main(["init", "--directory", str(target)]) == 0
    capsys.readouterr()
    database = target / "securitymasker.state/securitymasker.db"
    key = target / "securitymasker.state/securitymasker.key"
    old_key = key.read_bytes()
    store = SQLiteSessionStore(database, key, mode="chatgpt")
    try:
        assert main(["init", "--force", "--directory", str(target)]) == 1
    finally:
        store.close()

    captured = capsys.readouterr()
    assert "state is in use" in captured.err
    assert key.read_bytes() == old_key
    assert database.is_file()


def test_init_force_refuses_unmanaged_state_entry(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "SecurityMasker"
    assert main(["init", "--directory", str(target)]) == 0
    capsys.readouterr()
    unmanaged = target / "securitymasker.state/do-not-delete.txt"
    unmanaged.write_text("preserve me\n", encoding="utf-8")

    assert main(["init", "--force", "--directory", str(target)]) == 1

    captured = capsys.readouterr()
    assert "unmanaged entry" in captured.err
    assert unmanaged.read_text(encoding="utf-8") == "preserve me\n"


@pytest.mark.skipif(os.name != "posix", reason="symlink safety contract")
def test_init_force_refuses_managed_symlink(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "SecurityMasker"
    target.mkdir()
    outside = tmp_path / "outside.config"
    outside.write_text("preserve me\n", encoding="utf-8")
    (target / "securitymasker.config").symlink_to(outside)

    assert main(["init", "--force", "--directory", str(target)]) == 1

    captured = capsys.readouterr()
    assert "must not be a symlink" in captured.err
    assert outside.read_text(encoding="utf-8") == "preserve me\n"


def test_init_force_rolls_back_previous_layout_when_switch_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "SecurityMasker"
    initialize = bootstrap.initialize_layout(target, mode="chatgpt", port=4000)
    previous = {
        initialize.config: initialize.config.read_bytes(),
        initialize.dictionary: initialize.dictionary.read_bytes(),
        initialize.state_directory / "securitymasker.key": (
            initialize.state_directory / "securitymasker.key"
        ).read_bytes(),
    }
    database = initialize.state_directory / "securitymasker.db"
    database.write_bytes(b"synthetic previous database")
    database.chmod(0o600)
    previous[database] = database.read_bytes()
    original_replace = os.replace
    replace_calls = 0

    def fail_during_install(source: str | Path, destination: str | Path) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 5:
            raise OSError("synthetic switch failure")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_during_install)

    with pytest.raises(ConfigError, match="initialization failed"):
        bootstrap.initialize_layout(target, mode="claude", port=4001, force=True)

    for path, content in previous.items():
        assert path.read_bytes() == content
    assert not tuple(target.glob(".securitymasker-init-*"))
    assert not tuple(target.glob(".securitymasker-reset-*"))


def test_source_launcher_without_command_shows_help_from_unrelated_directory(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "securitymasker.py")],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "SecurityMasker CLI" in completed.stdout
    assert "gateway_started" not in completed.stderr


def test_release_version_is_consistent() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert securitymasker.__version__ == "0.1.0"
    assert 'version = "0.1.0"' in pyproject
    assert "## 0.1.0" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_source_packaging_script_is_local_and_reproducible() -> None:
    script = (ROOT / "scripts/package-source").read_text(encoding="utf-8")

    assert "git -C" in script
    assert "archive" in script
    assert "gzip -n" in script
    assert "sha256sum" in script
    assert "shasum -a 256" in script
    assert "curl" not in script
    assert "gh " not in script


def test_standard_setup_does_not_install_redis() -> None:
    setup = (ROOT / "scripts/setup").read_text(encoding="utf-8")
    standard_lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")

    assert "requirements.lock" in setup
    assert "requirements-redis.lock" not in setup
    assert "requirements-dev.lock" not in setup
    assert "pytest" not in setup
    assert "\nredis==" not in f"\n{standard_lock}"
    assert "python3.12" in setup
    assert "python3.11" in setup
    assert "requires Python 3.11+" in setup
    assert "--no-build-isolation --no-deps -e" in setup
    assert 'if [ "$(uname -s)" = "Linux" ]' in setup
    assert "requirements-torch-cpu.lock" in setup

    cpu_lock = (ROOT / "requirements-torch-cpu.lock").read_text(encoding="utf-8")
    assert "https://download.pytorch.org/whl/cpu" in cpu_lock
    assert "torch==2.13.0+cpu" in cpu_lock


def test_source_package_declares_python_311_minimum() -> None:
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'requires-python = ">=3.11"' in project
    assert 'target-version = "py311"' in project
    assert 'python_version = "3.11"' in project


def test_initialized_product_files_are_ignored_at_repository_root() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "/*.config" in gitignore
    assert "/*.dict" in gitignore
    assert "/*.state/" in gitignore


def test_test_setup_and_local_release_gate_are_separate_from_user_setup() -> None:
    test_setup = (ROOT / "scripts/test-setup").read_text(encoding="utf-8")
    release = (ROOT / "scripts/release-check").read_text(encoding="utf-8")

    assert '"$SCRIPT_DIRECTORY/setup"' in test_setup
    assert "requirements-dev.lock" in test_setup
    for required in (
        "ruff",
        "mypy",
        "tests/unit tests/evaluation",
        "test_live_gateway.py",
        "run_cli_e2e.sh",
        "SM_REQUIRE_MODEL=1",
        "SM_REQUIRE_ALL_CLIS=1",
    ):
        assert required in release
    assert "|| true" not in release


def test_linux_arm64_docker_gate_separates_online_and_isolated_phases() -> None:
    runner = (ROOT / "devtools/run_linux_arm64_release_gate.sh").read_text(
        encoding="utf-8"
    )
    dockerfile = (ROOT / "docker/Dockerfile.release-gate").read_text(
        encoding="utf-8"
    )
    dockerignore = (ROOT / "docker/Dockerfile.release-gate.dockerignore").read_text(
        encoding="utf-8"
    )

    for required in (
        "--platform linux/arm64",
        "SM_OPENAI_E2E_COMPARE_HTTP=1",
        "--network none",
        "SM_REQUIRE_ALL_CLIS=1",
        "readonly",
    ):
        assert required in runner
    assert runner.index("SM_OPENAI_E2E_COMPARE_HTTP=1") < runner.rindex("--network none")
    assert "@openai/codex@0.145.0" in dockerfile
    assert "@anthropic-ai/claude-code@2.1.212" in dockerfile
    assert "python:3.12.13-slim-bookworm@sha256:" in dockerfile
    assert "node:22.16.0-bookworm-slim@sha256:" in dockerfile
    assert "securitymasker.config" not in dockerignore
    assert "securitymasker.dict" not in dockerignore


def test_linux_arm64_binary_gate_builds_and_tests_a_python_free_runtime() -> None:
    runner = (ROOT / "devtools/run_linux_arm64_binary_gate.sh").read_text(
        encoding="utf-8"
    )
    dockerfile = (ROOT / "docker/Dockerfile.binary-gate").read_text(
        encoding="utf-8"
    )
    binary_test = (ROOT / "scripts/test-binary").read_text(encoding="utf-8")

    for required in (
        "--platform linux/arm64",
        "--network none",
        "--read-only",
        "--tmpfs /tmp:rw,exec,mode=1777",
        "/sys/class/net/*",
        "/proc/net/route",
        "/proc/net/ipv6_route",
        "command -v python3",
        "docker cp",
        "shasum -a 256",
    ):
        assert required in runner
    assert "./scripts/build-binary" in dockerfile
    assert "./scripts/test-binary" in dockerfile
    assert "FROM docker.io/library/debian:bookworm-slim@sha256:" in dockerfile
    assert "command -v python3" in dockerfile
    assert 'ENTRYPOINT ["securitymasker"]' in dockerfile
    assert 'PYTHON=${PYTHON:-"$PROJECT_DIRECTORY/.venv/bin/python"}' in binary_test


def test_binary_build_has_a_separate_fixed_toolchain_and_excludes_test_services() -> None:
    build = (ROOT / "scripts/build-binary").read_text(encoding="utf-8")
    lock = (ROOT / "requirements-build.lock").read_text(encoding="utf-8")
    spec = (ROOT / "securitymasker.spec").read_text(encoding="utf-8")

    assert "pyinstaller==6.21.0" in lock
    assert "requirements-build.lock" in build
    assert "requirements-torch-cpu.lock" in build
    assert "requirements-dev.lock" not in build
    assert "binary build requires Python 3.11+" in build
    assert "--no-build-isolation --no-deps -e" in build
    assert "securitymasker.spec" in build
    assert "securitymasker_model" in spec
    assert 'sys.path.insert(0, str(source))' in spec
    assert 'securitymasker/_binary_entry.py' in spec
    binary_test = (ROOT / "scripts/test-binary").read_text(encoding="utf-8")
    assert "tests/integration/test_binary_release.py" in binary_test
    assert 'SM_BINARY="$BINARY"' in binary_test
    for excluded in ("devtools", "pytest", "presidio_analyzer", "spacy"):
        assert f'"{excluded}"' in spec
    for removed in (
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.redis.yml",
        "requirements-redis.lock",
        ".github/workflows/ci.yml",
        "src/securitymasker/resources/securitymasker.example.yaml",
    ):
        assert not (ROOT / removed).exists()

    help_result = subprocess.run(
        [sys.executable, str(ROOT / "securitymasker.py"), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0
    assert "\n    run " not in help_result.stdout
    assert "\n    sessions " not in help_result.stdout


@pytest.mark.parametrize(
    ("arguments", "expected_mode", "expected_port"),
    [
        ([], "claude", 4001),
        (["--mode", "chatgpt", "--port", "4555"], "chatgpt", 4555),
    ],
)
def test_gateway_cli_runtime_overrides_take_priority_over_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    expected_mode: str,
    expected_port: int,
) -> None:
    config_path = _write_layout(tmp_path, mode="claude", port=4001)
    observed: dict[str, object] = {}
    monkeypatch.setenv("SECURITYMASKER_CONFIG", "test-sentinel")
    monkeypatch.setenv("SECURITYMASKER_PRODUCT_MODE", "test-sentinel")

    monkeypatch.setattr("securitymasker.gateway.app.create_app", lambda: object())

    def fake_serve(app: object, **keywords: object) -> int:
        observed["app"] = app
        observed.update(keywords)
        return 0

    monkeypatch.setattr("securitymasker.cli._serve_gateway", fake_serve)

    assert main(["gateway", "--config", str(config_path), *arguments]) == 0
    assert os.environ["SECURITYMASKER_PRODUCT_MODE"] == expected_mode
    assert observed["host"] == "127.0.0.1"
    assert observed["port"] == expected_port
    assert observed["mode"] == expected_mode
    assert capsys.readouterr().err == ""


def test_gateway_uses_configured_console_log_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECURITYMASKER_CONFIG", "test-sentinel")
    monkeypatch.setenv("SECURITYMASKER_PRODUCT_MODE", "test-sentinel")
    config_path = _write_layout(tmp_path)
    text = config_path.read_text(encoding="utf-8").replace(
        "runtime:\n", "logging:\n  level: ERROR\n\nruntime:\n"
    )
    config_path.write_text(text, encoding="utf-8")
    configured: list[str] = []

    monkeypatch.setattr("securitymasker.gateway.app.create_app", lambda: object())
    monkeypatch.setattr(
        "securitymasker.cli.configure_logging",
        lambda level="INFO": configured.append(level),
    )
    monkeypatch.setattr("securitymasker.cli._serve_gateway", lambda *args, **kwargs: 0)

    assert main(["gateway", "--config", str(config_path)]) == 0
    assert configured == ["INFO", "ERROR"]


def test_gateway_configuration_failure_is_error_without_start(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_layout(tmp_path)
    text = config_path.read_text(encoding="utf-8").replace(
        "version: 1", "version: 999", 1
    )
    config_path.write_text(text, encoding="utf-8")

    assert main(["gateway", "--config", str(config_path)]) == 1

    output = capsys.readouterr().err
    assert "[error] gateway_configuration_error" in output
    assert "gateway_started" not in output


def test_gateway_store_initialization_failure_is_error_without_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SECURITYMASKER_CONFIG", "test-sentinel")
    monkeypatch.setenv("SECURITYMASKER_PRODUCT_MODE", "test-sentinel")
    config_path = _write_layout(tmp_path)

    def fail_create_app() -> object:
        raise SessionError("synthetic SQLite initialization failure")

    monkeypatch.setattr("securitymasker.gateway.app.create_app", fail_create_app)

    assert main(["gateway", "--config", str(config_path)]) == 1

    output = capsys.readouterr().err
    assert "[error] gateway_store_error" in output
    assert "gateway_started" not in output
