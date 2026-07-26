# AGENTS.md — SecurityMasker 開発エージェント憲章

このファイルは、本リポジトリで作業するすべてのコーディングエージェント（Claude Code / Codex など）が
最初に読む**現行の運用ルール**です。歴史的経緯ではなく、いま従うべき指示だけを書きます。

[`doc/00-First-Order.md`](doc/00-First-Order.md) は**初期命令（ブリーフ・方針）**であり、変更不能な
制約宣言ではありません（冒頭が「安全側の前提を置き ADR に明記せよ」と工学判断を招く）。
初版から設計が変わった箇所（特に LiteLLM 撤廃）については、doc/00 の記述より新しい ADR が優先します。

**拘束力の三層**（[ADR-0006](docs/adr/0006-drop-litellm-purpose-built-proxy.md)）:

1. **不変条件**（製品の目的・ツール非依存）= §2。何を選んでも死守する。
2. **最新 ADR**（[`docs/adr/`](docs/adr/)）= 手段についての最新の決定。
3. **doc/00 の手段記述** = 初期案。1・2 と矛盾する場合は 1・2 に従う。

---

## 1. プロジェクト概要

**SecurityMasker** は、ローカルの Codex / Claude Code と外部 LLM（OpenAI / Anthropic）の間に立つ
**可逆マスキング・セキュリティ境界（透過プロキシ）**。送信前に機密情報をセッション単位の安定した
仮名（alias）へ可逆置換し、応答をローカルで復元する。

- パッケージ名: `securitymasker` / CLI: `securitymasker`。
- 「reversible-masker」という名称は**使用しない**。
- **アーキテクチャ**: Codex（OpenAI Responses）と Claude Code（Anthropic Messages）専用の
  **自作の薄い透過プロキシ**（Starlette + httpx）。外部プロキシ製品には依存しない。
  設計と撤廃理由は [ADR-0006](docs/adr/0006-drop-litellm-purpose-built-proxy.md) と
  [`doc/05-Phase6-Design.md`](doc/05-Phase6-Design.md)。
- 認証は**透過パススルー**（クライアントの資格情報を素通し・保存/復号/ログしない）。
- 起動経路は `securitymasker run <tool>`。gateway が `ready` でなければ子プロセスを起動しない。
  利用者の `~/.codex/config.toml` 等は変更せず、プロセス単位の `-c` override で経路を差し込む。

## 2. 破ってはいけない不変ルール（優先順位順）

`doc/00-First-Order.md` §40 に対応。設計判断に迷ったら常にこの順で決める。

1. 元の機密情報を外部（非信頼領域）へ送らない。
2. セッション / テナント / ユーザーをまたいで秘密や alias を混ぜない。
3. JSON・コード・ツール呼び出し・patch・シェルコマンドを構文的に壊さない。
4. 不明・障害時は **fail-closed**（外部へ送らずブロック）。fail-open は明示設定時のみ、かつ重大 Secret は常に block。
5. 上流クライアント・SDK を fork しない。プロトコル差分は薄いアダプターに隔離する。
6. Protocol adapter（OpenAI Responses / Anthropic Messages）と masking core を分離する。
7. 未知フィールド・未知イベント・未知ヘッダーは可能な限り透過的に通す（認証情報を除く）。
8. **ログ・監査・例外トレース・テレメトリに元の機密値、復号鍵、平文対応表を絶対に残さない。**
9. Presidio / NER だけに正しさを依存しない。ユーザー登録辞書を最も信頼する。
10. API キー・秘密鍵・パスワードは実値復元より `env_reference`（環境変数参照）を優先する。
11. 機能追加よりテスト可能性・保守性を優先する。

**「一部だけ検査して成功を返す」は 1 と 4 の違反**とみなす。上限に達したら黙って打ち切らず
block する（[ADR-0011](docs/adr/0011-bounding-model-inference.md)）。

## 3. アーキテクチャの要点

```
Codex / Claude Code → securitymasker gateway → OpenAI / Anthropic API
                          ├─ gateway/       (routes, forwarder, session, identity, runtime)
                          ├─ protocols/     (openai_responses / anthropic_messages / sse)
                          ├─ context/       (prose/code/shell/JSON/YAML/diff の文脈分割)
                          ├─ detectors/     (dictionary / regex / secret / jp-* / presidio / jp_ner)
                          ├─ aliases/       (replacement profiles + collision-safe factory)
                          ├─ sessions/      (store protocol → memory / redis, crypto)
                          └─ streaming/     (SSE 復元: text + tool-argument)
```

- **信頼領域**: ローカルマシン、Gateway、セッションストア、明示的に信頼したローカルツール。
- **非信頼領域**: OpenAI / Anthropic / 外部 LLM / 外部テレメトリ / 外部ログ / 外部 MCP / Hosted tools。
- セッション対応表は「速度最適化」ではなく **中核状態**。`secret_index = HMAC(session_key, normalized+type+profile)`、
  `alias → AES-GCM(original)`。素の SHA-256 単独で alias を決めない。セッション鍵は暗号乱数生成、
  セッション ID から直接導出しない。
- 決定論的検出器は全 context で走る。モデル検出器（`fuzzy = True`）のみ code 系を skip し、
  リクエスト全体で 1 回だけ走る（ADR-0011）。

## 4. ディレクトリ構成

```
securitymasker/
├── pyproject.toml / README.md / SECURITY.md / LICENSE
├── Dockerfile / docker-compose.yml / docker-compose.redis.yml
├── requirements*.lock            (runtime / dev / presidio / ner を分離)
├── config/                       (securitymasker.example.yaml / securitymasker.demo.yaml)
├── devtools/                     (mock_upstream.py — 製品イメージに入れない)
├── doc/                          (00 初期命令 / 01 計画 / 02-05 設計 / 06 課題 / 07 是正状況)
├── docs/                         (architecture / threat-model / compatibility / operations /
│                                  configuration / japanese-pii / adr/)
├── src/securitymasker/
│   ├── cli.py config.py doctor.py engine.py errors.py logging.py metrics.py
│   │   models.py models_fetch.py normalization.py policy.py tool_trust.py
│   └── aliases/ context/ detectors/ gateway/ integrations/ protocols/ sessions/ streaming/
└── tests/                        (unit / integration / evaluation / fixtures)
```

## 5. 現在の状況と進め方

初期計画（doc §37）の Phase 0〜5 と、LiteLLM 撤廃後の Phase 6（自作プロキシ）は実装済み。
現在は**監査で挙がった残存ギャップの是正フェーズ**にある。

- 何が `done` で何が `partial` かの唯一の正は [`doc/07-Remediation-Status.md`](doc/07-Remediation-Status.md)。
  着手前に必ず読む。
- **`done` と書いてよいのは、実装・製品への配線・回帰テスト・運用手順が揃ったときだけ。**
  どれか欠けていれば `partial` と、何が欠けているかを書く。
- 各作業単位の終わりに、実行可能な状態とテスト結果を残す。説明だけで終えない。

## 6. 技術スタック

- Python 3.12+。依存管理は **pip + venv**（`uv` は採用しない — [ADR-0002](docs/adr/0002-pip-venv-over-uv.md)）。
  lock ファイルは用途別に分割し、コミットする。
- `pydantic` / `pydantic-settings`、`cryptography`（AES-GCM）、`httpx`、`starlette`。
- `pytest` / `pytest-asyncio` / `hypothesis`、`ruff`、`mypy`。
- Presidio と NER は**任意依存**（既定 OFF）。モデルは revision + 全成果物の SHA-256 で固定し、
  safetensors のみ・`local_files_only`・`trust_remote_code` 不使用
  （[ADR-0010](docs/adr/0010-model-supply-chain.md)）。
- コンテナのベースイメージは digest で固定する。

## 7. テストの絶対条件（doc §30）

- **実在人物・実際の秘密情報をテストに使わない。合成データのみ。**
- 実 provider（OpenAI / Anthropic）へテスト本文を送信しない。
- 最重要受け入れテスト:「モデルへ送信した最終ペイロードに元の機密値が存在しない」(leakage test)。
- ストリーミングは property-based で **alias の全分割位置** を検証。tool 引数は複数 delta 分割・
  特殊文字（`" \ \n \t`）で JSON が壊れないことを検証。
- 並行性テストでセッション間の秘密・alias 混在がないことを必ず検証。
- **拒否できることだけを検証したテストは、受理できることを何も保証しない。**
  合成 fixture しか見ないチェックは、正しい入力まで拒否する不具合を通す
  （実例: ADR-0010 のモデルマニフェスト）。

## 8. エージェントの作法

- 破壊的・外部送信を伴う操作、依存の新規導入、大きな設計転換は**実施前にユーザーへ確認**する。
- 秘密値をコミット・ログ・スクラッチファイルへ書かない。
- 利用者の repo 外の実設定（`~/.codex/config.toml` 等）を直接変更しない。
- 置いた前提（安全側のデフォルト）は README または `docs/adr/` に必ず明記する。
  **コードから ADR を参照するなら、その ADR を実際に書く。**
- コミット / push はユーザーが依頼したときのみ。push・PR 作成・外部公開は明示依頼がない限り行わない。
- 作業言語: ユーザーとのやり取りは日本語。コード内コメント・識別子は英語基調で周囲に合わせる。
