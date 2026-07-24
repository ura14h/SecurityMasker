# AGENTS.md — SecurityMasker 開発エージェント憲章

このファイルは、本リポジトリで作業するすべてのコーディングエージェント（Claude Code / Codex など）が
最初に読むべき運用ルールです。**製品の完全な要件は [`doc/00-First-Order.md`](doc/00-First-Order.md) が唯一の正典（source of truth）** です。
本ファイルはその要約と、作業上の不変ルールを定めます。矛盾した場合は `doc/00-First-Order.md` を優先してください。

---

## 1. プロジェクト概要

**SecurityMasker** は、OSS の LiteLLM Proxy へ薄い拡張モジュールとして組み込む「可逆マスキング・セキュリティ境界」。
ローカルの Codex / Claude Code から外部 LLM（OpenAI / Anthropic）へ送信される機密情報を、送信前にセッション単位の
安定した仮名（alias）へ可逆置換し、レスポンスをローカルで復元する。

- パッケージ名: `securitymasker` / CLI: `securitymasker` / コールバッククラス: `SecurityMaskerCallback`
- 「reversible-masker」という名称は**使用しない**。
- LiteLLM 本体は **fork しない**。カスタムコールバック / Guardrail hook として実装し、
  LiteLLM 固有部分は `src/securitymasker/integrations/litellm.py` に閉じ込める。

## 2. 破ってはいけない不変ルール（優先順位順）

`doc/00-First-Order.md` §40 に対応。設計判断に迷ったら常にこの順で決める。

1. 元の機密情報を外部（非信頼領域）へ送らない。
2. セッション / テナントをまたいで秘密や alias を混ぜない。
3. JSON・コード・ツール呼び出し・patch・シェルコマンドを構文的に壊さない。
4. 不明・障害時は **fail-closed**（外部へ送らずブロック）。fail-open は明示設定時のみ、かつ重大 Secret は常に block。
5. LiteLLM 本体を fork しない。薄いアダプターに隔離する。
6. Protocol adapter（OpenAI Responses / Anthropic Messages）と masking core を分離する。
7. 未知フィールド・未知イベント・未知ヘッダーは可能な限り透過的に通す（認証情報を除く）。
8. **ログ・監査・例外トレース・テレメトリに元の機密値、復号鍵、平文対応表を絶対に残さない。**
9. Presidio / NER だけに正しさを依存しない。ユーザー登録辞書を最も信頼する。
10. API キー・秘密鍵・パスワードは実値復元より `env_reference`（環境変数参照）を優先する。
11. 機能追加よりテスト可能性・保守性を優先する。

## 3. アーキテクチャの要点

```
Codex / Claude Code → LiteLLM Proxy → SecurityMaskerCallback → OpenAI / Anthropic API
                                          ├─ Protocol adapters (openai_responses / anthropic_messages)
                                          ├─ Detectors (dictionary / regex / secret / presidio / jp-*)
                                          ├─ Alias factory + replacement profiles
                                          ├─ Session store (in-memory → Redis)
                                          └─ Streaming transformer (text + tool-argument)
```

- **信頼領域**: ローカルマシン、Gateway、セッションストア、明示的に信頼したローカルツール。
- **非信頼領域**: OpenAI / Anthropic / 外部 LLM / 外部テレメトリ / 外部ログ / 外部 MCP / Hosted tools。
- セッション対応表は「速度最適化」ではなく **中核状態**。`secret_index = HMAC(session_key, normalized+type+profile)`、
  `alias → AES-GCM(original)`。素の SHA-256 単独で alias を決めない。セッション鍵は暗号乱数生成、セッション ID から直接導出しない。

## 4. ディレクトリ構成（正典は doc §28）

```
securitymasker/
├── pyproject.toml / README.md / SECURITY.md / LICENSE / docker-compose.yml
├── config/   (litellm.example.yaml / securitymasker.example.yaml)
├── docs/     (architecture / threat-model / compatibility / operations / configuration / japanese-pii / adr/)
├── src/securitymasker/
│   ├── cli.py config.py errors.py logging.py models.py policy.py normalization.py ranges.py engine.py
│   ├── detectors/  aliases/  sessions/  protocols/  streaming/  integrations/
└── tests/    (unit / integration / e2e / fixtures / evaluation)
```

## 5. 実装フェーズ（doc §37）

- **Phase 0**: 互換性固定。LiteLLM / Presidio / Python バージョン確定、`/v1/responses`・`/v1/messages` の実 SSE 構造確認、
  hook シグネチャ確認、logging 実行順序確認、`docs/compatibility.md` と ADR 作成。
- **Phase 1**: コア MVP（辞書・Regex・Secret detector / インメモリセッション / HMAC alias / AES-GCM / profiles / 非ストリーム mask-unmask / fail-closed / CLI / unit test）。
- **Phase 2**: Codex 対応（OpenAI Responses adapter / SSE / streaming 復元 / tool 引数バッファ / mock upstream / E2E fixture）。
- **Phase 3**: Claude Code 対応（Anthropic Messages adapter / content blocks / tool use / beta header 透過 / E2E fixture）。
- **Phase 4**: 日本語 PII（Presidio adapter / JP phone・postal・My Number / DOB / NER adapter / composite address / 評価コーパス）。
- **Phase 5**: 運用強化（Redis / multi-tenant / 暗号化永続 / metrics / audit / Docker hardening / CI / benchmark）。

**各 Phase 終了時に、実行可能な状態とテスト結果を残す。** 説明だけで終えない。

## 6. 技術スタック（doc §36。Phase 0 で最終確定・pin する）

- Python 3.12+、`uv` で依存管理（lock file をコミット）。
- `pydantic` / `pydantic-settings`、`cryptography`（AES-GCM）、`httpx`。
- `pytest` / `pytest-asyncio` / `hypothesis`、`ruff`、`mypy` または `pyright`。
- LiteLLM・Presidio・OpenAI SDK・Anthropic SDK は**特にバージョン固定**する。
- 構造化ログ、pre-commit、GitHub Actions。

## 7. テストの絶対条件（doc §30）

- **実在人物・実際の秘密情報をテストに使わない。**
- 最重要受け入れテスト:「モデルへ送信した最終ペイロードに元の機密値が存在しない」(leakage test)。
- ストリーミングは property-based で **alias の全分割位置** を検証。tool 引数は複数 delta 分割・特殊文字（`" \ \n \t`）で JSON が壊れないことを検証。
- 並行性テストでセッション間の秘密・alias 混在がないことを必ず検証。

## 8. エージェントの作法

- 破壊的・外部送信を伴う操作、依存の新規導入、大きな設計転換は**実施前にユーザーへ確認**する。
- 秘密値をコミット・ログ・スクラッチファイルへ書かない。テストは合成データのみ。
- 置いた前提（安全側のデフォルト）は README または `docs/adr/` に必ず明記する（doc §00 冒頭・§07）。
- コミット / push はユーザーが依頼したときのみ。
- 作業言語: ユーザーとのやり取りは日本語。コード内コメント・識別子は英語基調で周囲に合わせる。
