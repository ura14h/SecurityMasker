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
    assert "separate required gate" in release
    assert "torch==2.13.0" in runtime_lock
    assert "colorama==0.4.6" in runtime_lock
    assert "pytest==8.4.2" in dev_lock
    assert "torch==" not in dev_lock


def test_windows_source_packaging_requires_clean_tree_and_never_overwrites() -> None:
    package = (ROOT / "scripts/package-source.cmd").read_text(encoding="utf-8")

    assert "git -C \"%PROJECT_DIRECTORY%\" diff --quiet" in package
    assert "git -C \"%PROJECT_DIRECTORY%\" diff --cached --quiet" in package
    assert "git -C \"%PROJECT_DIRECTORY%\" archive --format=tar.gz" in package
    assert '--prefix="securitymasker-%VERSION%/"' in package
    assert "hashlib.sha256" in package
    assert 'if exist "%ARCHIVE%" goto exists' in package
    assert 'if exist "%CHECKSUM%" goto exists' in package
    assert "--version" in package
