# AGENTS.md — SecurityMasker 開発エージェント憲章

このファイルは、本リポジトリで作業するすべてのコーディングエージェント（Claude Code / Codex など）が
最初に読む**現行の運用ルール**です。歴史的経緯ではなく、いま従うべき指示だけを書きます。

[`doc/00-First-Order.md`](doc/00-First-Order.md) は**初期命令（ブリーフ・方針）**であり、変更不能な
制約宣言ではありません（冒頭が「安全側の前提を置き ADR に明記せよ」と工学判断を招く）。
初版から設計が変わった箇所（特に LiteLLM 撤廃とローカル配布設計への刷新）については、
doc/00 の記述より新しい ADR が優先します。

**拘束力の三層**（[ADR-0006](docs/adr/0006-drop-litellm-purpose-built-proxy.md)、
[ADR-0012](docs/adr/0012-renew-package-design.md)）:

1. **不変条件**（製品の目的・ツール非依存）= §2。何を選んでも死守する。
2. **最新 ADR**（[`docs/adr/`](docs/adr/)）= 手段についての最新の決定。
3. **doc/00 の手段記述** = 初期案。1・2 と矛盾する場合は 1・2 に従う。

---

## 1. プロジェクト概要

**SecurityMasker** は、ローカルの ChatGPT デスクトップ版（Codex 統合機能）/ Codex CLI /
Claude Code デスクトップ版 / Claude Code CLI と外部 LLM（OpenAI / Anthropic）の間に立つ
**可逆マスキング・セキュリティ境界（透過プロキシ）**。送信前に機密情報をセッション単位の
安定した仮名（alias）へ可逆置換し、応答をローカルで復元する。

- パッケージ名: `securitymasker` / CLI: `securitymasker`。
- 「reversible-masker」という名称は**使用しない**。
- **アーキテクチャ**: ChatGPT/Codex（OpenAI Responses）と Claude Code（Anthropic Messages）専用の
  **自作の薄い透過プロキシ**（Starlette + httpx）。外部プロキシ製品には依存しない。
  設計と撤廃理由は [ADR-0006](docs/adr/0006-drop-litellm-purpose-built-proxy.md) と
  [ADR-0012](docs/adr/0012-renew-package-design.md)。
- 認証は**透過パススルー**（クライアントの資格情報を素通し・保存/復号/ログしない）。
- 利用者向け mode は `chatgpt` / `claude`。**1 process・1 mode・1 port・1 worker**を標準とし、
  両 mode は別設定・別 SQLite・別 master key で起動する。
- `securitymasker` 単一実行ファイルと `python3 securitymasker.py` の両方を第一級の起動経路にする。
  `--mode` / `--port` は設定ファイルに既定値を持ち、CLI 指定が優先する。
- 利用者の `~/.codex/config.toml` 等は変更しない。利用者自身が base URL 等を設定する。

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
9. NER などモデル検出だけに正しさを依存しない。ユーザー登録辞書を最も信頼する。
10. API キー・秘密鍵・パスワードは実値復元より `env_reference`（環境変数参照）を優先する。
11. 機能追加よりテスト可能性・保守性を優先する。

**「一部だけ検査して成功を返す」は 1 と 4 の違反**とみなす。上限に達したら黙って打ち切らず
block する（[ADR-0011](docs/adr/0011-bounding-model-inference.md)）。

## 3. アーキテクチャの要点

```
ChatGPT/Codex または Claude Code → SecurityMasker 1 process → 対応する provider API
                                     ├─ gateway/       (mode別route・forwarder・runtime)
                                     ├─ protocols/     (openai_responses / anthropic_messages / sse)
                                     ├─ context/       (prose/code/shell/JSON/YAML/diff の文脈分割)
                                     ├─ detectors/     (dictionary / regex / secret / jp-* / jp_ner)
                                     ├─ aliases/       (replacement profiles + collision-safe factory)
                                     ├─ sessions/      (memory preview / SQLite standard・crypto)
                                     └─ streaming/     (SSE 復元: text + tool-argument)
```

- **信頼領域**: ローカルマシン、Gateway、セッションストア、明示的に信頼したローカルツール。
- **非信頼領域**: OpenAI / Anthropic / 外部 LLM / 外部テレメトリ / 外部ログ / 外部 MCP / Hosted tools。
- セッション対応表は「速度最適化」ではなく **中核状態**。`secret_index = HMAC(session_key, normalized+type+profile)`、
  `alias → AES-GCM(original)`。素の SHA-256 単独で alias を決めない。セッション鍵は暗号乱数生成、
  セッション ID から直接導出しない。
- SQLite と master key は 1 対 1 とする。key は SQLite 内へ平文保存せず、設定で
  `securitymasker.state/securitymasker.{db,key}` を明示する。同じ SQLite を複数 process で共有しない。
- 決定論的検出器は全 context で走る。モデル検出器（`fuzzy = True`）のみ code 系を skip し、
  リクエスト全体で 1 回だけ走る（ADR-0011）。

## 4. ディレクトリ構成

利用者が展開する標準構成は次の形とする。

```
SecurityMasker/
├── securitymasker
├── securitymasker.py
├── securitymasker.config
├── securitymasker.dict
└── securitymasker.state/
    ├── securitymasker.db
    └── securitymasker.key
```

- `securitymasker.config` は拡張子にかかわらず strict YAML。runtime・state・policy・detector と、
  単一の辞書ファイルへの path を持つ設定の根とする。
- `securitymasker.dict` も YAML。複数辞書・include・glob・merge は導入しない。
- 相対 path は設定ファイルの所在を基準に解決する。`--config` 省略時は CLI、環境変数、
  実行ファイルまたは root script の隣、の順で探索し、任意の CWD や親 directory は探索しない。
- repository 内部は移行中も `src/securitymasker/`、`tests/`、`doc/`、`docs/adr/` を維持する。
  Docker・Redis・Presidio・CI 関連物は Phase 9 で標準製品から撤去するまで legacy として扱う。

## 5. 現在の状況と進め方

従来 architecture の監査是正は完了しているが、その構成を release target にはしない。
現在は [ADR-0012](docs/adr/0012-renew-package-design.md) の Phase 0〜10 に従って、
**clone 後に Python script または単一 binary で使えるローカル製品へ移行中**である。

- 何が `done` で何が `partial` かの唯一の正は [`doc/07-Remediation-Status.md`](doc/07-Remediation-Status.md)。
  着手前に必ず読む。
- **`done` と書いてよいのは、実装・製品への配線・回帰テスト・運用手順が揃ったときだけ。**
  どれか欠けていれば `partial` と、何が欠けているかを書く。
- 各作業単位の終わりに、実行可能な状態とテスト結果を残す。説明だけで終えない。
- 各 Phase で利用者の通常運用 setup と、mock/CLI を使う test setup を混同しない。

## 6. 技術スタック

- Python 3.12+。依存管理は **pip + venv**（`uv` は採用しない — [ADR-0002](docs/adr/0002-pip-venv-over-uv.md)）。
  lock ファイルは用途別に分割し、コミットする。
- `pydantic` / `pydantic-settings`、`cryptography`（AES-GCM）、`httpx`、`starlette`。
- `pytest` / `pytest-asyncio` / `hypothesis`、`ruff`、`mypy`。
- 標準永続化は Python 標準 library の SQLite。Redis は標準製品に含めない。
- 日本語 NER は**標準搭載・既定 ON**。Presidio は標準製品から撤去する。モデルは
  revision + 全成果物の SHA-256 で固定し、safetensors のみ・`local_files_only`・
  `trust_remote_code` 不使用
  （[ADR-0010](docs/adr/0010-model-supply-chain.md)）。
- `scripts/setup` が venv・固定依存・固定 NER model を準備し、実行中に暗黙 download しない。
- PyInstaller one-file build を提供するが、OS ごとに native build・検証する。
- GitHub Actions、PyPI、Docker は標準 release 経路にしない。GitHub repository の公開と
  手元で再現できる source/binary build を release の前提とする。

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
- 作業言語: ユーザーとのやり取りと技術文書（Markdown）は日本語を基本とする。
- コード内コメントと内部 docstring は日本語を基本とする。識別子、protocol 名、外部 API の
  用語、機械処理される文字列は英語を維持し、重要な専門用語は初出時に英語を併記する。
  公開 Python API の docstring は公開対象に応じて日本語または日英併記とする。
- コミットの件名と本文は日本語で書き、`feat:`、`fix:`、`docs:` などの
  Conventional Commits 形式は使用しない。件名は変更内容を具体的に表す通常の日本語文とし、
  必要な理由・検証内容・残存事項を本文に記す。AI エージェントの
  `Co-Authored-By` trailer は付けない。
