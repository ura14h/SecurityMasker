# SecurityMasker

LiteLLM Proxy へ薄い拡張として組み込む、**可逆マスキング・セキュリティ境界**。
ローカルの Codex / Claude Code から外部 LLM（OpenAI / Anthropic）へ機密情報を送る前に、
セッション単位の安定した仮名（alias）へ可逆置換し、レスポンスをローカルで復元します。

- 完全な要件（正典）: [`doc/00-First-Order.md`](doc/00-First-Order.md)
- 実装計画: [`doc/01-Plan.md`](doc/01-Plan.md)
- 開発エージェント向けルール: [`AGENTS.md`](AGENTS.md)

> ⚠️ 開発中: Phase 0（互換性固定）と Phase 1（コアマスキング MVP）が完了。
> Gateway への実接続（プロトコル walker / ストリーミング復元）は Phase 2 以降です。

## デモ（§39 / CLI で再現可能）

```bash
export SECURITYMASKER_CONFIG=config/securitymasker.example.yaml
securitymasker entities test "株式会社極秘技研の山田太郎です。 key=sk-abcdefghijklmnopqrstuvwxyz0"
```

```text
入力:
  株式会社極秘技研の山田太郎です。 key=sk-...

外部LLMへ送信（マスク後）:
  SM_ORG_2121B2のSM_PERSON_81FEB6です。 key=${SECURITYMASKER_SECRET_5F7B78}

（レスポンスに含まれる alias は、このセッションで生成したものだけをローカルで復元）
  SM_ORG_... → 株式会社極秘技研 / SM_PERSON_... → 山田太郎
  ${SECURITYMASKER_SECRET_...} は実値へ戻さず環境変数参照のまま
```

- 同一セッションでは同じ機密値が常に同じ alias に、別セッションでは別 alias になります（§6）。
- API キー・秘密鍵は既定で `${SECURITYMASKER_SECRET_...}` へ変換し、実値をレスポンスへ戻しません（§10, §27）。

## 対応バージョン

対応・固定バージョンは [`docs/compatibility.md`](docs/compatibility.md) を参照してください。

## 前提・既定（Assumptions）

安全側に置いた前提は [`docs/adr/`](docs/adr/) に記録します。

## 開発環境セットアップ（pip + venv）

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# 重い依存（Gateway 実行時）:
pip install -e ".[litellm,presidio,providers]"
```
