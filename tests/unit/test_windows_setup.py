"""Windows nativeのcmd setupとdependency lock契約を検査する。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_windows_setup_is_cmd_native_wheel_only_and_python_312() -> None:
    setup = (ROOT / "scripts/setup.cmd").read_text(encoding="utf-8")
    test_setup = (ROOT / "scripts/test-setup.cmd").read_text(encoding="utf-8")
    release = (ROOT / "scripts/release-check.cmd").read_text(encoding="utf-8")
    runtime_lock = (ROOT / "requirements-windows.lock").read_text(encoding="utf-8")
    dev_lock = (ROOT / "requirements-windows-dev.lock").read_text(encoding="utf-8")

    assert "py -3.12" in setup
    assert "sys.version_info[:2] == (3, 12)" in setup
    assert "requirements-windows.lock" in setup
    assert "--only-binary=:all:" in setup
    assert "--no-build-isolation --no-deps -e" in setup
    assert "model-load" in setup
    assert "requirements-windows-dev.lock" in test_setup
    assert "--no-deps" in test_setup
    assert "requirements-dev.lock" not in test_setup
    assert "SM_REQUIRE_MODEL=1" in release
    assert "test_live_gateway.py" in release
    assert "optional extended compatibility gate" in release
    assert "torch==2.13.0" in runtime_lock
    assert "colorama==0.4.6" in runtime_lock
    assert "pytest==8.4.2" in dev_lock
    assert "torch==" not in dev_lock


def test_common_install_guide_activates_windows_venv_without_command_alias() -> None:
    """Windows導入を共通ガイドへ統合し、独自のSM短縮変数を使わない。"""
    guide = (ROOT / "docs/getting-started.md").read_text(encoding="utf-8")

    assert ".venv\\Scripts\\activate.bat" in guide
    assert "SECURITYMASKER_CONFIG" not in guide
    assert 'set "SM=' not in guide
    assert "%SM%" not in guide
    assert not (ROOT / "docs/guides/windows-native-source.md").exists()


def test_windows_source_protects_managed_artifacts_without_rewriting_source_root() -> None:
    """WindowsもPOSIXと同じartifact単位の機密境界を使う。"""
    bootstrap = (ROOT / "src/securitymasker/bootstrap.py").read_text(encoding="utf-8")
    config = (ROOT / "src/securitymasker/config.py").read_text(encoding="utf-8")

    assert "_windows_secure(root" not in bootstrap
    assert "_windows_secure(state_directory, directory=True)" in bootstrap
    assert "_windows_secure(path, directory=False)" in bootstrap
    assert "_require_private_directory(config_path.parent" not in config


def test_windows_source_packaging_requires_clean_tree_and_never_overwrites() -> None:
    package = (ROOT / "scripts/package-source.cmd").read_text(encoding="utf-8")

    assert "if defined SECURITYMASKER_PYTHON" in package
    assert 'set "PYTHON=%SECURITYMASKER_PYTHON%"' in package
    assert "git -C \"%PROJECT_DIRECTORY%\" diff --quiet" in package
    assert "git -C \"%PROJECT_DIRECTORY%\" diff --cached --quiet" in package
    assert "git -C \"%PROJECT_DIRECTORY%\" archive --format=tar.gz" in package
    assert '--prefix="securitymasker-%VERSION%/"' in package
    assert "hashlib.sha256" in package
    assert "runpy.run_path" in package
    assert 'if exist "%VERSION_FILE%" goto version_file_exists' in package
    assert 'del /q "%VERSION_FILE%"' in package
    assert "securitymasker.py\" --version" not in package
    assert 'if exist "%ARCHIVE%" goto exists' in package
    assert 'if exist "%CHECKSUM%" goto exists' in package


def test_windows_binary_build_and_profile_gate_are_native_and_fixed() -> None:
    build = (ROOT / "scripts/build-binary.cmd").read_text(encoding="utf-8")
    test = (ROOT / "scripts/test-binary.cmd").read_text(encoding="utf-8")
    gate = (ROOT / "scripts/windows-binary-gate.cmd").read_text(encoding="utf-8")
    lock = (ROOT / "requirements-windows-build.lock").read_text(encoding="utf-8")

    assert "requirements-windows.lock" in build
    assert "requirements-windows-build.lock" in build
    assert "--only-binary=:all: --no-deps" in build
    assert "sys.version_info[:2] == (3, 12)" in build
    assert "struct.calcsize('P') == 8" in build
    assert "securitymasker-%PROFILE%.exe" in build
    assert "SECURITYMASKER_BINARY_PROFILE=%PROFILE%" in build
    assert "securitymasker.spec" in build
    assert "pyinstaller==6.21.0" in lock
    assert "pefile==2024.8.26" in lock
    assert "pywin32-ctypes==0.2.3" in lock
    assert "tests\\integration\\test_binary_release.py" in test
    assert "SM_BINARY_PROFILE=%PROFILE%" in test
    assert "SM_BINARY_WINDOWS_TEMP_ROOT" in test
    assert "model-load" in test
    assert 'build-binary.cmd" --profile lite' in gate
    assert 'test-binary.cmd" --profile lite' in gate
    assert 'build-binary.cmd" --profile full' in gate
    assert 'test-binary.cmd" --profile full' in gate
