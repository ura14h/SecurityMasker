"""ADR-0012 Phase 1のconfig、辞書、init、source launcher検証。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import securitymasker
from securitymasker.cli import main
from securitymasker.config import load_config, resolve_config_path
from securitymasker.errors import ConfigError

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
version: 2
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


def test_v2_loads_one_dictionary_and_resolves_paths_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = tmp_path / "layout"
    layout.mkdir()
    config_path = _write_layout(layout, mode="claude", port=4001)
    decoy = tmp_path / "elsewhere"
    decoy.mkdir()
    monkeypatch.chdir(decoy)

    config = load_config(config_path)

    assert config.version == 2
    assert config.runtime is not None
    assert config.runtime.mode == "claude"
    assert config.runtime.port == 4001
    assert config.dictionary == (layout / "securitymasker.dict").resolve()
    assert config.state is not None
    assert config.state.database == (layout / "securitymasker.state/securitymasker.db").resolve()
    assert [entry.id for entry in config.entities] == ["example_person"]


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
def test_v2_refuses_files_readable_by_other_users(tmp_path: Path, target: str) -> None:
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
def test_v2_refuses_state_directory_accessible_by_other_users(tmp_path: Path) -> None:
    config_path = _write_layout(tmp_path)
    (tmp_path / "securitymasker.state").chmod(0o755)

    with pytest.raises(ConfigError, match="unsafe permissions"):
        load_config(config_path)


def test_v2_refuses_unknown_config_and_dictionary_fields(tmp_path: Path) -> None:
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


def test_source_launcher_runs_from_unrelated_working_directory(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "securitymasker.py"), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "SecurityMasker CLI" in completed.stdout


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
    assert "--no-build-isolation --no-deps -e" in setup


def test_initialized_product_files_are_ignored_at_repository_root() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "/securitymasker.config" in gitignore
    assert "/securitymasker.dict" in gitignore
    assert "/securitymasker.state/" in gitignore


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


def test_binary_build_has_a_separate_fixed_toolchain_and_excludes_test_services() -> None:
    build = (ROOT / "scripts/build-binary").read_text(encoding="utf-8")
    lock = (ROOT / "requirements-build.lock").read_text(encoding="utf-8")
    spec = (ROOT / "securitymasker.spec").read_text(encoding="utf-8")

    assert "pyinstaller==6.21.0" in lock
    assert "requirements-build.lock" in build
    assert "requirements-dev.lock" not in build
    assert "binary build requires Python 3.12+" in build
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
    arguments: list[str],
    expected_mode: str,
    expected_port: int,
) -> None:
    config_path = _write_layout(tmp_path, mode="claude", port=4001)
    observed: dict[str, object] = {}
    monkeypatch.setenv("SECURITYMASKER_CONFIG", "test-sentinel")
    monkeypatch.setenv("SECURITYMASKER_PRODUCT_MODE", "test-sentinel")

    monkeypatch.setattr("securitymasker.gateway.app.create_app", lambda: object())

    def fake_run(app: object, **keywords: object) -> None:
        observed.update(keywords)

    monkeypatch.setattr("uvicorn.run", fake_run)

    assert main(["gateway", "--config", str(config_path), *arguments]) == 0
    assert os.environ["SECURITYMASKER_PRODUCT_MODE"] == expected_mode
    assert observed["host"] == "127.0.0.1"
    assert observed["port"] == expected_port
