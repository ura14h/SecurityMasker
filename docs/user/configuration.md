# 設定リファレンス

`securitymasker.config` は拡張子が `.config` のstrict YAMLです。未知fieldは誤記として拒否されます。
相対pathはconfigのdirectoryを基準に解決します。

```yaml
version: 2

runtime:
  mode: chatgpt
  host: 127.0.0.1
  port: 4000

state:
  database: ./securitymasker.state/securitymasker.db
  key: ./securitymasker.state/securitymasker.key

dictionary: ./securitymasker.dict

defaults:
  fail_mode: closed
  normalization: nfkc
  merge_surface_forms: false
  preserve_aliases: true
  session_idle_ttl: 4h
  session_absolute_ttl: 24h
  inject_alias_instruction: true

detectors:
  secrets:
    enabled: true
  formats:
    enabled: true
  japanese_pii:
    enabled: true
    my_number_restore_policy: block
  japanese_ner:
    enabled: true
    model: tsmatz/xlm-roberta-ner-japanese
    revision: aba094e118d5ffc622e9b25e07edc49f9dd85feb
    min_score: 0.7
    local_files_only: true
    allow_unverified_model: false

tool_trust:
  trusted_local_tools: []
```

## runtime

- `mode`: `chatgpt` または `claude`。1プロセスで両方は指定できません。
- `host`: loopbackだけを許可します。
- `port`: 1から65535。CLI `--port` は一時的に上書きします。

優先順位は CLI option、config、組込み既定値です。modeが決まらなければ起動しません。

## state

`database` と `key` は1対1です。keyは256-bitのraw binaryで、DBの外に置きます。
session全体はAES-256-GCMで封緘され、session/response IDのlookupにはHMACを使います。

DBとkeyを必ず同じ時点の組としてbackupしてください。keyを失うと既存DBを復号できません。
別modeのDB/keyを流用すると拒否されます。

## dictionary

辞書は1ファイルだけを参照します。include、glob、複数ファイルmergeはありません。

```yaml
version: 1

entities:
  - id: internal_project
    type: ORGANIZATION
    values: ["極秘計画", "Project Cedar"]
    replacement_profile: prose_identifier
    restore_policy: literal
    priority: 100

  - id: deployment_password
    type: SECRET
    value_from_env: DEPLOYMENT_PASSWORD
    replacement_profile: prose_identifier
    restore_policy: env_reference
    priority: 200

patterns:
  - id: internal_ticket
    pattern: 'INC-[0-9]{6}'
    type: CUSTOMER_ID
    replacement_profile: numeric
    restore_policy: literal
    priority: 120
```

API key、password、秘密鍵は平文の `values` より `value_from_env` を推奨します。環境変数が
未設定ならfail-closedで起動しません。危険なregex形状、重複ID、矛盾する定義も拒否します。

## detector

ユーザー辞書を最優先に、秘密/形式/日本固有の決定論的検出器、日本語NERを重ねます。
標準v2設定では日本語NERを有効にし、固定model以外や未検証modelへの切替を許可しません。

`fail_mode: closed` が標準です。`open` は一部のfuzzy detector障害を許容しますが、重大secretと
最終leak guardは常にblockします。通常利用では変更しないでください。

## TTL

`session_idle_ttl` は最終利用からの期限、`session_absolute_ttl` は作成からの上限です。
idle TTLがabsolute TTLを超える設定は拒否します。TTL満了後は同じ会話の過去aliasを復元できない
ため、新しい会話を開始してください。
