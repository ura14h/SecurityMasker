# 設定

proxy（ADR-0006）は、**SecurityMasker dictionary**（`SECURITYMASKER_CONFIG`）と
少数の環境変数で設定します。dictionary の例は
[`config/securitymasker.example.yaml`](../config/securitymasker.example.yaml) にあります。

## proxy の起動

```bash
export SECURITYMASKER_CONFIG=config/securitymasker.example.yaml
securitymasker gateway --port 4000
```

環境変数：

- `SECURITYMASKER_CONFIG` — dictionary／policy YAML。**必須**で、未設定なら Gateway
  は fail-closed で起動に失敗します（doc/06 P0-1）。開発時だけマスクを無効にする
  には `SECURITYMASKER_DEV_TRANSPARENT=1` を明示します。この mode を実 provider に
  向けてはなりません。
- `SECURITYMASKER_OPENAI_UPSTREAM` — 既定値は
  `https://chatgpt.com/backend-api/codex`（Codex ChatGPT auth）。API key を使う
  OpenAI の場合は `https://api.openai.com/v1`。
- `SECURITYMASKER_ANTHROPIC_UPSTREAM` — 既定値は `https://api.anthropic.com`。
- `SECURITYMASKER_MODE` — `local`（既定）、`tenant`、`tenant_user`（ADR-0008）。
  `local` は単一 caller で分離なし、`tenant` は tenant 間を分離するものの同一
  tenant 内の user は alias table を共有し、`tenant_user` は両方を分離します。
  `multitenant` は `tenant` の legacy alias として受理します。非 local mode では
  `SECURITYMASKER_TENANT_AUTH_SECRET` が必要です。信頼済み authenticator はこれを
  用いて tenant+user（+timestamp）の version 付き canonical payload を署名します。
  bare header は信頼せず、assertion の欠落・偽造・期限切れは fail-closed で拒否します。
- `SECURITYMASKER_MAX_CLOCK_SKEW_SECONDS` — timestamp 付き identity assertion の
  許容経過時間。既定値は300秒。
- `SECURITYMASKER_GATEWAY_URL` — `run` と `doctor` が参照する Gateway。既定値は
  `http://127.0.0.1:4000`。
- `SECURITYMASKER_STORE` — `memory`（既定）または `redis`。`redis` では
  `SECURITYMASKER_REDIS_URL` も必要で、package／URL がなければ fail-closed
  （doc/06 P1-9）。
- `SECURITYMASKER_MASTER_KEY` — Redis session store に必須の、base64 で表した
  32 bytes（§8）。

Codex は proxy を指す `requires_openai_auth = true` provider を使い、ChatGPT OAuth
token を透過します。API key は保存しません。Claude Code は
`ANTHROPIC_BASE_URL` を設定します。詳細は [operations.md](operations.md) を
参照してください。

## dictionary（SECURITYMASKER_CONFIG）

```yaml
version: 1
defaults:
  fail_mode: closed            # エラー時は fail-closed（§26）
  normalization: nfkc          # 検出時に正規化し、復元時は元の表記を維持
  merge_surface_forms: false   # 表層形ごとに alias を生成
  session_idle_ttl: 4h
  session_absolute_ttl: 24h

entities:                      # 最も信頼する完全一致（§12）
  - id: employee
    type: PERSON
    values: ["山田太郎", "山田 太郎"]     # 複数の表層形
    # value_from_env: EMPLOYEE_NAME       # または環境変数から取得（平文の秘密を置かない）
    replacement_profile: prose_identifier
    restore_policy: literal               # literal | env_reference | redacted | block
    priority: 100

patterns:                      # ユーザー定義 regex
  - id: ticket
    pattern: 'INC-[0-9]{6}'
    type: CUSTOMER_ID
    replacement_profile: numeric

enable_secret_detector: true   # API key／JWT／PEM／DB URL → env_reference
enable_format_detectors: true  # email／IPv4／credit card（Luhn）→ block

japanese_pii:
  enabled: true
  my_number_restore_policy: block

ner:                           # v1互換schema。v2では固定modelを既定ON
  model: null
```

## 置換 profile（§9）

`prose_identifier`（`SM_PERSON_2B891C`）、`hostname`
（`sm-host-….example.invalid`）、`email`（`sm-user-…@example.invalid`）、
`ipv4`／`ipv6`（documentation range）、`uuid`、`numeric`（桁数保持）、
`file_path`、`url`、`environment_reference`
（`${SECURITYMASKER_SECRET_…}`）があります。

## 復元 policy（§10）

- `literal` — client へ返す前に復元する。
- `env_reference` — `${…}` のまま保持し、実値へ戻さない。
- `redacted` — 不可逆な `[REDACTED]` にする。
- `block` — request を拒否する。

既定では、氏名・住所・host・path は `literal`、API key・password・key は
`env_reference`、My Number と credit card は `block` です。

## 環境変数

- `SECURITYMASKER_CONFIG` — dictionary YAML の path。マスクを行うには必須。
- `SECURITYMASKER_SESSION_ID` — wrapper／header が伝播する session ID。
- `SECURITYMASKER_MASTER_KEY` — Redis store に必須の base64 32 bytes（§8）。
- dictionary の `value_from_env` が参照する値。

設定は load 時に検証します。不正な enum／regex、重複 ID があれば起動に失敗します
（§12）。

## context 分類

検出前に message body を prose、fenced／inline Markdown code、shell、JSON、YAML、
diff の typed span へ分割します（§17）。code-like span を除外するのは
`skip_code_contexts` を宣言する HF NER だけです。
dictionary と全 deterministic detector はすべての context で動かします。code fence
に貼られた秘密も秘密だからです。確実に分類できない text は、無効になる detector が
最も少ない `prose` として扱います。

## detector の上限

```yaml
defaults:
  detector_timeout_seconds: 10.0   # 0で無効。timeout時はrequestを拒否
  max_fuzzy_chars: 200000          # 超過時はrequestを拒否
```

ユーザー定義 regex は load 時に lint し、既知の catastrophic backtracking
（`(a+)+`、`(a|a)*`、巨大な bounded repeat）を拒否します。

**timeout になっても暴走 detector 自体は停止しません。** Python の `re` と
CPU-bound model は割り込み不能です。timeout が制限するのは request の待ち時間で、
worker は動き続けます。危険な regex は load 時に拒否し、model inference は固定長
pool と admission limit の下で実行します。放棄された inference は終了まで slot を
占有し、その間の追加 request は queue に積まず拒否します（ADR-0011）。
拒否は `DetectionError` となり、request は fail-closed になります。

**model detector が見る text 量を segmentation で制限しません。** fuzzy 対象 span を
結合し、request ごとに一度だけ実行するため、cost は segment 数ではなく prose 量に
比例します。旧設計は detector pass 数を制限しており、inline code を詰めた request
によって末尾 text を NER の対象外へ押し出せたため、現在は量を制限します
（ADR-0011）。

`max_fuzzy_chars` を超えた request は truncate せず**拒否**します。一部だけ検査して
成功を返すことは clean result と区別できません。上限を増やす場合は inference cost
を考慮してください。

## 任意の日本語 NER（ADR-0009）

既定は無効です。dictionary と deterministic detector が信頼層であり、NER は未登録名
の recall を広げるだけです。NER の結果を安全判定の根拠にはしません。

```yaml
ner:
  model: tsmatz/xlm-roberta-ner-japanese
  revision: aba094e118d5ffc622e9b25e07edc49f9dd85feb   # model指定時は必須
  min_score: 0.7          # 実測上の最適値。ADR-0009参照
  local_files_only: true  # request処理中にはfetchしない
  skip_code_contexts: true
```

```bash
pip install -e '.[ner]'
securitymasker models fetch --config <dictionary.yaml>   # 明示的に取得しdigest検証
```

model の label schema と tokenizer の offset 対応は起動時に検証します。label を
mapping できない model や文字 span を報告できない model は、黙って検出ゼロにせず
拒否します。

## tool arguments の信頼設定

```yaml
tool_trust:
  trusted_local_tools: []   # 既定では実値を受け取るtoolはない
```

表示用の response text は復元します。tool arguments は実行されるため、この allowlist
にある tool だけ実値へ復元します。外部 MCP tool を含むそれ以外には alias を渡します。
