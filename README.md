# SecurityMasker

ローカルの Codex / Claude Code から外部 LLM（OpenAI / Anthropic）へ機密情報を送る前に、
セッション単位の安定した仮名（alias）へ**可逆置換**し、レスポンスをローカルで復元する
**可逆マスキング・セキュリティ境界**（透過プロキシ）。

- 初期ブリーフ（方針）: [`doc/00-First-Order.md`](doc/00-First-Order.md)
- 実装計画: [`doc/01-Plan.md`](doc/01-Plan.md) / アーキテクチャ転換: [`docs/adr/0006-drop-litellm-purpose-built-proxy.md`](docs/adr/0006-drop-litellm-purpose-built-proxy.md)
- 開発エージェント向けルール: [`AGENTS.md`](AGENTS.md) / 運用: [`docs/operations.md`](docs/operations.md)
- **セキュリティ是正の到達状況と既知の制限**: [`doc/07-Remediation-Status.md`](doc/07-Remediation-Status.md)
  （implemented / partial / 未実装 を明示）
- 主要な設計判断: [ADR-0007 alias長](docs/adr/0007-alias-token-length.md) /
  [ADR-0008 tenant+user identity](docs/adr/0008-tenant-user-identity.md) /
  [ADR-0009 日本語NER](docs/adr/0009-japanese-ner-backend.md)

> ⚠️ `SECURITYMASKER_CONFIG`（マスキング辞書）は**必須**です。未設定だと起動に失敗します
> （fail-closed）。マスキングなしの開発モードは `SECURITYMASKER_DEV_TRANSPARENT=1` を明示した
> ときだけで、実プロバイダーには決して向けないでください。
>
> ✅ `securitymasker run codex` / `run claude` は、**Gateway が `/ready` を返し、かつ経路を
> 設定できたときだけ**ツールを起動します。経路を保証できない場合は起動しません
> （「保護されているつもり」で直接送信されることを防ぐため）。

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

出力例（alias は session 鍵に基づくため実行ごとに異なります）：

```text
masked:
  SM_ORG_2121B255C21BのSM_PERSON_81FEB612A4D0です。 key=${SECURITYMASKER_SECRET_5F7B783CB629}
detected (type: count):
  API_KEY: 1
  ORGANIZATION: 1
  PERSON: 1
```

CLI は元の入力を再表示せず、この command では復元も行いません。応答経路では、この
session が生成した `literal` alias だけをローカルで復元します。API key／private key は
実値へ戻さず `${SECURITYMASKER_SECRET_...}` のまま保持します（§10、§27）。

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
