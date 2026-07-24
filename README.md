# SecurityMasker

LiteLLM Proxy へ薄い拡張として組み込む、**可逆マスキング・セキュリティ境界**。
ローカルの Codex / Claude Code から外部 LLM（OpenAI / Anthropic）へ機密情報を送る前に、
セッション単位の安定した仮名（alias）へ可逆置換し、レスポンスをローカルで復元します。

- 完全な要件（正典）: [`doc/00-First-Order.md`](doc/00-First-Order.md)
- 実装計画: [`doc/01-Plan.md`](doc/01-Plan.md)
- 開発エージェント向けルール: [`AGENTS.md`](AGENTS.md)

> ⚠️ 開発初期（Phase 0: 互換性固定）です。まだ動作する Gateway は提供していません。

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
