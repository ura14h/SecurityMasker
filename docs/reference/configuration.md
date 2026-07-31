# 設定リファレンス

`securitymasker.config`と`securitymasker.dict`の全fieldを扱う仕様書です。会社名やproject名を
追加するだけなら、先に[辞書のカスタマイズ](../guides/customize-dictionary.md)を参照してください。

`securitymasker.config`はstrict YAMLです。未知fieldは誤記として拒否されます。相対pathはconfigの
directoryを基準に解決します。CLI `--config`、`SECURITYMASKER_CONFIG`、実行ファイルに隣接する
`securitymasker.config`の順で探索し、current working directoryや親directoryは探索しません。

## 完全な設定例

`init`が生成する既定値を、通常はそのまま利用してください。

```yaml
version: 1

runtime:
  mode: chatgpt
  host: 127.0.0.1
  port: 4000

logging:
  level: INFO

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
  detector_timeout_seconds: 10.0
  max_fuzzy_chars: 200000

detectors:
  secrets:
    enabled: true
  formats:
    enabled: true
  japanese_pii:
    enabled: true
    my_number_restore_policy: block
    my_number_min_score: 0.0
    corporate_number: false
  japanese_ner:
    enabled: true
    model: tsmatz/xlm-roberta-ner-japanese
    revision: aba094e118d5ffc622e9b25e07edc49f9dd85feb
    min_score: 0.7
    local_files_only: true
    skip_code_contexts: true
    allow_unverified_model: false

tool_trust:
  trusted_local_tools: []
```

## top-level

- `version`: 現行schemaは整数`1`だけです。
- `runtime`: 1 processのmode、bind先、port。
- `logging`: Gatewayが標準errorへ出す製品logの表示閾値。
- `state`: 暗号化SQLiteとsidecar master key。
- `dictionary`: 1つの`securitymasker.dict`へのpath。
- `defaults`: masking engineとsessionの共通動作。
- `detectors`: 組込みdetectorの有効化と閾値。
- `tool_trust`: tool argumentを原文へ復元してよいlocal tool。

## runtime

- `mode`: `chatgpt`または`claude`。1 processで両方は指定できません。
- `host`: `127.0.0.1`、`::1`、`localhost`だけです。
- `port`: `1`から`65535`。CLI `--port`はその起動だけ上書きします。

CLI option、config、組込み既定値の順で解決します。public bindと複数workerは設定できません。

## logging

- `level`: `DEBUG`、`INFO`、`WARNING`、`ERROR`のいずれか。既定は`INFO`。

指定level以上のeventだけを標準errorへ表示します。順序は
`DEBUG < INFO < WARNING < ERROR`です。`logging`節を持たない既存のschema v1 configも
`INFO`として読み込みます。level名は大文字で指定し、未知値や未知fieldは起動時に拒否します。

levelの意味とevent対応は[CLI referenceのログの読み方](cli.md#ログの読み方)を参照してください。
設定file自体が読めない、または`logging.level`が不正な場合はその設定を適用できないため、
設定不良を既定閾値の`ERROR`として表示します。

## state

- `database`: session、alias対応表、response bindingを保存するSQLite file。
- `key`: 32 byteのmaster key。DBとは別fileに保存します。

`database`と`key`は1対1です。DBとkeyを必ず同じ時点の組としてbackupしてください。keyを失うと
既存DBを復号できません。別modeのDB/key、wrong key、改竄、同じDBへの二重writerは拒否します。

POSIXではconfig、辞書、DB、keyを`0600`、state directoryを`0700`にします。owner以外が
読み書きできる場合は起動しません。

## defaults

| field | 既定 | 意味 |
|---|---:|---|
| `fail_mode` | `closed` | detector障害時の扱い。`open`でskipできるのはfuzzyな日本語NERだけ |
| `normalization` | `nfkc` | 検出時のUnicode正規化。`nfkc`、`nfc`、`nfkd`、`nfd` |
| `merge_surface_forms` | `false` | `true`なら正規化後に同じ値となる表記へ同じaliasを割り当てる |
| `preserve_aliases` | `true` | 既存のSecurityMasker alias形状を検出し、再マスクから保護する |
| `session_idle_ttl` | `4h` | 最終利用からのsession期限 |
| `session_absolute_ttl` | `24h` | 作成からのsession上限 |
| `inject_alias_instruction` | `true` | aliasを不透明なtokenとして保持する指示をprovider requestへ追加する |
| `detector_timeout_seconds` | `10.0` | detectorごとの待機上限。`0`はtimeout待機を無効化、最大`300` |
| `max_fuzzy_chars` | `200000` | 1 requestでfuzzy detectorへ渡せる総文字数。`1000`から`10000000` |

`fail_mode: open`でもdictionary、user regex、secret、format等の決定論的detectorの障害は
fail-closedです。detectorを設定で明示的に無効化することとは別です。

`max_fuzzy_chars`を超えた場合はprefixだけを検査せずrequest全体を拒否します。
`detector_timeout_seconds`は処理自体を強制停止する値ではなく、Gatewayが結果を待つ上限です。

TTLは正の整数と単位`s`、`m`、`h`、`d`で記述します。idle TTLがabsolute TTLを超える設定は
拒否します。期限後は過去aliasを復元できないため、新しい会話を開始してください。

## detectors

detectorの`enabled`は、用途に合わせて保護範囲を選ぶための設定です。`false`にすると、その
detectorは通常のmaskingだけでなく、unknown fieldや構造fieldを転送前に検査する最終leak guard
からも外れます。無効化した種類をSecurityMaskerが別経路で検出するとは仮定しないでください。

### `secrets`

`detectors.secrets.enabled`はdeveloper secret patternを切り替えます。既定`true`ではOpenAI、
Anthropic、GitHub、AWS、Slack、Stripe、Google、npm、PyPI等のtoken、JWT、private key、
credential付きURL、DB接続文字列、secret名付き代入を決定論的に検出します。

`false`は、これらを別の境界で処理する構成や検出範囲を意図的に限定する場合のための重要な
フラグです。設定するとsecret detectorと同detectorによる最終guardの両方が無効になります。
辞書やuser regexで同じ値を登録しない限り、その値はmaskされません。

検出したAPI key等は`environment_reference`形状へ置換し、`env_reference` policyによって
responseで実値へ戻しません。`fail_mode: open`でもsecret detectorの実行障害はblockします。

### `formats`

`detectors.formats.enabled`はemail、IPv4、Luhn checksumが有効なcredit cardを切り替えます。
`false`ではmaskingと同detectorによる最終guardの両方から外れます。credit cardは既定で
requestをblockします。

### `japanese_pii`

- `enabled`: My Number、電話、郵便番号、複合住所、生年月日、旅券、運転免許、在留カード、
  年金、雇用保険、健康保険、銀行口座等の日本固有detectorをまとめて切り替えます。
- `my_number_restore_policy`: My Number検出時のpolicy。既定`block`。
- `my_number_min_score`: `0.0`から`1.0`。`0.0`はchecksumが有効な全候補、約`0.6`では
  My Numberを示す周辺語を要求します。値を上げるとfalse positiveと同時にrecallも下がります。
- `corporate_number`: 公開情報である法人番号のmasking。既定`false`、必要な場合だけ有効化します。

### `japanese_ner`

- `enabled`: 未登録の一般的な日本語人名、組織名、地名を補完します。既定`true`。
- `model`: `tsmatz/xlm-roberta-ner-japanese`に固定されています。
- `revision`: 採用commit
  `aba094e118d5ffc622e9b25e07edc49f9dd85feb`に固定されています。
- `min_score`: `0.0`から`1.0`。既定`0.7`は同梱evaluation corpusで選定した値です。
- `local_files_only`: 常に`true`。request処理中にdownloadしません。
- `skip_code_contexts`: `true`ならcode、shell、JSON等のcode-like contextでfuzzy NERをskipします。
  dictionaryと決定論的detectorは引き続き検査します。
- `allow_unverified_model`: 常に`false`。manifestにないmodelを通常設定から許可しません。

`enabled: false`はmodelを読み込まずNERを無効化します。dictionaryと決定論的detectorだけでは
未登録の人名・組織名・地名を補完できないため、保護範囲の変更として扱ってください。

## dictionary

辞書は1 fileだけを参照し、schema `version: 1`、`entities`、`patterns`を持ちます。include、
glob、複数file mergeはありません。entityとpatternの`id`は辞書全体で一意にします。

```yaml
version: 1

entities:
  - id: internal_project
    type: PROJECT_NAME
    values: ["極秘計画", "Project Cedar"]
    replacement_profile: prose_identifier
    restore_policy: literal
    priority: 100
    case_sensitive: true

  - id: deployment_password
    type: PASSWORD
    value_from_env: DEPLOYMENT_PASSWORD
    replacement_profile: environment_reference
    restore_policy: env_reference
    priority: 200

patterns:
  - id: internal_ticket
    pattern: 'INC-([0-9]{6})'
    group: 1
    type: CUSTOMER_ID
    replacement_profile: numeric
    restore_policy: literal
    priority: 120
```

### entities

- `id`: 設定内で使う識別子。値ではないため機密を含めないでください。
- `type`: entity category。alias tag、policyの安全下限、診断件数に使います。
- `values`: 完全一致で検出する1つ以上の表記。
- `value_from_env`: 起動時に値を読む環境変数名。`values`と併用できます。
- `replacement_profile`: mask後の構文形状。
- `restore_policy`: responseでの復元方針。
- `priority`: `0`から`1000`、既定`100`。重なる候補の選択に使います。
- `case_sensitive`: 既定`true`。`false`ではUnicode `casefold`で照合します。

`values`または`value_from_env`の少なくとも一方が必要です。API key、password、秘密鍵は平文の
`values`より`value_from_env`を使ってください。環境変数が未設定または空なら起動しません。

`type`には`PERSON`、`ORGANIZATION`、`PROJECT_NAME`、`PRODUCT_NAME`、`HOSTNAME`、`EMAIL`、
`PHONE`、`IP_ADDRESS`、`URL`、`FILE_PATH`、`UUID`、`CREDIT_CARD`、`API_KEY`、
`OAUTH_TOKEN`、`JWT`、`PRIVATE_KEY`、`DB_CONNECTION_STRING`、`PASSWORD`、
`GENERIC_SECRET`、`LOCATION`、`JP_ADDRESS`、各種日本固有ID、`DATE_OF_BIRTH`、
`EMPLOYEE_ID`、`CUSTOMER_ID`等を使用します。custom文字列も受理しますが、組込みのpolicy安全下限や
専用alias tagは適用されません。

### patterns

- `id`、`type`、`replacement_profile`、`restore_policy`、`priority`: entityと同じ意味です。
  `priority`の既定は`150`です。
- `pattern`: Python `re`としてcompileする正規表現。問題のpattern本文はerrorへ表示しません。
- `group`: maskするcapture group番号。既定`0`はmatch全体です。存在しないgroupは拒否します。

既知のcatastrophic backtracking形状はload時に拒否します。ただしuser regexは利用者が管理する
実行可能な検出規則です。合成した長い入力でも`preview`し、必要以上に広いpatternを避けてください。

## replacement_profile

| 値 | mask後の形 |
|---|---|
| `prose_identifier` | `SM_PERSON_...`等の一般identifier |
| `hostname` | `.example.invalid`配下のhostname |
| `email` | `.example.invalid`宛のemail |
| `ipv4` | RFC 5737 documentation address |
| `ipv6` | RFC 3849 documentation address |
| `uuid` | UUID形状 |
| `numeric` | 元値の数字数に合わせた数値 |
| `file_path` | path構造を保つplaceholder |
| `url` | URL component構造を保つplaceholder |
| `environment_reference` | `${SECURITYMASKER_SECRET_...}` |

元値の構造に合わないprofileは、code、JSON、shell commandを壊す原因になります。迷う場合、
通常の固有名詞は`prose_identifier`、credentialは`environment_reference`を使います。

## restore_policy

| 値 | responseでの扱い |
|---|---|
| `literal` | 同じsessionが発行したaliasを元の表記へ完全一致で復元 |
| `env_reference` | `${SECURITYMASKER_SECRET_...}`等のaliasを残し、実値を返さない |
| `redacted` | request時に`[REDACTED]`へ不可逆置換 |
| `block` | 検出したrequestを上流へ送らず拒否 |

組込みの安全下限が`priority`より優先されます。API key、OAuth token、JWT、private key、
password、DB接続文字列等は最低`env_reference`、My Numberとcredit cardは最低`block`です。
辞書やregexで弱いpolicyを指定しても下限より弱くなりません。

## tool_trust

`trusted_local_tools`はtool名の完全一致allowlistです。既定の空listでは、response textは復元しても
tool argument内のaliasは実値へ戻しません。これは外部MCPやprovider-hosted toolへ秘密を渡さない
ためです。

```yaml
tool_trust:
  trusted_local_tools:
    - local_database_client
```

登録すると、その名前のtool argumentへliteral policyの原文を復元して実行させます。processが
ローカルにあるだけでなく、argument、log、telemetry、子process、network送信まで管理できるtool
だけを登録してください。外部MCP、remote tool、provider-hosted tool、実体を確認できない同名toolを
登録してはいけません。tool名を判定できない場合は復元しません。

## 設定変更後の確認

設定や辞書を変更したらGatewayを再起動し、実データを送る前に合成値で確認します。

```console
python3 securitymasker.py config-check
python3 securitymasker.py entities
python3 securitymasker.py preview < synthetic-prompt.txt
python3 securitymasker.py doctor
python3 securitymasker.py doctor --require-ready
```

`entities`は値を表示せずvariant件数だけを出します。`preview`へ実値をcommand line引数で渡すと
shell historyやprocess一覧に残り得るため、標準入力を使ってください。
