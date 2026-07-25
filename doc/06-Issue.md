# 06-Issue — セキュリティ境界を成立させるための是正実装プロンプト

作成日: 2026-07-25

この文書は、現行 SecurityMasker の実装・設定・テストを監査して判明した問題を、別のコーディング AI が
そのまま是正実装へ移れる形にした作業プロンプトである。

以下の「コーディング AI への指示」を、リポジトリのルート
`/Users/ishiura/Developer/Private/SecurityMasker` で実行するエージェントへ渡すこと。

---

# コーディング AI への指示

あなたは SecurityMasker のセキュリティ是正を担当するシニア Python エンジニア兼
セキュリティアーキテクトです。分析や提案だけで終わらず、実装、テスト、ドキュメント更新、
実行結果の確認まで完了してください。

## 0. 最初に守るルール

作業開始前に、次を完全に読んでください。

1. `AGENTS.md`
2. `docs/adr/0006-drop-litellm-purpose-built-proxy.md`
3. `doc/00-First-Order.md` の不変条件、検出、構造保持、障害処理、テスト要件
4. `doc/05-Phase6-Design.md`
5. `docs/threat-model.md`
6. `SECURITY.md`
7. 本文書

判断の優先順位は、必ず次のとおりです。

1. `AGENTS.md` §2 のセキュリティ不変条件
2. 最新 ADR
3. 初期ブリーフの手段記述

特に、次は変更禁止の製品不変条件です。

- 元の機密情報を OpenAI、Anthropic、外部 MCP、Hosted tools、外部ログ等の非信頼領域へ送らない。
- セッションまたはテナントをまたいで秘密、鍵、alias、復元対応表を混ぜない。
- JSON、tool call、コード、shell、diff、patch、URL、file path を構文的に壊さない。
- 不明、未対応、解析不能、検出器障害、セッション障害、暗号障害では既定で fail-closed にする。
- ログ、例外、監査、テレメトリへ原文、復号鍵、平文対応表、認証値を残さない。
- Protocol adapter と masking core の分離を維持する。
- テストには実在人物、実際の公的番号、実際の秘密値を使用しない。

現行正典は ADR-0006 の自作プロキシである。LiteLLM 依存へ戻してはならない。

### 作業権限と禁止事項

- この指示は、実装、テスト、設定例、ドキュメント、必要な ADR の変更を許可する。
- コミット、push、PR作成は行わない。
- 新規の外部依存を追加する場合は、先に必要性、代替案、供給網リスク、固定方法を示してユーザーへ確認する。
- 実際のAPIキーや個人情報をファイル、環境変数、ログ、テストfixtureへ置かない。
- 既存のユーザー変更がある場合は保持し、無関係な変更を巻き戻さない。
- 変更は `apply_patch` 等の差分が明確な方法で行う。

## 1. 現状評価

現行コードは、登録済みの合成氏名・ホスト名を用いた既知の正常系では、OpenAI Responses と
Anthropic Messages の非stream/streamマスク・復元に成功する。監査時点では次が成功している。

```text
pytest tests/unit tests/evaluation -q
133 passed

SM_RUN_LIVE=1 pytest tests/integration/test_live_gateway.py -q
4 passed

ruff check src tests
All checks passed

mypy
Success
```

ただし、これは安全性の十分条件ではない。設定なし、不正本文、未知ルート、未知フィールド、
検出器障害、大入力、複数ターン、外部MCP、テナント、主要な日本固有情報などに、直接的な漏えい経路または
保証欠落がある。現状は本番用セキュリティ境界ではなく、正常系の参照実装と評価する。

以降の修正では、既存テストを通すだけでは不十分である。各問題の再現テストを先に追加し、
是正後に同じテストが安全な結果を証明するようにすること。

## 2. 必須成果

完了時には、少なくとも次を満たすこと。

1. 設定なし、設定誤記、必須環境変数欠落、解析不能本文で外部送信が起きない。
2. サポート対象外の書き込みルートを外部へ透過転送しない。
3. 最終送信する本文と非認証ヘッダーに登録済み機密または高信頼Secretが残っていない。
4. 検査対象外部分を黙って送信しない。
5. 有効化したDetectorが利用不能・故障した場合に送信しない。
6. 主要な開発用Secretと日本固有PIIについて、明示した対応範囲を実装・テスト・文書化する。
7. 外部MCPまたは信頼未設定ツールへ実値を復元しない。
8. セッションとテナントの一意性、安定性、分離を実装する。
9. Redisを選択した場合にGatewayが実際に使い、正しい排他制御を行う。
10. URL、file path、JSON、tool argument、コード文脈で構造を壊さない。
11. 最大入力、最大mapping数、Detector timeout等を実際に強制する。
12. readiness/doctorがマスキング無効やDetector欠落を正常と報告しない。
13. CIとDockerが固定依存を使用する。
14. すべての保証と制限が、コードとテストに一致する。

## 3. 優先度 P0 — 外部漏えいを直ちに止める

以下はリリースブロッカーである。先に修正し、P0がすべて通るまで本番利用可能と宣言しないこと。

### P0-1. 設定未指定時の透過転送を廃止する

現状:

- `src/securitymasker/gateway/runtime.py` の `GatewayRuntime.from_env()` は、
  `SECURITYMASKER_CONFIG` がなければ `engine=None` を作る。
- `src/securitymasker/gateway/app.py` は `engine is None` のとき本文をそのまま上流へ送る。
- `securitymasker gateway` は警告表示だけで起動を継続する。
- `/health` は常に `{"ok": true}` を返す。

期待する修正:

- セキュリティ境界としての既定値は、設定欠落時に起動失敗とする。
- 明示的な無効化モードが必要なら、通常起動とは別の明確な開発専用フラグとし、実プロバイダー宛てでは
  使用できない、または強い警告と安全制約を設ける。
- `fail_mode: open` を実装する場合も、APIキー、秘密鍵、認証トークン、マイナンバー、カード番号等の
  重大Secretは絶対にfail-openしない。
- liveness と readiness を分離し、readiness は engine、設定、必須Detector、session store が
  安全に利用可能なときだけ成功させる。

必須テスト:

- `SECURITYMASKER_CONFIG`なしではGatewayが起動失敗するか、全推論リクエストをローカル拒否する。
- 設定なしでmock upstreamの記録が0件である。
- readinessが失敗し、livenessの意味と区別される。

### P0-2. 不正JSON、非object JSON、圧縮・未知エンコーディングを送信しない

現状:

- `_load_body()` が `None` を返した場合、生bytesをそのまま上流へ転送する。
- JSON配列、JSON文字列、壊れたJSON、gzip等の解析不能本文が漏えい経路になる。
- 上流が400を返しても、その前に信頼境界を越えている。

期待する修正:

- `/responses` と `/messages` は、サポートするContent-TypeとJSON object以外をローカルで拒否する。
- `Content-Encoding`が未対応なら、デコードせず明示的に拒否する。
- UTF-8、JSON parse、schema envelope検証の失敗は外部送信前に安全なエラーへ変換する。
- エラー本文と例外ログへ入力値を含めない。

必須テスト:

- 不正JSON、JSON配列、JSON文字列、空でない非JSON、gzip、無効UTF-8を送っても上流記録が0件。
- ローカルエラー本文とログに合成秘密が存在しない。

### P0-3. 未知の書き込みルートを透過転送しない

現状:

- catch-all `Route("/{path:path}", transparent, methods=["GET", "POST"])` が存在する。
- `/v1/chat/completions`等へ秘密を送るとマスクされない。
- pathに`"/messages"`が含まれるかだけで上流を選ぶため、認証値を誤ったProviderへ送る危険がある。

期待する修正:

- 許可するルート、method、転送先を明示的なテーブルで管理する。
- mask不要で安全と確認したGET、例: model一覧だけを透過許可する。
- 未知POST、PUT、PATCH、DELETE等はローカルで404/405または安全なunsupported responseにする。
- OpenAI認証をAnthropicへ、Anthropic認証をOpenAIへ誤送信しない。
- query stringを扱う場合は、必要な安全ルートのみ正しく保持する。

必須テスト:

- `/v1/chat/completions`、任意の未知POST、`/foo/messages`は上流へ到達しない。
- OpenAI/Anthropicの認証ヘッダーが反対側のmock upstreamへ渡らない。
- 明示許可したmodel一覧取得だけが正常に透過する。

### P0-4. 最終送信ペイロード全体の漏えいガードを実装する

現状:

- `MaskingEngine._verify_no_leak()` は、個別テキスト断片ですでに検出された原文だけを確認する。
- 最終JSON body全体を再検査していない。
- Protocol adapterが知らないfield、schema key、tool name、ID、画像block、Base64等は検査されない。
- `tests/unit/test_anthropic_adapter.py` は画像block内の登録氏名をそのまま残すことを期待している。

設計原則:

- JSON全体を文字列化して一括置換してはならない。
- しかし、最終構造全体を「検出とblockのために」走査することは必要である。
- 変更禁止フィールドに機密があれば、そこを置換せずリクエスト全体をblockする。
- 未知フィールドは、安全を確認できる場合のみ透過する。不変条件1が「未知フィールド透過」より優先する。

期待する修正:

- 既知text fieldは既存adapterで構造保持置換する。
- その後、dict keyを含む全JSON構造のstring部分をblock-onlyモードで走査する。
- 登録辞書、重大Secret、高信頼の決定論的Detectorが残っていれば送信しない。
- JSON escapeされた値、改行、引用符、backslashを含む秘密でも検出できる設計にする。
- 最終直列化bytesにも防御的検査を行い、少なくとも登録値の表現揺れやエスケープ表現を見逃さない。
- body以外の非認証カスタムヘッダーについても登録済み秘密を検査し、検出時はblockする。
- `Authorization`、`x-api-key`等のProvider認証ヘッダーは意図された宛先にのみ透過し、マスク・保存・ログしない。
- `X-SecurityMasker-Session-ID`等の内部ヘッダーは上流へ送らない。

必須テスト:

- 登録秘密を未知field、schema key、tool name、ID、nested description、image metadata、
  Base64風string、custom headerへ入れた場合に上流記録が0件。
- 構造変更禁止fieldで検出した場合、fieldを書き換えずリクエストをblockする。
- 引用符、backslash、改行、tab、Unicode結合文字を含む登録秘密が最終payloadに残らない。
- 正常な未知fieldで機密がない場合は、方針どおり透過できる。

### P0-5. Detectorの走査打ち切りを漏えいに変えない

現状:

- `src/securitymasker/detectors/regex.py` は先頭2,000,000文字だけを検査し、残りを黙って無視する。
- 2,000,001文字目以降に置いた合成OpenAI形式キーが検出0件になることを再現済み。

期待する修正:

- HTTP requestの最大サイズを設定し、外部送信前に強制する。
- Detectorが全入力を安全に検査できない場合は、切り詰めずリクエスト全体をblockする。
- 最大サイズは設定可能にし、安全な既定値、単位、エラーを文書化する。
- tool result、system、instructions、各content blockの合計サイズにも適用する。

必須テスト:

- 上限直前では全文検査される。
- 上限超過時は上流記録0件。
- 検査範囲末尾に配置した秘密を検出できる。
- 「検査だけ切り詰めて送信」は存在しない。

### P0-6. 必須環境変数と有効化Detectorを起動時検証する

現状:

- `value_from_env`が未設定でも`resolved_values()`は空を返し、設定はvalidになる。
- Presidioと日本語NERは、依存・モデル欠落または実行時例外を空結果へ変換する。

期待する修正:

- `value_from_env`を指定した値が存在しない、空、または不正なら起動失敗する。
- `presidio.enabled: true`なら、指定言語・モデルがロードできなければ起動失敗する。
- `ner.model`指定済みなら、pipelineが利用不能な状態で起動成功させない。
- 起動後のDetector例外またはtimeoutは、閉じたfail modeではrequest blockにする。
- optionalで「導入されていないので無効」は、設定自体が無効な場合だけ許す。
- doctor/readinessは実際の設定で同じ検証を行う。

必須テスト:

- 環境変数未設定、Presidio model欠落、NER依存欠落、推論例外で上流記録0件。
- doctor/readinessが明確に失敗する。
- エラーメッセージへ環境変数の実値や入力本文を含めない。

### P0-7. Unicode正規化とalias形式による検出回避を修正する

現状:

- 本文は1 code pointずつNFKC/NFCを適用し、辞書値は全文を正規化する。
- 合成済み「が」を辞書登録しても、`か + U+3099 COMBINING KATAKANA-HIRAGANA VOICED SOUND MARK`
  は検出0件になる。
- `SM_PERSON_ABCDEF`等は、現在のsession mappingに存在しなくても「既存alias」として保護される。
- 同じ文字列を辞書へ秘密として登録しても検出されない。

期待する修正:

- 文字列全体として正規化した結果と、原文への正確なspan mappingを両立する。
- canonical composition/decomposition、NFKC expansion、全角、結合濁点をproperty testする。
- 既存aliasは正規表現だけで保護せず、現在のsessionで実際に発行済みか確認する。
- 他session、期限切れsession、未発行のalias形式文字列は自動保護しない。
- `preserve_aliases`設定を実際に反映する。

必須テスト:

- NFC/NFD/NFKC/NFKD相当の表記揺れで登録秘密を検出する。
- 全角スペース、半角カナ、結合濁点、互換文字から原文spanへ正しく戻る。
- alias形式そのものを秘密登録した場合にマスクまたはblockされる。
- 現sessionの正規aliasだけが二重マスクされない。

### P0-8. 外部MCP・未信頼toolへ実値を復元しない

現状:

- OpenAI function call argumentsとAnthropic `tool_use.input`を無条件にliteral復元する。
- tool名、MCP server、実行場所、信頼レベルを判定していない。
- `docs/threat-model.md`の「外部MCPへの復元は既定無効」は実装されていない。

期待する修正:

- tool/MCP serverごとのtrust policyを設定可能にする。
- 最低限 `trusted_local`、`untrusted_external`、`blocked` を表現する。
- 既定は未信頼とし、明示allowlistされたローカルtoolだけliteral復元を許す。
- 未信頼toolにはaliasまたはenv_referenceのまま渡す。literal実値を出さない。
- Hosted toolsはProvider側で実行されるため、常にマスク済み値のままにする。
- tool trustを判定できない場合は安全側へ倒す。
- response textの表示用復元と、tool argumentsの実行用復元を別ポリシーにする。

必須テスト:

- trusted local toolだけがliteral復元される。
- external MCP、unknown tool、hosted toolは復元されない。
- tool name、tool call ID、schema key、event typeは変更されない。
- stream/non-streamの両方で同じtrust policyが適用される。

### P0-9. セッション・テナント分離をGatewayで実際に強制する

現状:

- Gatewayは常に`InMemorySessionStore()`を使用する。
- `tenant_id`と`user_id`を渡していない。
- `X-SecurityMasker-Session-ID`を無条件に信用する。
- 同じsession IDを複数利用者が使えば同じ対応表を共有する。

期待する修正:

- 単一ユーザーローカルモードとmulti-tenantモードを明示的に分ける。
- multi-tenant時は、信頼できる認証主体からtenant/userを解決し、単なる任意ヘッダーを信頼しない。
- session key space、store key、lock、AAD、audit fingerprintへtenantを一貫して含める。
- session IDだけで別tenantのmappingを取得・復元できない。
- 内部session headerを上流へ転送しない。
- 公開bindを行う場合の認証・ネットワーク制約を明示し、安全でなければ起動を拒否する。

必須テスト:

- 同じsession IDでもtenantが異なれば鍵・alias・mappingが分離する。
- tenant Aがtenant Bのaliasを復元できない。
- 明示tenant情報の偽装を受け入れない構成を検証する。
- localhost単一利用者モードの制約を文書化する。

## 4. 優先度 P1 — 安全性・可逆性・構造保証を完成させる

### P1-1. セッション選択を複数ターンで安定させる

現状:

- custom session headerがないと、`session-id`/`thread-id`より先に`previous_response_id`を採用する。
- `previous_response_id`が毎ターン変われば対応表も毎ターン変わる。
- 明示session headerなしの複数ターンテストがない。

期待する修正:

- 優先順位を安全に再設計する。少なくとも、明示SecurityMasker session、安定したthread/session識別子、
  既存の`previous_response_id -> session` binding、最後に単発ephemeralの順を検討する。
- response IDを同じsessionへbindingする必要がある場合は、response処理時に安全に保存する。
- 安定した識別子が得られないのに過去aliasを含む場合は、黙って新session扱いせずblockまたは明示エラーにする。

必須テスト:

- Responsesの3ターン以上で同じ原文が同じaliasになる。
- `previous_response_id`が変わってもsessionが継続する。
- retry、並列turn、cancel、session expiryを検証する。

### P1-2. 設定スキーマをstrictにし、宣言項目をすべて配線する

現状:

- Pydanticの未知fieldが無視され、`entitiez`、`fail_mdoe`等がvalidになる。
- `version: 999`も受理される。
- Regex設定のprofile、restore policy、capture group等が起動時検証されない。
- 次の設定が実行時未使用である。
  - `fail_mode`
  - `preserve_aliases`
  - `session_idle_ttl`
  - `session_absolute_ttl`
  - `inject_alias_instruction`

期待する修正:

- すべての設定modelで未知fieldを拒否する。
- 現行schema versionだけを受理する。migrationを行うなら明示する。
- enum、duration、size、priority、score、capture group、空値、重複値を起動時検証する。
- Regex profile/policyをEntityConfigと同じ厳密さで検証する。
- 設定済みの全項目を実装へ接続するか、未実装項目をschemaから削除してmigration noteを書く。
- duration文字列を実際の`timedelta`へ変換しstoreへ渡す。
- config exampleを「demo」と「production template」に分けることを検討する。

必須テスト:

- typo、未知field、未知version、invalid enum、invalid duration、missing groupで起動失敗。
- 各設定を変更すると実挙動が変わる。
- config validation結果とGateway runtimeの構成が一致する。

### P1-3. 重大Secretの最低安全ポリシーをpriorityで弱められないようにする

現状:

- overlapはpriority、span length、scoreだけで解決する。
- 高priority辞書を使うと、APIキーの`env_reference`を`literal`へ弱められる。
- マイナンバーやカードの`block`も同様に上書き可能である。

期待する修正:

- Entity typeごとの最低restore policy、安全格子、または「より厳しいpolicyが勝つ」ルールを導入する。
- 少なくともAPI key、OAuth token、JWT、private key、password、DB credentialsは
  `literal`へ弱められない。
- My Numberとcredit cardは、安全な既定blockを任意priorityで回避できない。
- 意図的な例外を許す場合は、独立した危険設定、明示確認、監査イベントを必要とする。

必須テスト:

- 高priorityの弱い辞書/Regexを重ねても重大Secretの最低policyが維持される。
- より厳しい`block`または`redacted`は適用できる。

### P1-4. Secret Detectorの最低対応範囲を満たす

現状の組み込みは、OpenAI/Anthropic風`sk-`、GitHub token、AWS Access Key ID、JWT、PEM、
一部DB URL、basic auth URLに限定される。

最低限、次を検討・実装・テストする。

- OpenAI API keysの現行形式
- Anthropic API keys
- GitHub tokens
- AWS Access Key IDとSecret Access Keyの組み合わせ
- Google Cloud service account JSONの秘密鍵・private key ID等
- Azure client secret、connection string、SAS等
- OAuth Bearer token
- JWT
- PEM/SSH/PGP private keys
- Database、cache、message broker connection string
- Basic authentication URL
- Kubernetes service account token/config内token
- `.env`や設定ファイルの`KEY=value`形式
- Slack、Stripe、npm、PyPI等、採用範囲を明記した一般的token
- context付きgeneric high-entropy secret
- ユーザー定義pattern

注意:

- 汎用entropyだけで通常のhash、commit ID、fixture、圧縮データを秘密扱いしすぎない。
- key name、assignment context、prefix、length、alphabet等を組み合わせる。
- 秘密値全体をエラーやテスト名へ出さない。
- 重大Secretは原則`env_reference`、必要に応じてblockとする。
- 新規scanner依存が必要なら、追加前にユーザー承認とADRを得る。

### P1-5. 入力・Detector・session資源の上限とtimeoutを実装する

現状:

- HTTP body上限なし
- session mapping数上限なし
- Detector timeout/circuit breakerなし
- 任意Regexを同期実行
- NER/Presidioをevent loop上で同期実行
- dictionaryはtermごとのsubstring scan
- built-in email regexは長いASCII文字列で二次的に遅くなる

監査再現:

```text
"a" * 5,000   -> 約 0.024 秒
"a" * 10,000  -> 約 0.097 秒
"a" * 20,000  -> 約 0.379 秒
```

期待する修正:

- request body、各field、tool JSON、stream buffer、session mappingsへ上限を持たせる。
- Detectorごとのtimeoutと障害状態を管理する。
- timeout時はclosed modeで送信しない。
- event loopを長時間blockしない。
- user regexのReDoS対策を設計する。安全な標準ライブラリ実装で不可能なら、代替案と依存追加の承認を求める。
- dictionaryは必要に応じてTrie/Aho-Corasick等へ置き換える。ただし新規依存なしで実装可能性を検討する。
- 最悪ケース文字列をbenchmarkへ追加する。

### P1-6. URL・file path・numeric等のreplacement profileを本当に構造保持させる

現状:

- `url`と`file_path`は単なる`SM_VALUE_<hex>`を返す。
- コメントにはengineがcomponent分割するとあるが実装がない。
- numericは桁数だけを維持し、区切りや具体的文法を維持しない。

期待する修正:

- URLはscheme、host、port、path segment、query value、fragmentを解析し、機密componentだけを置換する。
- URL全体を安全に変換できなければblockする。
- file pathはabsolute/relative、POSIX/Windows、basename、extension、separatorを壊さない。
- hostname、IPv4、IPv6、UUID、email、numericは構文validityをproperty testする。
- low-cardinality profileでalias一意性を保証できない場合は、誇大な保証を書かずADRで制約と代替を決める。

必須テスト:

- URL parser、`json.loads`、shell parser相当、path処理でalias後も構文valid。
- query valueに秘密があってもkeyとURL構造が維持される。
- 引用符、空白、日本語path、Windows drive、UNC、IPv6 URLを検証する。

### P1-7. コード・Markdown・shell・diff・patch文脈を実際に分類する

現状:

- 通常のmessage本文はすべて`prose`としてDetectorへ渡る。
- Markdown code fence内でもNERのcode skipが効かない。
- `JapaneseNerDetector`はコメント上code skipを想定するが実装されていない。

期待する修正:

- 最低限、prose、Markdown code fence、JSON string、YAML scalar、shell、source code、URL、file path、
  tool argument、tool resultを区別する。
- 完全ASTが不要でも、安全な境界抽出と文脈別Detector policyを実装する。
- dictionaryと高信頼Secretはコード内でも検査する。
- 曖昧NERはコード内で既定無効または非常に高い閾値にする。
- 安全なreplacement profileを選べない場合はblockする。

必須テスト:

- Python、TypeScript、JSON、YAML、shell、SQL、Markdown、diff、patchの合成fixture。
- マスク後も構文parse可能、patch適用可能、tool argumentがvalid JSON。
- identifier、quote、escape、改行、indentationを壊さない。

### P1-8. aliasの安全性と保証を見直す

現状:

- prose aliasの初期tokenは16進6桁、24 bit。
- IPv4 aliasは254通り。
- 短いnumeric aliasも空間が小さい。
- session内衝突は検出するがsession間衝突は検出できない。

期待する修正:

- prose、hostname、email、env reference等は十分長いtokenを既定にする。少なくとも誤相関・推測リスクを
  定量評価し、選定根拠をADRへ記録する。
- session間で「絶対に異なる」と数学的に保証できないprofileについて、保証を正確に書き直す。
- IPv4や短いnumericのshape保持とalias空間のtrade-offを設計し、安全な代替profileまたはblock条件を用意する。
- alias collision、同一原文別session、別原文同一sessionを大規模property testする。

### P1-9. Redis storeをGatewayへ配線し、排他制御を修正する

現状:

- Redis実装とCompose serviceはあるがGatewayが使わない。
- `SET NX` lock取得失敗時でもcontext managerが処理本体へ入る。
- `get_or_create`がlock前なので複数workerで異なるsessionを生成できる。
- lock owner tokenなしで、期限切れ後に他ownerのlockを削除する可能性がある。

期待する修正:

- store backendをstrict設定で選択可能にし、Redis選択時にGatewayが実際に使う。
- lock取得までbounded waitするか、安全に失敗する。
- 一意owner tokenで取得・解放し、自分のlockだけを削除する。
- get/create/saveを同じ排他範囲で扱う。
- lock期限更新または処理上限を設計する。
- Redis障害・復号失敗・master key欠落では送信しない。

必須テスト:

- 複数store instance・並列初回登録で同じsession鍵とaliasになる。
- lock競合、期限切れ、owner違い、Redis停止、tamperを検証する。
- tenant namespaceをまたいで混ざらない。

### P1-10. ストリーム異常終了とtool JSONを安全に処理する

現状:

- tool argument deltaをdone/stopまで抑止する。
- done/stopなしでstreamが終わるとbufferがflushされずデータが消える。
- invalid JSONは「fail-closed」ではなく、alias入りの不正JSONをそのまま返す。

期待する修正:

- 正常完了、cancel、network切断、上流エラー、done欠落を状態機械として扱う。
- 未完tool JSONを実行可能なものとして出さない。
- HTTP stream開始後にstatus codeを変更できない点を踏まえ、Provider互換の安全なエラーeventまたは
  明示的なstream termination方針を設計・文書化する。
- partial literal restorationは行わない。
- buffer上限を設け、超過時は安全に停止する。

必須テスト:

- aliasの全分割位置
- UTF-8の全byte分割位置
- tool JSONの全delta分割位置
- done欠落、途中cancel、invalid JSON、複数tool同時進行
- 引用符、backslash、newline、tabを含む復元値

## 5. 日本固有情報の検出を是正する

### 5.1 規定値の現状

設定ファイルを読み込んだ場合、`japanese_pii.enabled`は既定trueだが、`presidio.enabled`はfalse、
`ner.model`はnullである。Gateway自体が設定なしなら、全Detectorが無効になる。

現在の決定論的対応は次に限られる。

- My Number
- 代表的な日本の電話番号
- 文脈付き郵便番号
- 限定形式の日本住所
- 文脈付き生年月日
- ASCII中心のメール
- Luhn-valid credit card
- ユーザー辞書

### 5.2 監査で実行確認した検出結果

すべて合成値を使用した。

```text
未登録漢字氏名                         -> 未検出
未登録ひらがな氏名                     -> 未検出
未登録カタカナ氏名                     -> 未検出
未登録日本法人名                       -> 未検出
日本語 local-part email               -> 未検出
全角＠ + ASCII local-part email       -> 検出
090-1234-5678                         -> 検出
文脈あり 09012345678                  -> 検出
文脈なし 09012345678                  -> 未検出
文脈あり郵便番号                       -> 検出
郵便番号単独                           -> 未検出
東京都渋谷区神宮前1丁目2番3号          -> 検出
無番地住所                             -> 未検出
番地と建物名の間に空白がある住所        -> 番地までの部分検出
文脈あり西暦/和暦生年月日               -> 検出
パスポート番号風                       -> 未検出
運転免許証番号風                       -> 未検出
在留カード番号風                       -> 未検出
基礎年金番号風                         -> 未検出
雇用保険番号風                         -> 未検出
健康保険記号番号風                     -> 未検出
法人番号風                             -> 未検出
適格請求書番号風                       -> 未検出
銀行・支店・口座番号                   -> 未検出
社員番号                               -> 未検出
有効checksumの合成My Number            -> 検出・block
```

### 5.3 氏名・法人名・地名

期待する修正:

- ユーザー辞書を最優先・最信頼とする。
- 未登録の氏名・組織・地名は、設定済み日本語NERで補完できるようにする。
- NER利用を要求したproduction profileでは、モデル欠落をfail-closedにする。
- 漢字、空白あり、全角空白、ひらがな、カタカナ、ローマ字、敬称付きの評価fixtureを増やす。
- 「さくら」「葵」「ひかり」等の曖昧語は文脈なしで高信頼にしない。
- コード、識別子、商品名、プロジェクト名との誤検出を測定する。
- NERだけで安全保証したと宣言しない。

依存上の注意:

- Presidio extraはspaCyモデルを別途必要とする。
- 独立したHugging Face NER用の`transformers`導入経路は現行`pyproject.toml`にない。
- 新規依存追加前にユーザー承認を得る。

### 5.4 日本語メール

期待する修正:

- NFKCで全角英数、全角記号を扱う。
- Unicode local-partまたは国際化domainを対応範囲へ含めるか、未対応時は検出してblockする。
- `山田＠example.co.jp`等を黙って送らない。
- offset mappingを壊さない。

### 5.5 日本住所

現状のregexは原則として都道府県、数字、丁目/番地/号またはhyphenを必要とし、建物名が空白で
分離されると住所全体をmaskしない。

期待する修正:

- 郵便番号、都道府県、市区町村、町域、丁目、番地、号、建物名、階、部屋番号を複合spanへ統合する。
- 都道府県省略、無番地、漢数字、京都の通り名、北海道等の長い町域、建物名との空白を評価する。
- field nameが`address`、`所在地`、`送付先`等の場合、JSON/YAML keyを文脈へ使う。
- 住所の一部だけをmaskし、残りから再構成できる状態を避ける。
- 全体spanを安全に確定できない場合のblock policyを用意する。
- 自治体辞書や郵便番号データセットを導入する場合は、ライセンス、更新方法、サイズ、供給網をADRへ記録し、
  依存追加前に承認を得る。

### 5.6 My Numberと12桁業務番号

現状:

- checksum-valid 12桁なら文脈なしでもDetectionResultを返す。
- 文脈なしscoreは0.5だが、中央に最低閾値がないためblockされる。
- 運転免許証番号、AWS account ID、顧客番号等が偶然checksum一致すると誤blockし得る。

期待する修正:

- entity typeごとの最低score/文脈policyを実装する。
- valid checksum + My Number文脈は高信頼block。
- valid checksumだが文脈なしの12桁をどう扱うか、安全性とprecisionを明示したADRまたはpolicyにする。
- 「scoreを下げるだけで挙動が変わらない」状態をなくす。
- 実在番号を使わず、生成したvalid/invalid checksumと他12桁IDで評価する。

### 5.7 日本固有の公的・業務識別子

以下を、すべて無条件に機密扱いするのではなく、形式、checksum、周辺文脈、組織policyを使って
対応範囲へ追加する。公開情報になり得る法人番号・適格請求書番号等は、policyでmask可否を選べるようにする。

- 日本旅券番号
- 運転免許証番号
- 在留カード番号
- 特別永住者証明書番号
- 健康保険の記号・番号
- 基礎年金番号
- 雇用保険被保険者番号
- 労働保険番号
- 法人番号
- 適格請求書発行事業者登録番号
- 銀行コード、支店コード、口座番号
- 車両登録番号・ナンバープレート
- 社員番号、顧客番号、契約番号

実装条件:

- 正規形式やchecksumは、可能な限り日本政府・所管機関等の一次情報で確認する。
- 形式だけで一般数値と衝突するものは、key名や周辺語を必須にする。
- bare numberを過剰検出しない。
- どのDetectorをbuilt-inにし、どれをユーザーRegex/辞書へ委ねるかを文書化する。
- EntityTypeだけ宣言してDetectorがない状態を、対応済みと表現しない。

### 5.8 日本向けクラウド資源識別子

監査では、次は規定値で未検出だった。

```text
AWS ap-northeast-1 を含む ARN
AWS account ID
GCP asia-northeast1 の project/resource path
Azure Japan の subscription UUID
日本regionを含む内部hostname
```

GCP service account emailはEMAILとして検出されたが、project IDやresource pathは未検出だった。

期待する修正:

- region名自体は通常機密ではないため、単独ではmaskしない。
- account ID、subscription/tenant ID、project ID、ARN、resource name、Secret Manager/Key Vault path、
  internal hostname、cluster名等を組織policyで登録できるようにする。
- AWS/GCP/Azureの資格情報とresource identifierを区別する。
- UUID typeを宣言するだけでなく、必要なDetectorまたはユーザーpattern例を提供する。
- `.co.jp`、`.internal`等を一律機密扱いせず、辞書・suffix policy・文脈を使う。

## 6. 優先度 P2 — 運用・証明・供給網を正す

### P2-1. health、doctor、CLIを実体に合わせる

- livenessとreadinessを分ける。
- doctorは、設定、必須env、Detector model、store、master key、upstream設定、危険なpublic bindを検証する。
- doctorは既定のPresidio設定を別途生成して確認するのではなく、実際に読み込んだlanguage/model/thresholdで検証する。
- `securitymasker run`がsession環境変数を設定するだけで、Codex/Claude Codeのproxy接続やheader伝播が
  成立していない状態を成功扱いしない。設定helper、wrapper、運用手順の責務を明確にする。
- `securitymasker sessions list|inspect|revoke|purge`を実装するか、未実装コマンドを削除して成功終了させない。
- inspectは原文や平文mappingを表示しない。
- revoke/purgeは対象tenant/sessionを厳密に解決する。

### P2-2. metrics/auditをGatewayへ接続する

- request数、masked entity count、block理由、Detector timeout、store error、stream error、latencyを安全なlabelだけで記録する。
- 原文、session ID平文、alias原文対応、auth header、full promptを渡さない。
- 呼び出し側が任意の`safe_fields`を渡せるだけのAPIに依存せず、許可fieldを型またはschemaで制限する。
- metric label cardinalityを制限する。
- exporterを追加する場合は依存・公開範囲を確認する。

### P2-3. 再現可能ビルドを成立させる

現状:

- `requirements.lock`はあるがDocker/CIは範囲指定の`pyproject.toml`からinstallする。
- `python:3.12-slim`と`redis:7-alpine`はfloating tag。
- CI actionもversion tag参照である。

期待する修正:

- 採用した依存管理方針を最新ADRへ明記し、CI/Docker/localで同じlockを使う。
- runtimeとdev/Presidio等のlockを矛盾なく管理する。
- 可能ならhash verificationとimage digest固定を行う。
- production imageへmock upstreamや不要なtest codeを含めない。
- `pyproject.toml`のLiteLLM由来の古いdescription/comment/keywordsを更新する。
- 新規依存を無承認で追加しない。

### P2-4. ドキュメントの保証をコードと一致させる

少なくとも次を更新する。

- `README.md`
- `SECURITY.md`
- `docs/threat-model.md`
- `docs/architecture.md`
- `docs/configuration.md`
- `docs/operations.md`
- `docs/japanese-pii.md`
- `docs/compatibility.md`
- `doc/01-Plan.md`
- 必要なADR
- `AGENTS.md`内の古いLiteLLM構成図・説明

現在の誤解を招く記述例:

- 設定なしを通常のtransparent modeとして推奨
- Detector障害がfail-closedという保証
- 外部MCP復元が既定無効という保証
- Redis/multi-tenantが運用接続済みという保証
- URL/file path profileが構造保持済みという保証
- 「未対応データは黙って送らずblock」という保証
- 8正例・5負例の評価だけで日本語P/R/F1=1.00を一般化
- 存在しない、または名前が変わったtest fileへの参照
- Phase 5/6を、未配線機能まで完了扱いする記述

実装していない保証は削除またはknown limitationへ移し、implemented/tested/unsupportedを区別する。

## 7. 必須テスト計画

実在データを使わず、合成fixtureとproperty-based testで実装すること。

### 7.1 Leakage integration

mock upstreamが受け取った最終bytesとheadersを記録し、すべての合成原文が0件であることを確認する。

対象:

- OpenAI Responses stream/non-stream
- Anthropic Messages stream/non-stream
- system/instructions/messages/history
- tool definition/description/schema
- tool input/result
- code/shell/diff/patch
- unknown fields
- structural fieldsに入った秘密
- malformed/unsupported body
- Unicode/escape表記
- request size境界
- retry/cancel/error
- multi-turn

「adapter出力にない」だけでなく、「mock upstreamが実際に受信した最終HTTP payloadにない」ことを検証する。

### 7.2 Config fail-closed

- configなし
- typo/unknown field
- unsupported version
- missing env
- empty value
- invalid enum
- invalid regex/group
- Detector dependency/model欠落
- Redis/master key欠落
- unsafe public bind

すべてで外部送信0件を確認する。

### 7.3 Session/tenant/concurrency

- 同一session同一秘密 -> 同一alias
- 別session同一秘密 -> 実用上十分に独立したalias
- 同じsession IDでも別tenant -> 完全分離
- 並列初回登録
- 複数worker相当Redis store
- expiry/revoke/purge
- previous response binding
- 他session aliasの復元禁止

### 7.4 Streaming

- aliasの全文字分割位置
- UTF-8の全byte分割位置
- tool JSONの全delta分割位置
- 複数content block/tool
- done欠落
- cancel
- upstream error
- buffer limit
- 特殊文字`"`, `\`, newline, tab

### 7.5 構造保持

- `json.loads`可能
- URL parserでvalid
- IPv4/IPv6/UUID/email/hostnameがvalid
- shell quoting維持
- source code parse可能な範囲
- diff/patch適用可能
- schema key、tool name、IDs、event type不変

### 7.6 日本固有評価

正例と負例を十分増やし、少なくとも次を分けて計測する。

- entity別precision/recall/F1
- dictionaryあり/なし
- NERあり/なし
- prose/code/tool argument
- 表記揺れ
- 文脈あり/なし
- 住所全体spanと部分漏えい
- 公的番号と一般業務番号の衝突
- 日本語email
- cloud resource identifiers

単に全体平均だけで合格させない。高リスクentityはentity別最低recallを持たせる。

### 7.7 Performance/DoS

- 10KB、100KB、1MBまたは採用上限
- 100/1,000/10,000辞書語
- 長いASCII文字列
- `@`なしemail候補
- adversarial user regex
- 長いUnicode結合列
- 大量mapping
- NER timeout
- stream buffer上限

時間だけでなく、上限超過時に外部送信しないことを確認する。

## 8. 実装順序

巨大な一括変更で安全性を見失わないよう、次の順で進める。

### Milestone A — 外部送信ゲートを閉じる

- config必須化
- route allowlist
- invalid/unsupported body拒否
- request size上限
- final payload/block-only leakage guard
- internal header除去
- P0 leakage integration tests

完了条件:

- 解析不能、未知、設定欠落を含む全テストでupstream原文0件。

### Milestone B — 設定・Detector障害をfail-closed化

- strict config
- missing env起動失敗
- Detector readiness/runtime error
- config項目配線
- health/doctor

完了条件:

- 設定ミスまたはDetector障害が正常起動・外部送信に変換されない。

### Milestone C — セッション・テナント・tool trust

- stable multi-turn session
- tenant identity
- Redis配線とlock
- issued-alias membership
- external MCP/tool trust policy

完了条件:

- session/tenant/tool trustの全分離テストが成功する。

### Milestone D — 検出と構造保持

- Secret Detector拡張
- Unicode
- URL/path/numeric profile
- context classification
- 日本固有PII
- entity policy/threshold

完了条件:

- entity別評価と構造保持テストが合格し、部分漏えいがない。

### Milestone E — streaming、運用、供給網、文書

- abnormal stream handling
- limits/timeout/benchmark
- CLI/metrics/audit
- locked build
- docs/ADR更新

完了条件:

- CI相当の全検査、live mock integration、benchmarkが成功する。

## 9. 完了前に実行する検証

リポジトリの実際の方針に合わせてコマンドは調整してよいが、最低限次を実行する。

```bash
ruff check src tests
mypy
pytest tests/unit tests/evaluation -q
SM_RUN_LIVE=1 pytest tests/integration/test_live_gateway.py -q
```

追加したsecurity regression suite、config failure suite、multi-tenant/Redis concurrency suite、
日本固有評価、performance regressionも実行する。

テストがsandboxのlocalhost bind制限だけで失敗した場合は、ユーザー承認を得て必要最小限の権限で再実行する。
実ネットワークや本物のLLMへ、合成値を含めて勝手に送信しない。実Provider E2Eは明示許可がある場合のみ行う。

## 10. 完了報告の形式

最終報告には、次を含める。

1. 修正したセキュリティ不変条件と設計判断
2. P0/P1/P2それぞれの完了・残件
3. 変更ファイル
4. 追加したテストと、漏えい0を確認した経路
5. 実行したコマンドと結果
6. 新規依存・ADR・migrationの有無
7. 残存リスクと、なぜ現在の既定値が安全と言えるか

次の状態では「完了」と報告しないこと。

- P0に未修正の外部送信経路が残る。
- テストをskipしただけで安全扱いする。
- docsだけを変更して実装を変えていない。
- 実装していない保証を文書へ残す。
- mock upstreamの最終payloadを検証していない。
- 設定なし、Detector障害、解析不能本文で外部送信が起きる。

安全性と構造保持が衝突し、既存指示だけでは決められない場合は、勝手にfail-openを選ばず、
選択肢、脅威、推奨案を提示してユーザーの判断を待つこと。
