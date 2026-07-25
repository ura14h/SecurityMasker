# SecurityMasker

ローカルの Codex / Claude Code から外部 LLM（OpenAI / Anthropic）へ機密情報を送る前に、
セッション単位の安定した仮名（alias）へ**可逆置換**し、レスポンスをローカルで復元する
**可逆マスキング・セキュリティ境界**（透過プロキシ）。

- 初期ブリーフ（方針）: [`doc/00-First-Order.md`](doc/00-First-Order.md)
- 実装計画: [`doc/01-Plan.md`](doc/01-Plan.md) / アーキテクチャ転換: [`docs/adr/0006-drop-litellm-purpose-built-proxy.md`](docs/adr/0006-drop-litellm-purpose-built-proxy.md)
- 開発エージェント向けルール: [`AGENTS.md`](AGENTS.md) / 運用: [`docs/operations.md`](docs/operations.md)
- **セキュリティ是正の到達状況と既知の制限**: [`doc/07-Remediation-Status.md`](doc/07-Remediation-Status.md)
  （実装済み／partial／deferred を明示。URL/パスの完全な構造保持や未登録名の NER は未実装）

> ⚠️ `SECURITYMASKER_CONFIG`（マスキング辞書）は**必須**です。未設定だと起動に失敗します
> （fail-closed）。マスキングなしの開発モードは `SECURITYMASKER_DEV_TRANSPARENT=1` を明示した
> ときだけで、実プロバイダーには決して向けないでください。

> ✅ Codex（OpenAI Responses）と Claude Code（Anthropic Messages）を、**自作の薄い透過プロキシ**
> （Starlette+httpx、LiteLLM 非依存 — [ADR-0006](docs/adr/0006-drop-litellm-purpose-built-proxy.md)）で
> マスク・復元します（非ストリーム／ストリーム）。認証はクライアントの資格情報を**透過パススルー**
> （ChatGPT OAuth も可・検証済み）。応答ストリーミング復元も動作します。

## しくみ

```
Codex / Claude Code ──▶ SecurityMasker Proxy ──▶ OpenAI / Anthropic / ChatGPT backend
   ▲  復元済み応答          mask 要求 / restore 応答        （マスク済みデータのみ到達）
   └───────────────────────  認証は素通し（保存・ログしない）
```

## マスキングを試す（CLI・外部送信なし）

```bash
export SECURITYMASKER_CONFIG=config/securitymasker.example.yaml
securitymasker entities test "株式会社極秘技研の山田太郎です。 key=sk-abcdefghijklmnopqrstuvwxyz0"
```

```text
入力:   株式会社極秘技研の山田太郎です。 key=sk-...
マスク: SM_ORG_2121B2のSM_PERSON_81FEB6です。 key=${SECURITYMASKER_SECRET_5F7B78}
復元:   このセッションで生成した alias のみをローカルで復元（SM_ORG_→株式会社極秘技研 等）。
        API キー/秘密鍵は実値へ戻さず ${SECURITYMASKER_SECRET_...} のまま（§10, §27）。
```

- 同一セッションでは同じ機密値が常に同じ alias、別セッションでは別 alias（§6）。

## プロキシを起動して Codex/Claude Code をつなぐ

```bash
export SECURITYMASKER_CONFIG=config/securitymasker.example.yaml
securitymasker gateway --port 4000            # 透過マスキングプロキシ
```

- **Codex**: `~/.codex/config.toml` に securitymasker プロバイダを追加（`base_url="http://127.0.0.1:4000"`,
  `wire_api="responses"`, `requires_openai_auth=true`）。生成例は
  `python -c "from securitymasker.integrations.codex import codex_config_toml; print(codex_config_toml())"`。
- **Claude Code**: `ANTHROPIC_BASE_URL=http://127.0.0.1:4000` ＋ セッションヘッダ。
- ラッパー: `securitymasker run codex` / `securitymasker run claude`（セッション UUID を発行して起動）。

## 開発環境セットアップ（pip + venv）

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
# 任意: 日本語 NER（Presidio + spaCy）
pip install -e ".[presidio]" && python -m spacy download ja_core_news_md
```

固定バージョンは [`docs/compatibility.md`](docs/compatibility.md)、置いた前提は
[`docs/adr/`](docs/adr/) を参照。
