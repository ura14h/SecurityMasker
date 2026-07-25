"""Set up an ISOLATED environment for the real Codex E2E (method B: reuse session).

Safety: this only *reads* ``~/.codex/auth.json`` and never writes anything under
``~/.codex`` or ``~/.config/litellm``. It builds a throwaway directory with:

  <e2e>/codex_home/          — CODEX_HOME for the test Codex (copied auth.json +
                               a minimal config.toml pointing at the gateway)
  <e2e>/litellm_chatgpt/     — CHATGPT_TOKEN_DIR for LiteLLM (tokens reformatted
                               from Codex's auth.json into LiteLLM's flat layout)

Token values are never printed. Run:  python scripts/codex_e2e_setup.py
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

E2E_DIR = Path(os.environ.get("SM_CODEX_E2E_DIR", "/private/tmp/sm-codex-e2e"))
REAL_CODEX = Path.home() / ".codex"
GATEWAY_URL = "http://127.0.0.1:4000/v1"

MINIMAL_CONFIG_TOML = f"""\
# Minimal isolated Codex config for the SecurityMasker E2E. No plugins/MCP.
model = "securitymasker-codex"
model_provider = "securitymasker"

[model_providers.securitymasker]
name = "SecurityMasker Gateway"
base_url = "{GATEWAY_URL}"
env_key = "LITELLM_API_KEY"
wire_api = "responses"
supports_websockets = false

[model_providers.securitymasker.env_http_headers]
X-SecurityMasker-Session-ID = "SECURITYMASKER_SESSION_ID"
"""


def _secure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)
    os.chmod(p, stat.S_IRWXU)  # 700


def _secure_file(p: Path) -> None:
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)  # 600


def main() -> int:
    real_auth = REAL_CODEX / "auth.json"
    if not real_auth.exists():
        print(f"error: {real_auth} not found (is Codex logged in?)", file=sys.stderr)
        return 1

    # Guard: we must never write inside the real Codex home.
    if E2E_DIR.resolve() == REAL_CODEX.resolve() or REAL_CODEX in E2E_DIR.resolve().parents:
        print("error: refusing to place the test env inside ~/.codex", file=sys.stderr)
        return 1

    codex_home = E2E_DIR / "codex_home"
    litellm_dir = E2E_DIR / "litellm_chatgpt"
    _secure_dir(E2E_DIR)
    _secure_dir(codex_home)
    _secure_dir(litellm_dir)

    # 1) Copy auth.json into the isolated CODEX_HOME (read from real, write to copy).
    shutil.copyfile(real_auth, codex_home / "auth.json")
    _secure_file(codex_home / "auth.json")

    # 2) Minimal config.toml pointing Codex at the gateway.
    (codex_home / "config.toml").write_text(MINIMAL_CONFIG_TOML, encoding="utf-8")

    # 3) Reformat Codex tokens into LiteLLM's flat auth.json (no values printed).
    codex_auth = json.loads(real_auth.read_text(encoding="utf-8"))
    tokens = codex_auth.get("tokens", {})
    flat = {
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "account_id": tokens.get("account_id"),
        "id_token": tokens.get("id_token"),
    }
    missing = [k for k, v in flat.items() if not v]
    if missing:
        print(f"error: Codex auth.json missing token fields: {missing}", file=sys.stderr)
        return 1
    (litellm_dir / "auth.json").write_text(json.dumps(flat), encoding="utf-8")
    _secure_file(litellm_dir / "auth.json")

    print("Isolated Codex E2E environment ready (no changes to ~/.codex):")
    print(f"  CODEX_HOME        = {codex_home}")
    print(f"  CHATGPT_TOKEN_DIR = {litellm_dir}")
    print()
    print("Terminal 1 — gateway:")
    print("  export SECURITYMASKER_CONFIG=config/securitymasker.example.yaml")
    print(f"  export CHATGPT_TOKEN_DIR={litellm_dir}")
    print("  export LITELLM_API_KEY=local-dummy-key")
    print("  .venv/bin/litellm --config config/litellm.codex-e2e.yaml --port 4000")
    print()
    print("Terminal 2 — Codex against the isolated home + gateway:")
    print(f"  export CODEX_HOME={codex_home}")
    print("  export LITELLM_API_KEY=local-dummy-key")
    print("  export SECURITYMASKER_SESSION_ID=$(uuidgen)")
    print('  codex exec "山田太郎さん向けに株式会社極秘技研のprod-db01.internal.exampleへ'
          '接続するPythonを書いて"')
    print()
    print(f"Cleanup when done:  rm -rf {E2E_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
