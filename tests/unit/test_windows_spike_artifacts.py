"""Windows向けLinux-hosted technical spikeの配布契約を検査する。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKER = ROOT / "docker"
MODEL = "tsmatz/xlm-roberta-ner-japanese"
REVISION = "aba094e118d5ffc622e9b25e07edc49f9dd85feb"


def _compose(mode: str) -> dict[str, Any]:
    path = DOCKER / f"compose.{mode}.yaml"
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _volume(service: dict[str, Any], source: str) -> dict[str, Any]:
    return next(volume for volume in service["volumes"] if volume["source"] == source)


def test_mode_specific_compose_files_keep_product_state_separate() -> None:
    expected = {
        "chatgpt": ("securitymasker-chatgpt", "chatgpt", "4000"),
        "claude": ("securitymasker-claude", "claude", "4001"),
    }

    for mode, (project, product_mode, port) in expected.items():
        compose = _compose(mode)
        assert compose["name"] == project
        assert set(compose["services"]) == {"model-setup", "init", "gateway"}
        assert set(compose["volumes"]) == {"model-cache", "product-data"}

        init = compose["services"]["init"]
        assert init["profiles"] == ["setup"]
        assert init["network_mode"] == "none"
        assert init["command"][-3:] == [product_mode, "--port", port]
        assert _volume(init, "product-data")["target"] == (
            "/var/lib/securitymasker-product"
        )


def test_compose_runtime_preserves_loopback_and_container_hardening() -> None:
    for mode, port in (("chatgpt", "4000"), ("claude", "4001")):
        gateway = _compose(mode)["services"]["gateway"]

        assert gateway["network_mode"] == "host"
        assert "ports" not in gateway
        assert gateway["user"] == "10001:10001"
        assert gateway["read_only"] is True
        assert gateway["cap_drop"] == ["ALL"]
        assert gateway["security_opt"] == ["no-new-privileges:true"]
        assert gateway["init"] is True
        assert gateway["restart"] == "unless-stopped"
        assert gateway["stop_signal"] == "SIGTERM"
        assert gateway["command"] == [
            "gateway",
            "--config",
            "/var/lib/securitymasker-product/securitymasker.config",
        ]
        assert _volume(gateway, "model-cache")["read_only"] is True
        assert f"127.0.0.1:{port}/ready" in gateway["healthcheck"]["test"][-1]
        assert gateway["environment"]["HF_HUB_OFFLINE"] == "1"
        assert gateway["environment"]["TRANSFORMERS_OFFLINE"] == "1"
        assert "privileged" not in gateway


def test_compose_setup_uses_pinned_model_and_no_secret_environment() -> None:
    secret_names = {"MASTER_KEY", "API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}

    for mode in ("chatgpt", "claude"):
        compose = _compose(mode)
        model_setup = compose["services"]["model-setup"]
        assert model_setup["profiles"] == ["setup"]
        assert MODEL in model_setup["command"]
        assert REVISION in model_setup["command"]

        for service in compose["services"].values():
            assert secret_names.isdisjoint(service.get("environment", {}))
            assert service["image"] == "securitymasker-windows-spike:0.1.0"
            assert service["build"]["context"] == ".."
            assert service["build"]["dockerfile"] == "docker/Dockerfile"


def test_dockerfile_uses_fixed_linux_base_and_non_root_runtime() -> None:
    dockerfile = (DOCKER / "Dockerfile").read_text(encoding="utf-8")

    assert (
        "python:3.12.13-slim-bookworm@"
        "sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b"
    ) in dockerfile
    assert "requirements-torch-cpu.lock" in dockerfile
    assert "requirements-ner.lock" in dockerfile
    assert "dockerfile:1" not in dockerfile
    assert "COPY . " not in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'ENTRYPOINT ["securitymasker"]' in dockerfile


def test_windows_extra_guide_redirects_native_users_and_keeps_volume_warning() -> None:
    guide = (ROOT / "docs/unsupported/windows-evaluation.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "Windows native source版は限定条件で対応",
        "Windows native source版の導入手順",
        "この番外編",
        "評価版の免責",
        "実データの投入は利用者自身の判断と責任",
        "Windows directoryをbind mount",
        "docker/compose.chatgpt.yaml",
        "docker/compose.claude.yaml",
        "down --volumes",
        "ADR-0015",
    ):
        assert required in guide
