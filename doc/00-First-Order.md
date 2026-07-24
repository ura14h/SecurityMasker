あなたは、Python、LiteLLM、OpenAI Responses API、Anthropic Messages API、SSEストリーミング、Microsoft Presidio、LLM Gatewayの設計・実装に詳しいシニアソフトウェアエンジニア兼セキュリティアーキテクトです。

以下の要件に基づき、OSSのLiteLLM Proxyへ小規模なカスタムモジュールとして組み込める「SecurityMasker」を設計・実装してください。

単なる概念説明ではなく、実際に動作するコード、設定例、Docker構成、テスト、ドキュメントまで作成してください。LiteLLM本体をforkせず、LiteLLMのアップデートへ比較的追従しやすい薄い拡張モジュールとして実装することを重視します。

不明点がある場合は作業を止めず、安全側の合理的な前提を置いてください。置いた前提はREADMEまたはADRに明記してください。

# 1. プロジェクト名

プロジェクト名は以下とします。

- 製品・機能名: `SecurityMasker`
- Gateway全体を指す名称: `SecurityMasker Gateway`
- Gitリポジトリ名: `securitymasker`
- Pythonパッケージ名: `securitymasker`
- LiteLLMコールバッククラス名: `SecurityMaskerCallback`
- CLIコマンド名: `securitymasker`

「reversible-masker」という名称は使用しないでください。

SecurityMaskerは、単なるPIIマスキング製品ではなく、LLMへ送信する前に機密情報を可逆的な仮名へ置き換え、LLMから戻るレスポンスをローカル環境で復元するセキュリティ境界です。

# 2. 背景と目的

ローカルで動作するCodex CLIまたはCodex IDE連携からOpenAI APIへ送信されるプロンプトには、次のような機密情報が含まれる可能性があります。

- 氏名
- 会社名
- 顧客名
- 日本語住所
- 電話番号
- メールアドレス
- 生年月日
- マイナンバー
- 社員番号
- 顧客番号
- 銀行口座情報
- パスポート番号
- 運転免許証番号
- 健康保険関連番号
- 年金関連番号
- APIキー
- OAuthトークン
- JWT
- 秘密鍵
- 証明書
- データベース接続文字列
- 内部ホスト名
- 内部IPアドレス
- ファイルパス
- Gitリポジトリ名
- 社内プロジェクト名
- 未公開製品名
- ソースコード中の組織固有識別子
- ユーザーが自由に登録した任意の文字列

これらをOpenAIまたはAnthropicなどの外部LLMサーバーへ送信せず、ローカルまたは信頼済みネットワーク内のSecurityMasker Gatewayで仮名化したうえで送信します。

LLMから返されたレスポンス、生成コード、パッチ、ツール呼び出し引数などに仮名が含まれていた場合は、CodexまたはClaude Codeへ返す前に元の値へ復元します。

最重要要件は次の3点です。

1. 元の機密情報を外部LLMサーバーへ送らないこと。
2. マスキングのために生成コード、JSON、シェルコマンド、パッチ、ツール呼び出しを壊さないこと。
3. LiteLLM本体を大規模に変更せず、独立したカスタムモジュールとして保守できること。

# 3. 想定アーキテクチャ

基本構成は次のとおりです。

```text
Codex CLI / Codex IDE
    |
    | OpenAI Responses API
    | POST /v1/responses
    | SSE streaming
    v
LiteLLM Proxy
    |
    +-- SecurityMaskerCallback
    |     |
    |     +-- Request structure walker
    |     +-- Sensitive data detectors
    |     +-- Alias generator
    |     +-- Session mapping store
    |     +-- Response stream transformer
    |     +-- Protocol adapters
    |
    v
OpenAI API
```

Claude Codeにも対応します。

```text
Claude Code
    |
    | Anthropic Messages API
    | POST /v1/messages
    | SSE streaming
    v
LiteLLM Proxy
    |
    +-- SecurityMaskerCallback
    |
    v
Anthropic API
```

同一のSecurityMaskerコアを、次の2つのプロトコルアダプターから利用してください。

- OpenAI Responses API adapter
- Anthropic Messages API adapter

プロトコル固有処理と、機密情報検知・置換・復元処理を分離してください。

# 4. 実装方針

LiteLLM本体はforkしないでください。

原則としてLiteLLM ProxyのカスタムコールバックまたはカスタムGuardrail hookを使用してください。

少なくとも次のタイミングへ介入する必要があります。

- LLM呼び出し直前のリクエスト変更
- 非ストリーミング応答の変更
- ストリーミング応答のリアルタイム変更
- エラー応答の安全な処理
- 必要に応じたレスポンスヘッダー追加

LiteLLMはバージョンや拡張方式によって、ストリーミングhookの名称やシグネチャが異なる可能性があります。例えば、ドキュメント上で次のような名称が使われる場合があります。

- `async_post_call_streaming_hook`
- `async_post_call_streaming_iterator_hook`

実装開始時に、採用するLiteLLM固定バージョンの公式ドキュメントとソースコードを確認してください。

次を実施してください。

1. 動作確認したLiteLLMバージョンを固定する。
2. 使用するhookの正確なシグネチャをテストで固定する。
3. LiteLLM固有部分を`securitymasker/integrations/litellm.py`などの小さなアダプターへ閉じ込める。
4. LiteLLMのhook変更時にコアロジックを変更しなくてよい設計にする。
5. 対応バージョンと確認済みhookを`docs/compatibility.md`へ記録する。

LiteLLM標準のPresidio Guardrailだけに依存しないでください。Presidioは主として「検出器」として利用し、可逆置換、セッション対応表、ストリーミング復元、構造保持はSecurityMasker独自実装で管理してください。

# 5. 信頼境界

以下を信頼済み領域とします。

- CodexまたはClaude Codeが動作するローカルマシン
- SecurityMasker Gateway
- SecurityMaskerのセッションストア
- ユーザーが明示的に信頼したローカルツール

以下を原則として非信頼領域とします。

- OpenAI API
- Anthropic API
- その他の外部LLMプロバイダー
- 外部テレメトリサービス
- 外部ログ基盤
- 外部MCPサーバー
- Hosted Web Searchなど、LLMプロバイダー側で実行されるツール

元の機密情報を非信頼領域へ送信してはいけません。

外部LLMがHosted Toolを直接実行する場合、ツールへ渡される値もマスク済みのままになります。実値が必要なツール処理は、原則としてCodexまたはClaude Code側のローカルツールで実行してください。

MCPサーバーについては、サーバー単位で次の信頼レベルを設定できる設計にしてください。

- `untrusted`: 常にマスク済みデータだけを渡す
- `trusted_internal`: ポリシーで許可されたエンティティのみ復元可能
- `local`: ローカルツールと同様に復元可能
- `blocked`: 機密情報を含む呼び出しを拒否する

初期実装では、外部MCPへの復元は無効をデフォルトにしてください。

# 6. マスキングの基本原則

単純な文字列`<PERSON>`、`<ORGANIZATION>`、`***`などへの置換は使用しないでください。

複数の人物や組織を区別できず、HTML、XML、シェル、TypeScriptなどの構文を壊す可能性があるためです。

同一セッション内では、一つの機密値に対して一つの安定した仮名を割り当ててください。

例:

```text
株式会社極秘技研
    -> SM_ORG_7F3A91

田中太郎
    -> SM_PERSON_2B891C

prod-db01.internal.example
    -> sm-host-7f3a91.example.invalid

taro@example.co.jp
    -> sm-user-2b891c@example.invalid
```

同じセッションで「田中太郎」が複数回現れた場合は、常に同じ仮名へ変換してください。

別セッションでは、同じ機密値でも異なる仮名へ変換してください。

```text
Session A:
田中太郎 -> SM_PERSON_2B891C

Session B:
田中太郎 -> SM_PERSON_81C330
```

これにより、外部LLMプロバイダーが異なるセッションを同じ人物・会社で関連付けにくくします。

# 7. セッション管理

可逆マスキングの対応表は、単なる速度最適化ではなく、SecurityMaskerの中核状態として扱ってください。

最低限、次の双方向マッピングを保持してください。

```text
secret fingerprint -> alias
alias              -> encrypted original secret
```

平文の機密情報をRedisキーやログへ使用しないでください。

推奨方式は次のとおりです。

```text
secret_index = HMAC(session_index_key, normalized_secret + entity_type + profile)
secret_index -> alias

alias -> AES-GCM encrypted original secret
```

要件:

- HMACなしの通常のSHA-256だけで仮名を決定しない
- セッションごとに暗号学的乱数から生成した鍵を使用する
- セッションID自体からセッション鍵を直接導出しない
- HMAC入力にエンティティ型と置換プロファイルを含める
- HMAC衝突や短縮alias衝突を検出する
- 衝突時はalias長を延長する
- aliasから元の値を推測できないこと
- alias生成は並列リクエストでも決定論的であること
- 同一セッション内のリトライでaliasが変わらないこと

セッションキー候補は優先順位を付けて決定してください。

1. `X-SecurityMasker-Session-ID`ヘッダー
2. クライアントが送る会話ID、thread ID、previous response IDなど
3. 認証済みユーザー、クライアント種別、ワークスペース、プロセス起動単位を組み合わせた一時セッション
4. 上記が得られない場合は新規セッションを生成する

Codexではカスタムモデルプロバイダーの環境変数由来HTTPヘッダーを使用できるため、ラッパーCLIを用意してください。

例:

```bash
securitymasker run codex
securitymasker run claude
```

ラッパーはセッションUUIDを生成し、対応する環境変数やカスタムヘッダーを設定してからCodexまたはClaude Codeを起動します。

Codex設定例の方向性:

```toml
model = "securitymasker-openai"
model_provider = "securitymasker"

[model_providers.securitymasker]
name = "SecurityMasker Gateway"
base_url = "http://127.0.0.1:4000/v1"
env_key = "LITELLM_API_KEY"
wire_api = "responses"
supports_websockets = false

[model_providers.securitymasker.env_http_headers]
X-SecurityMasker-Session-ID = "SECURITYMASKER_SESSION_ID"
```

実際のCodexバージョンで正しい設定形式を確認してください。

Claude Codeでは`ANTHROPIC_BASE_URL`と認証変数に加え、利用可能ならカスタムヘッダーを設定してください。

セッションには次の寿命を設定してください。

- アイドルTTL: 設定可能。デフォルト4時間程度
- 絶対TTL: 設定可能。デフォルト24時間程度
- 明示的なセッション終了
- Gateway停止時の破棄
- ユーザーによるCLIからの削除

次のCLIを検討してください。

```bash
securitymasker sessions list
securitymasker sessions inspect <session-id>
securitymasker sessions revoke <session-id>
securitymasker sessions purge
```

`inspect`でも元の機密値は表示せず、エンティティ型、件数、生成時刻、最終使用時刻だけを表示してください。

# 8. セッションストア

初期実装では、単一プロセス向けのインメモリストアを実装してください。

インターフェースは抽象化し、後からRedisへ交換可能にしてください。

例:

```python
class SessionStore(Protocol):
    async def get(self, session_id: str) -> MaskingSession | None: ...
    async def create(self, session_id: str) -> MaskingSession: ...
    async def save(self, session: MaskingSession) -> None: ...
    async def delete(self, session_id: str) -> None: ...
    async def touch(self, session_id: str) -> None: ...
```

実装候補:

- `InMemorySessionStore`
- `RedisSessionStore`

Redis実装では次を守ってください。

- 機密値をRedisキーにしない
- 機密値を平文で保存しない
- AES-GCMなどの認証付き暗号を使う
- Redisバックアップや永続化へ平文を残さない
- TTLをRedis側にも設定する
- テナント間のキー空間を分離する
- 暗号鍵をRedisへ保存しない
- 複数ワーカー間の同時更新を安全に扱う
- alias割り当てに分散ロックまたは原子的操作を使用する

# 9. エンティティごとの置換プロファイル

すべての機密情報を同じ形式へ置換しないでください。

エンティティの構文的性質を維持する「replacement profile」を導入してください。

最低限、次のプロファイルを用意してください。

## 9.1 Prose identifier

人名、会社名、プロジェクト名など。

```text
SM_PERSON_2B891C
SM_ORG_7F3A91
SM_PROJECT_04A91D
```

英数字とアンダースコアを基本とし、多くのプログラミング言語で識別子としても利用可能な形式にしてください。

## 9.2 Hostname

```text
sm-host-7f3a91.example.invalid
```

RFC上のホスト名として不正にならない形式を使用してください。

`.invalid`など、実在接続先として誤用されにくい予約ドメインを優先してください。

## 9.3 Email address

```text
sm-user-2b891c@example.invalid
```

メールアドレスとして構文的に有効な形式を使用してください。

## 9.4 IPv4 / IPv6

文書用に予約されたアドレス範囲を使うことを検討してください。

ただし、IPv4だけではセッション内の一意性確保が難しい可能性があります。衝突管理を実装するか、必要に応じてIPv6文書用範囲を利用してください。

## 9.5 UUID

元の値がUUIDである場合、仮名もUUID形式にしてください。

## 9.6 Numeric identifier

元の値が数字だけであり、受け側が桁数や数字形式を要求する場合のプロファイルです。

- 桁数を維持する
- チェックサムが必要なら有効なチェックサムを生成する
- 同一セッション内で一意にする
- 実在する可能性を下げる
- 数字だけでは安全な仮名を作れない場合は、自動置換せずブロックできること

## 9.7 File path

OS種別とパス形式を維持してください。

例:

```text
/home/tanaka/project-secret/config.yaml
    ->
/home/sm-user-2b891c/sm-project-04a91d/config.yaml
```

Windowsパス、POSIXパス、UNCパスを区別してください。

## 9.8 URL

スキーム、ホスト、パス、クエリの各構造を解析し、機密部分だけを置換してください。

URL全体を無条件に一つのトークンへ置換しないでください。

## 9.9 Environment reference

APIキー、パスワード、秘密鍵など、レスポンスへ実値を戻すべきでない秘密には環境変数参照を使用してください。

```text
sk-real-secret-value
    ->
${SECURITYMASKER_SECRET_A7C391}
```

生成コードでは次のように利用させます。

```bash
curl \
  -H "Authorization: Bearer ${SECURITYMASKER_SECRET_A7C391}" \
  https://example.invalid
```

ローカルツール実行環境へ必要な環境変数を設定できる仕組みを用意してください。

# 10. 復元ポリシー

エンティティごとに復元方法を設定可能にしてください。

```text
literal
env_reference
redacted
block
```

意味:

- `literal`: CodexまたはClaude Codeへ返す直前に元の値へ戻す
- `env_reference`: 元の値へ戻さず、環境変数参照を維持する
- `redacted`: 不可逆な伏字のまま返す
- `block`: そのエンティティを含む処理自体を拒否する

デフォルト例:

- 氏名: `literal`
- 会社名: `literal`
- 顧客名: `literal`
- 住所: `literal`
- 社内ホスト名: `literal`
- 内部ファイルパス: `literal`
- APIキー: `env_reference`
- パスワード: `env_reference`
- 秘密鍵: `env_reference`
- セッショントークン: `env_reference`
- マイナンバー: ポリシーで`literal`または`block`
- クレジットカード番号: 原則`block`または`redacted`

# 11. 検出パイプライン

検出処理は次の優先順位で実行してください。

1. 既存のSecurityMasker aliasの認識
2. ユーザー定義の完全一致辞書
3. ユーザー定義の正規表現
4. APIキー・秘密鍵・接続文字列などのSecret Detector
5. チェックサム付き日本固有番号
6. メール、電話、IP、URL、クレジットカードなどの形式Recognizer
7. 日本語NER
8. 複数結果の統合
9. コードコンテキストと信頼度による最終ポリシー判定

既存aliasは再度マスクしないでください。

```text
SM_ORG_7F3A91
    -X-> SM_ORG_99AA12
```

このような二重マスクを防ぎ、処理を冪等にしてください。

同じ入力へ複数のDetectorが反応した場合は、範囲、Detector優先度、信頼度、エンティティ型を使って統合してください。

部分一致では長い文字列を優先してください。

例:

```text
極秘技研
株式会社極秘技研
```

短い値から置換して、

```text
株式会社SM_ORG_XXXX
```

となってはいけません。

全検知結果を一度収集し、重複範囲を解決してから、一回の変換として置換してください。

登録辞書が大きい場合に備えて、TrieまたはAho–Corasickなどの複数パターン検索を検討してください。

# 12. ユーザー定義辞書

ユーザーが日本語を含む任意の機密情報を登録できるようにしてください。

YAML例:

```yaml
version: 1

defaults:
  fail_mode: closed
  session_idle_ttl: 4h
  session_absolute_ttl: 24h
  preserve_aliases: true

entities:
  - id: customer_company
    type: ORGANIZATION
    values:
      - 株式会社極秘技研
      - 極秘技研
      - 極秘技研株式会社
      - （株）極秘技研
    replacement_profile: prose_identifier
    restore_policy: literal
    priority: 100

  - id: employee_yamada
    type: PERSON
    values:
      - 山田太郎
      - 山田 太郎
      - 山田　太郎
      - やまだたろう
      - ヤマダタロウ
      - Taro Yamada
    replacement_profile: prose_identifier
    restore_policy: literal
    priority: 100

  - id: production_hostname
    type: HOSTNAME
    value_from_env: PROD_DB_HOST
    replacement_profile: hostname
    restore_policy: literal
    priority: 100

  - id: internal_api_key
    type: API_KEY
    value_from_env: INTERNAL_API_KEY
    replacement_profile: environment_reference
    restore_policy: env_reference
    priority: 200

  - id: internal_project
    type: PROJECT_NAME
    values:
      - 桜計画
      - Project Sakura
    replacement_profile: prose_identifier
    restore_policy: literal
```

要件:

- Unicodeを正しく扱う
- NFCまたはNFKC正規化を設定可能にする
- 全角・半角の扱いを設定可能にする
- 大文字・小文字を区別するか設定可能にする
- スペースの違いを別名として扱える
- 正規化は検出用途に使い、復元時には元の表記を保持する
- YAMLに平文を置かず、環境変数やSecret Managerから取得できる
- 同一エンティティに複数表記を登録できる
- 複数表記を同じaliasへまとめるか、表記ごとに別aliasへするか設定できる
- 変更時に設定検証を行う
- 不正な正規表現や競合設定を起動時に検出する

CLI例:

```bash
securitymasker config validate
securitymasker entities list
securitymasker entities test "山田太郎の電話番号は090-1234-5678です"
securitymasker entities add
securitymasker doctor
```

CLIの標準出力にも、デフォルトでは元の機密値を表示しないでください。

# 13. Presidioの位置付け

Microsoft Presidioは主に検出エンジンとして利用してください。

Presidio Anonymizerによる`<PERSON>`などへの標準置換を、SecurityMaskerの中心機能にしないでください。

SecurityMasker自身が以下を管理します。

- alias生成
- 双方向マッピング
- セッションスコープ
- 暗号化
- 構造保持
- ストリーミング復元
- ツール引数復元
- 復元ポリシー
- エンティティ別置換形式

Presidioからは、主として次の情報を受け取ります。

- エンティティ型
- 開始位置
- 終了位置
- 信頼度
- Recognizer名
- 文脈情報

PresidioはインプロセスPythonライブラリまたは別コンテナのAnalyzer Serviceとして利用可能な設計にしてください。

MVPではメンテナンス量を抑えるため、次のどちらかを選び、その理由をADRへ記録してください。

- LiteLLMと同じPython環境でPresidio Analyzerを利用
- 公式Presidio AnalyzerコンテナへHTTP接続

Presidio Anonymizerコンテナは、SecurityMasker独自置換で不要なら導入しなくて構いません。

# 14. 日本語PII対応

Presidio標準だけで日本固有情報を十分に検知できるとは仮定しないでください。

日本語対応は次の三層としてください。

1. ユーザー登録済み辞書
2. 形式・チェックサム・文脈語に基づくRecognizer
3. 日本語NER

## 14.1 氏名

未登録氏名の検知には日本語対応NERモデルを使用できるようにしてください。

モデルは設定可能とし、特定モデルをコアへハードコードしないでください。

Hugging Faceのtoken classificationモデルなどを接続できるRecognizerアダプターを用意してください。

次の表記を評価してください。

```text
山田太郎
山田 太郎
山田　太郎
やまだたろう
ヤマダ タロウ
Taro Yamada
山田さん
田中部長
```

「さくら」「ひかり」「葵」など、人名と一般名詞・製品名・プロジェクト名が曖昧な語では誤検知が起きるため、NERだけで高信頼判定しないでください。

次の文脈語でスコアを上げてください。

```text
氏名
名前
契約者
申込者
担当者
代表者
連絡先
お客様
患者
従業員
```

コード領域では、未登録氏名のNERをデフォルトで無効または非常に保守的にしてください。

## 14.2 住所

日本語住所は単一Recognizerだけで検知しないでください。

次を組み合わせてください。

- 郵便番号
- 都道府県辞書
- 市区町村辞書
- 日本語LOCATION NER
- 丁目、番地、号
- 建物名
- 階
- 部屋番号
- 「住所」「所在地」「送付先」などの文脈語

例:

```text
〒150-0001 東京都渋谷区神宮前1丁目2番3号 秘密ビル401
```

個別検知結果を統合し、可能であれば住所全体を一つの`JP_ADDRESS`エンティティとして扱ってください。

住所の一部だけがマスクされ、残りから住所が推測できる状態を避けてください。

## 14.3 電話番号

日本の電話番号として、少なくとも次を評価してください。

```text
03-1234-5678
090-1234-5678
080-1234-5678
0120-123-456
+81 90 1234 5678
03（1234）5678
内線1234
```

次の文脈語で信頼度を上げてください。

```text
電話
TEL
携帯
連絡先
内線
phone
mobile
fax
```

コード中のビルド番号や顧客番号を電話番号として誤検知しないようにしてください。

## 14.4 メールアドレス

通常のメールアドレスに加え、全角記号を含む表記の事前正規化を検討してください。

```text
taro@example.co.jp
山田＠example.co.jp
```

正規化後の位置を原文位置へ戻せるオフセットマッピングを保持してください。

## 14.5 マイナンバー

`JP_MY_NUMBER`カスタムRecognizerを実装してください。

要件:

- 全角数字を正規化
- 空白またはハイフン区切りを許容
- 12桁であることを確認
- 公式チェックディジット方式を実装
- 文脈語で信頼度を調整
- チェックサム不一致は検知しない
- 単なる12桁業務IDとの誤検知を抑える
- 単体テストに既知の有効・無効番号を使う
- テストデータは実在人物の番号を使用しない

文脈語例:

```text
マイナンバー
個人番号
個人番号カード
番号カード
扶養控除
源泉徴収
税
社会保障
```

## 14.6 郵便番号

`JP_POSTAL_CODE`を実装してください。

単なる`123-4567`だけでは商品番号などと衝突するため、次を利用してスコアを調整してください。

- `〒`
- 「郵便番号」
- 後続する都道府県名
- 後続する市区町村
- 任意の郵便番号辞書照合

## 14.7 生年月日

通常の日付と生年月日を区別してください。

```text
2026年7月24日にリリース
1985年4月12日生まれ
```

次の文脈がある場合に`DATE_OF_BIRTH`へ昇格させてください。

```text
生年月日
誕生日
生まれ
DOB
年齢
```

## 14.8 その他の日本固有エンティティ

拡張しやすいRecognizer基盤を用意し、少なくとも仕様とテスト方針を示してください。

候補:

- `JP_PASSPORT_NUMBER`
- `JP_DRIVER_LICENSE_NUMBER`
- `JP_HEALTH_INSURANCE_ID`
- `JP_PENSION_NUMBER`
- `JP_BANK_ACCOUNT`
- `JP_BANK_BRANCH`
- `JP_CORPORATE_NUMBER`
- `EMPLOYEE_ID`
- `CUSTOMER_ID`

法人番号は公開情報の場合がありますが、SecurityMaskerでは法的区分ではなく組織ポリシーによりマスク対象へ設定できるようにしてください。

# 15. Secret Detector

PIIだけでなく、開発環境固有のSecretsを検知してください。

最低限、次を対象としてください。

- OpenAI API keys
- Anthropic API keys
- GitHub tokens
- AWS access keys
- Google Cloud service account key fragments
- Azure credentials
- JWT
- OAuth bearer tokens
- PEM private keys
- SSH private keys
- Database connection strings
- Basic authentication URLs
- Kubernetes tokens
- Generic high-entropy secrets
- `.env`形式のシークレット
- ユーザー定義パターン

Secret Detectorはプラガブルにしてください。

```python
class SensitiveDataDetector(Protocol):
    async def detect(
        self,
        text: str,
        context: DetectionContext,
    ) -> list[DetectionResult]: ...
```

実装候補:

- `DictionaryDetector`
- `RegexDetector`
- `PresidioDetector`
- `SecretPatternDetector`
- `JapaneseNerDetector`
- `CompositeAddressDetector`

外部Secret Scannerライブラリを採用する場合でも、その依存をアダプターへ閉じ込めてください。

APIキー、パスワード、秘密鍵は、原則として`env_reference`へ変換してください。

# 16. コードと構造を壊さない処理

JSON全体を文字列化して一括置換してはいけません。

プロトコル構造を再帰的に走査し、マスク可能な値だけを処理してください。

原則としてマスク対象:

- ユーザー入力テキスト
- developerまたはsystemメッセージ内のユーザー由来部分
- 会話履歴のテキスト
- コードブロック
- ファイル内容
- パッチ内容
- ツール実行結果
- ツール引数の文字列値
- MCP tool resultの文字列
- URLやパスの値部分
- 画像OCR結果。ただし画像対応を実装する場合のみ

原則として変更禁止:

- model名
- response ID
- message ID
- tool call ID
- tool名
- function名
- protocolの`type`
- SSEイベント名
- JSON Schemaのプロパティ名
- JSON Schemaの型定義
- role
- status
- finish reason
- usage
- 認証ヘッダー
- LiteLLM内部メタデータ
- エラーコード
- Provider固有の制御フィールド

ツール定義の説明文に機密情報が含まれる場合は、説明文だけをマスク可能としてください。ツール名とJSON Schemaキーはデフォルトでは変更しないでください。

ツール引数についてはキーを変更せず、値だけを再帰的に処理してください。

例:

```json
{
  "tool_name": "connect_database",
  "arguments": {
    "hostname": "prod-db01.internal.example",
    "username": "tanaka",
    "query": "SELECT * FROM secret_customer"
  }
}
```

外部LLMへ送る形式:

```json
{
  "tool_name": "connect_database",
  "arguments": {
    "hostname": "sm-host-7f3a91.example.invalid",
    "username": "SM_PERSON_2B891C",
    "query": "SELECT * FROM SM_TABLE_04A91D"
  }
}
```

ただし、SQL識別子やプログラム識別子を置換する場合は、置換後も対象言語で有効な識別子であることを確認してください。

安全な置換形式を生成できない場合は、次のいずれかにしてください。

- リクエストをfail-closedで拒否
- ユーザーに対象を辞書登録させる
- 対象セグメントだけローカル処理へ回す
- ポリシーにより監査モードへ移行

不明なまま外部LLMへ送信してはいけません。

# 17. コード領域でのNER

自然言語NERをソースコード全体へ無条件に適用しないでください。

例えば次は人名、製品名、クラス名、変数名のいずれにもなり得ます。

```text
Sakura
Tanaka
Tokyo
User
Company
```

コード領域では次の優先順位にしてください。

1. ユーザー辞書の完全一致
2. 高精度Secret Detector
3. ホスト、メール、IP、URLなどの高精度形式Recognizer
4. 明示的な組織固有識別子ルール
5. NERはデフォルト無効または高閾値

Markdownコードフェンス、JSON、YAML、シェル、SQL、ソースファイルなどを可能な範囲で識別してください。

すべての言語に完全な構文解析器を実装する必要はありませんが、少なくとも次のコンテキストを区別できる設計にしてください。

- prose
- markdown code block
- JSON string value
- YAML scalar
- shell command
- source code
- URL
- file path
- tool argument
- tool result

検出結果には`context_kind`と`replacement_profile`を持たせてください。

# 18. リクエスト処理

送信前処理は次の順序としてください。

```text
1. セッション特定
2. リクエストプロトコル特定
3. マスク対象フィールド抽出
4. 既存aliasの保護
5. Unicode正規化と位置マッピング
6. 全Detector実行
7. 検出結果統合
8. ポリシー判定
9. alias取得または新規生成
10. 構造を保った置換
11. 送信直前の漏えい再スキャン
12. 外部LLMへ送信
```

送信直前に、登録済み機密情報と高信頼Secretが残っていないか再スキャンしてください。

漏えい候補が残っていた場合は、デフォルトでfail-closedとしてください。

例外、Detector障害、セッションストア障害、暗号化障害が起きた場合も、デフォルトでは外部LLMへ元データを送らないでください。

# 19. レスポンス処理

外部LLMからのレスポンスでは、このセッションで実際に生成されたaliasだけを復元してください。

別セッションのaliasや、偶然同じ形式の文字列は復元してはいけません。

処理対象:

- 通常テキスト
- Markdown
- コード
- diff
- patch
- tool call arguments
- function call arguments
- MCP tool instructions
- JSON出力
- エラーメッセージ内のモデル生成部分

変更禁止:

- response ID
- event ID
- tool call ID
- event type
- status
- protocol fields
- usage
- Providerメタデータ

復元後にJSONやプロトコル構造が壊れていないか検証してください。

# 20. ストリーミング処理

CodexとClaude Codeの双方で、SSEストリーミングを扱ってください。

単純にチャンクごとに`replace()`してはいけません。

aliasがチャンク境界で分割される可能性があります。

例:

```text
chunk 1: "接続先は sm-host-7f"
chunk 2: "3a91.example.invalid です"
```

通常テキストでは、最大alias長を考慮したcarry bufferを持つストリーミング複数パターン置換器を実装してください。

要件:

- aliasが任意の位置で分割されても復元できる
- UTF-8マルチバイト文字を壊さない
- SSEイベント境界を壊さない
- 未知のSSEイベントを透過的に通す
- イベント順序を維持する
- usageイベントを変更しない
- stream終了時にcarry bufferをflushする
- キャンセル時にセッション状態を破損しない
- リトライ時に二重復元しない

# 21. ツール引数のストリーミング

OpenAI Responses APIおよびAnthropic Messages APIでは、ツール引数のJSONが複数deltaへ分割される可能性があります。

aliasを元の値へ単純置換すると、元の値に次の文字が含まれる場合にJSONを壊す可能性があります。

- `"`
- `\`
- 改行
- タブ
- Unicode制御文字

そのためツール引数は、原則として次の処理をしてください。

```text
1. tool call IDごとに引数deltaを蓄積
2. 引数完了イベントを待つ
3. JSONとしてparse
4. 文字列値を再帰的に復元
5. JSONとして再serialize
6. クライアントへ整合したイベント列として返す
```

可能なら、クライアント互換性を保った単一deltaまたは安全な再分割deltaとして返してください。

LiteLLMとCodex／Claude Codeの実際の挙動を統合テストで確認してください。

引数完了までの小さな遅延は許容します。壊れたJSONや不完全なコマンドを返すことより正しさを優先してください。

ツール引数をバッファする間も、通常テキストのストリームは可能な限り逐次返してください。

# 22. OpenAI Responses APIアダプター

OpenAI Responses API用アダプターを実装してください。

少なくとも次を考慮してください。

- `input`
- message content
- input text
- output text
- tool definitions
- function call arguments
- function call argument deltas
- tool output
- `previous_response_id`
- streaming events
- response completion
- error events
- reasoning関連フィールド
- Hosted tools
- MCP関連イベント

イベント名や構造は固定観念で実装せず、採用するOpenAI SDK、Codex、LiteLLMのバージョンで実際のイベントを記録して確認してください。

未知イベントは、機密情報を含む可能性がある既知のテキストフィールドだけを安全に処理し、それ以外は透過的に通してください。

Codexとの初期対応ではWebSocketを無効にし、SSEへ限定して構いません。

```toml
supports_websockets = false
```

WebSocket対応は別フェーズとし、READMEに明記してください。

# 23. Anthropic Messages APIアダプター

Claude Code向けにAnthropic Messages APIアダプターを実装してください。

少なくとも次を考慮してください。

- `system`
- `messages`
- content blocks
- text blocks
- tool use blocks
- tool result blocks
- input JSON delta
- content block start
- content block delta
- content block stop
- message start
- message delta
- message stop
- error events
- thinking blocks
- citationsやその他の追加ブロック
- `anthropic-beta`ヘッダー
- Claude Code固有の追加ヘッダー

Claude Codeは更新により新しい機能やヘッダーを追加する可能性があります。

SecurityMaskerが理解しないヘッダーやイベントを不用意に削除しないでください。

認証情報を除き、必要なProvider機能ヘッダーを透過的に転送してください。

対応バージョンを`docs/compatibility.md`へ記録し、Claude Code更新時に実行できる互換性テストを用意してください。

# 24. モデルへのalias保持指示

外部LLMへ送るdeveloperまたはsystem指示へ、機密値を含まない次の趣旨の説明を追加できるようにしてください。

```text
Identifiers beginning with the configured SecurityMasker alias prefix are
opaque placeholders. Preserve them exactly. Do not translate, normalize,
shorten, split, pluralize, change case, or invent variants. When producing
code, patches, paths, commands, JSON, or tool arguments, copy each placeholder
verbatim.
```

この指示は設定で有効・無効にできるようにしてください。

既存のsystemまたはdeveloper指示を上書きせず、追加コンテキストとして挿入してください。

この指示だけに正しさを依存せず、復元処理側でもalias変形を検出してください。

aliasが小文字化、分割、翻訳、複数形化などで変形された場合は、曖昧な自動復元を避けてください。

- 通常テキストでは警告付きで未復元
- ツール引数ではfail-closed
- 設定により近似復元を許可可能
- 近似復元を行った場合は監査イベントへ記録

# 25. ログと監査

SecurityMasker、LiteLLM、Presidio、Webサーバー、例外トレースのいずれにも元の機密情報を記録しないでください。

本番環境では、LiteLLMの詳細デバッグログを有効にしないでください。

ログに記録してよい情報:

- request ID
- session IDの不可逆fingerprint
- tenant ID
- user ID
- client type
- protocol
- model alias
- 検出エンティティ型
- Detector名
- 信頼度
- 検出件数
- mask件数
- block件数
- 処理時間
- エラー種別
- fail-openまたはfail-closedの結果
- aliasの不可逆fingerprint

ログに記録してはいけない情報:

- 元の機密値
- 暗号化前の対応表
- 復号鍵
- APIキー
- Authorizationヘッダー
- ユーザーの完全なプロンプト
- 完全なモデルレスポンス
- 復元済みツール引数
- 復元済みシェルコマンド

LiteLLMのログ機能がSecurityMaskerのpre-call hookより前にraw requestを保存する可能性を調査してください。

採用バージョンで次をテストしてください。

- LiteLLM内部ログへraw requestが残らない
- Langfuseなどの外部連携へraw requestが送られない
- エラー時にもraw requestが残らない
- tracing metadataに秘密が含まれない

保証できない場合は、そのログ連携を自動的に無効化するか、デプロイ時検証を失敗させてください。

# 26. エラー処理

デフォルトはfail-closedとしてください。

次の障害時に元のリクエストを外部へ送らないでください。

- Presidio停止
- セッションストア停止
- 暗号化失敗
- alias生成失敗
- Detector例外
- リクエスト構造解析失敗
- 送信直前の漏えい再スキャン失敗
- ストリーム変換失敗
- ツール引数JSONの復元失敗
- 不明な機密データ形式を検出
- 復元不能なalias変形

エラー応答にも機密値を含めないでください。

エラーには、ユーザーが対処できる安全な情報を含めてください。

例:

```text
SecurityMasker blocked this request because a sensitive value could not be
safely replaced inside a tool argument. Entity type: JP_MY_NUMBER.
Request ID: ...
```

設定により開発環境だけfail-openを許可しても構いませんが、明示設定が必要です。

fail-open時にも、重大Secret、秘密鍵、APIキー、マイナンバーなどは必ずblockできるようにしてください。

# 27. ツール承認との関係

SecurityMaskerはCodexまたはClaude Codeのツール承認機構を回避してはいけません。

モデルがaliasを含む危険なシェルコマンドを返し、SecurityMaskerが実値へ復元した場合でも、実行承認は通常どおりクライアント側で行われる必要があります。

SecurityMasker自身は次を行わないでください。

- ツール呼び出しの自動承認
- シェルコマンドの自動実行
- ファイル変更の自動適用
- 復元済みAPIキーのコマンドライン引数埋め込み
- 秘密値のログ出力

APIキーなどは環境変数参照を優先し、プロセス一覧やシェル履歴へ値が露出しないようにしてください。

# 28. 推奨パッケージ構成

次のような責務分離を基本としてください。必要に応じて改善して構いません。

```text
securitymasker/
├── pyproject.toml
├── README.md
├── SECURITY.md
├── LICENSE
├── docker-compose.yml
├── config/
│   ├── litellm.example.yaml
│   └── securitymasker.example.yaml
├── docs/
│   ├── architecture.md
│   ├── threat-model.md
│   ├── compatibility.md
│   ├── operations.md
│   ├── configuration.md
│   ├── japanese-pii.md
│   └── adr/
├── src/
│   └── securitymasker/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── errors.py
│       ├── logging.py
│       ├── models.py
│       ├── policy.py
│       ├── normalization.py
│       ├── ranges.py
│       ├── engine.py
│       ├── detectors/
│       │   ├── base.py
│       │   ├── dictionary.py
│       │   ├── regex.py
│       │   ├── presidio.py
│       │   ├── secret_patterns.py
│       │   ├── japanese_ner.py
│       │   ├── japanese_phone.py
│       │   ├── japanese_postal_code.py
│       │   ├── japanese_my_number.py
│       │   ├── japanese_address.py
│       │   └── date_of_birth.py
│       ├── aliases/
│       │   ├── factory.py
│       │   ├── profiles.py
│       │   ├── collisions.py
│       │   └── validation.py
│       ├── sessions/
│       │   ├── models.py
│       │   ├── store.py
│       │   ├── memory.py
│       │   ├── redis.py
│       │   └── crypto.py
│       ├── protocols/
│       │   ├── base.py
│       │   ├── sse.py
│       │   ├── openai_responses.py
│       │   ├── anthropic_messages.py
│       │   └── structured_walker.py
│       ├── streaming/
│       │   ├── text_replacer.py
│       │   ├── tool_arguments.py
│       │   └── buffers.py
│       └── integrations/
│           ├── litellm.py
│           ├── codex.py
│           └── claude_code.py
└── tests/
    ├── unit/
    ├── integration/
    ├── e2e/
    ├── fixtures/
    └── evaluation/
```

# 29. データモデル

最低限、次のようなモデルを設計してください。

```python
@dataclass(frozen=True)
class DetectionResult:
    entity_type: str
    start: int
    end: int
    score: float
    detector: str
    context_kind: str
    replacement_profile: str
    restore_policy: str
    normalized_value: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
@dataclass
class AliasMapping:
    entity_type: str
    alias: str
    encrypted_original: bytes
    original_fingerprint: str
    replacement_profile: str
    restore_policy: str
    created_at: datetime
    last_used_at: datetime
@dataclass
class MaskingSession:
    session_id: str
    tenant_id: str | None
    user_id: str | None
    client_type: str
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    mappings_by_fingerprint: MutableMapping[str, AliasMapping]
    mappings_by_alias: MutableMapping[str, AliasMapping]
@dataclass(frozen=True)
class MaskingPolicyDecision:
    action: Literal["mask", "block", "ignore", "audit"]
    replacement_profile: str | None
    restore_policy: str | None
    reason: str
```

型安全性を重視し、`mypy`または`pyright`で検査してください。

# 30. テスト戦略

テストでは、実在人物や実際の秘密情報を使用しないでください。

## 30.1 Unit tests

最低限、次をテストしてください。

- 同じ秘密が同じセッションで同じaliasになる
- 同じ秘密が別セッションで異なるaliasになる
- aliasから元の値を復元できる
- alias衝突を検出できる
- HMAC入力がエンティティ型で分離される
- 復元ポリシーが適用される
- Unicode NFC/NFKC
- 全角・半角
- 日本語スペース
- 長い一致が短い一致より優先される
- 重複範囲が正しく解決される
- 既存aliasが二重マスクされない
- メールアドレス
- 日本電話番号
- 郵便番号
- マイナンバーチェックディジット
- 生年月日文脈
- APIキー
- JWT
- PEM秘密鍵
- ホスト名
- ファイルパス
- URL
- JSON文字列エスケープ
- TTL
- セッション削除
- 暗号化改ざん検知

## 30.2 Streaming tests

任意の文字位置でaliasを分割してください。

Property-based testを使い、alias長のすべての境界位置をテストしてください。

```text
SM_ORG_7F3A91
```

を次のようにすべての位置で分割しても復元できる必要があります。

```text
S | M_ORG_7F3A91
SM | _ORG_7F3A91
SM_ | ORG_7F3A91
...
```

次もテストしてください。

- 複数aliasが連続する
- aliasのprefixだけが出力される
- ストリーム終了時に未完prefixが残る
- UTF-8日本語との境界
- SSEコメント行
- 複数行data
- eventフィールド
- retryフィールド
- unknown event
- cancellation
- upstream error

## 30.3 Tool call tests

- tool名が変更されない
- tool call IDが変更されない
- JSON Schemaキーが変更されない
- 引数値だけが復元される
- aliasが複数deltaへ分割される
- 元の秘密にダブルクォートが含まれる
- 元の秘密にバックスラッシュが含まれる
- 元の秘密に改行が含まれる
- ネストした配列とオブジェクト
- 複数の同時tool call
- tool call完了順序が前後する
- 不完全JSONを安全に拒否する
- 復元後JSONがparse可能
- シェルコマンドが構文的に維持される
- diffまたはpatchが元ファイルへ適用可能

## 30.4 Concurrency tests

- 同一セッションの並列リクエスト
- 別セッションの並列リクエスト
- 同じ秘密の同時初回登録
- 異なるテナント
- 複数LiteLLM worker
- Redis使用時の競合
- リトライ
- タイムアウト
- セッション期限切れとの競合

セッション間で機密情報やaliasが混ざらないことを必ず検証してください。

## 30.5 Leakage tests

モックの外部LLMサーバーを作成し、SecurityMaskerから実際に送信されたHTTPリクエストを取得してください。

次を自動検証してください。

- 元の氏名が含まれない
- 元の住所が含まれない
- 元の電話番号が含まれない
- 元のメールが含まれない
- 元のマイナンバーが含まれない
- 元のAPIキーが含まれない
- 元のホスト名が含まれない
- 元のファイルパスが含まれない
- Authorization以外のヘッダーに秘密が漏れない
- URLクエリへ秘密が漏れない
- LiteLLMログへ秘密が漏れない
- SecurityMaskerログへ秘密が漏れない
- 例外トレースへ秘密が漏れない

「モデルへ送信した最終ペイロードに元の機密値が存在しない」ことを、最重要の受け入れテストとしてください。

## 30.6 Codex E2E

CodexまたはCodex互換のリクエストfixtureを用いて次を確認してください。

- `/v1/responses`
- SSE
- 通常回答
- コード生成
- patch生成
- shell tool call
- tool resultの再送
- 会話履歴
- retry
- cancellation
- response ID
- previous response ID

Codexの実バージョンを使うテストはoptional integration testとして分離してください。

## 30.7 Claude Code E2E

Claude CodeまたはAnthropic Messages互換fixtureで次を確認してください。

- `/v1/messages`
- SSE
- content blocks
- tool use
- tool result
- input JSON delta
- thinking blocks
- beta headers
- unknown block type
- Claude Codeの更新で追加されたフィールドの透過転送

# 31. 日本語評価コーパス

匿名化された日本語評価fixtureを作成してください。

自然文、メール、チャット、ログ、設定ファイル、ソースコードを混ぜてください。

正例:

```text
担当者は山田太郎です。
連絡先は090-1234-5678です。
メールはtaro.yamada@example.co.jpです。
住所は〒150-0001 東京都渋谷区神宮前1-2-3です。
個人番号はテスト用の有効チェックサム番号です。
```

負例:

```text
リリース日は2026年7月24日です。
build_idは09012345678です。
クラス名はSakuraServiceです。
チケット番号は123-4567です。
テストデータのUserクラスを生成してください。
```

次を測定してください。

- precision
- recall
- F1
- false positive件数
- false negative件数
- エンティティ別precision/recall
- proseとcodeの別評価
- 辞書あり／なしの比較
- NERあり／なしの比較

機密情報の外部送信防止ではrecallが重要ですが、コードを壊さないためprecisionも重要です。

エンティティ型ごとに閾値を変更可能にしてください。

# 32. パフォーマンス要件

目標値を設定し、ベンチマークを用意してください。

初期目標例:

- 辞書とRegexだけの場合、通常プロンプトの追加p95レイテンシを小さく保つ
- PresidioまたはNER利用時もタイムアウトを設定
- alias復元はマッピング数に対して効率的であること
- ストリームの通常テキストは不要に全文バッファしない
- ツールJSONだけ必要範囲でバッファする
- 大きなソースコード入力で極端な二次計算量にならない
- セッション内の登録数に上限を設定可能
- 一リクエストの最大入力サイズを設定可能
- Detectorごとにタイムアウトとサーキットブレーカーを設定可能

ベンチマーク対象:

- 10 KB
- 100 KB
- 1 MB
- 100件の登録秘密
- 1,000件の登録秘密
- 10,000件の登録秘密
- 同時10リクエスト
- 同時100リクエスト

# 33. セキュリティ上の注意

次を脅威モデルへ含めてください。

- 外部LLMによるaliasの意図的改変
- Prompt injection
- 別セッションaliasの注入
- alias collision
- 辞書攻撃
- キャッシュ窃取
- Redis漏えい
- メモリダンプ
- ログ漏えい
- エラーレスポンス漏えい
- テレメトリ漏えい
- timing side channel
- tenant間データ混在
- 悪意あるユーザーによる他人のalias推測
- 不正なUnicode
- confusable文字
- Base64やURL encodingによる機密情報
- 圧縮・バイナリ・画像内の機密情報
- 巨大入力によるDoS
- catastrophic backtrackingを起こす正規表現
- 復元された秘密を含む危険なshell command
- Hosted toolへのマスク済み値送信
- 信頼されていないMCPへの復元
- LiteLLMアップデートによるhook不動作
- Claude Codeアップデートによる未知イベント
- CodexアップデートによるResponsesイベント変更

Pythonでは完全なメモリゼロ化を保証しにくいことを明記してください。

そのため次を推奨してください。

- Gatewayをローカルまたは信頼済みネットワークで動かす
- swapを制限する
- core dumpを無効化する
- コンテナ権限を最小化する
- read-only filesystemを利用する
- secretsを環境変数またはSecret Managerで管理する
- GatewayへTLSを使用する
- 管理APIを外部公開しない
- セッションストアを暗号化する
- ログ保存期間を最小化する

# 34. 非対応または制約として明記する項目

少なくとも初期版では次を制約として明記して構いません。

- 画像内の文字情報
- 音声
- バイナリファイル
- 暗号化ファイル
- Base64の再帰的完全解析
- 圧縮データ
- あらゆるプログラミング言語の完全なAST解析
- WebSocket版Responses API
- Hosted toolへ実値を渡す処理
- すべての日本語氏名・住所の完全検出
- 未登録の社内固有語を100%自動検出
- モデルが大幅に改変したaliasの完全復元
- 秘密値の文字数や内容自体を使う計算の再現

ただし、未対応データが入力されたことを検知できる場合は、黙って外部へ送らずblockしてください。

# 35. Docker構成

ローカル開発用のDocker Composeを用意してください。

候補サービス:

```text
litellm
securitymasker module mounted into litellm
presidio-analyzer
redis optional
mock-upstream-openai
mock-upstream-anthropic
```

SecurityMaskerがLiteLLMと同一プロセスで動作する場合は、独立サービスにする必要はありません。

本番向けイメージでは次を考慮してください。

- 非rootユーザー
- read-only root filesystem
- 最小ベースイメージ
- health check
- dependency pinning
- SBOM生成
- vulnerability scan
- secretsをイメージへ含めない
- debug log無効
- 管理ポートの外部非公開

# 36. 開発ツール

次のような構成を推奨します。合理的な理由があれば変更可能です。

- Python 3.12以降
- `uv`による依存管理
- `pydantic`または`pydantic-settings`
- `cryptography`のAES-GCM
- `pytest`
- `pytest-asyncio`
- `hypothesis`
- `ruff`
- `mypy`または`pyright`
- `httpx`
- `redis` async client
- 構造化ログ
- pre-commit
- GitHub Actions

依存バージョンは固定し、lock fileをコミットしてください。

LiteLLM、Presidio、OpenAI SDK、Anthropic SDKは特に固定してください。

# 37. 実装フェーズ

以下の順序で実装してください。

## Phase 0: 調査と互換性固定

- LiteLLMの対象バージョンを決定
- `/v1/responses`の実リクエスト／SSE構造を確認
- `/v1/messages`の実リクエスト／SSE構造を確認
- LiteLLM callback hookのシグネチャ確認
- LiteLLM loggingの実行順序確認
- Codex設定確認
- Claude Code設定確認
- `docs/compatibility.md`作成
- ADR作成

## Phase 1: コアMVP

- ユーザー辞書
- Regex Detector
- Secret Detector
- インメモリセッション
- HMAC alias
- AES-GCM mapping
- replacement profiles
- non-streaming mask/unmask
- fail-closed
- CLI
- unit tests

## Phase 2: Codex対応

- OpenAI Responses API adapter
- SSE parser
- streaming text restoration
- tool argument buffering
- Codex config example
- mock upstream
- Codex E2E fixture

## Phase 3: Claude Code対応

- Anthropic Messages adapter
- content block処理
- tool use／tool result処理
- Claude Code config example
- beta header pass-through
- Claude Code E2E fixture

## Phase 4: 日本語PII

- Presidio adapter
- JP phone
- JP postal code
- JP My Number
- date of birth
- Japanese NER adapter
- composite address detector
- evaluation corpus

## Phase 5: 運用強化

- Redis store
- multi-tenant separation
- encrypted persistence
- metrics
- audit logs
- Docker hardening
- compatibility CI
- performance benchmark

各Phase終了時に、実行可能な状態とテスト結果を残してください。

# 38. 受け入れ基準

最低限、次をすべて満たしてください。

1. CodexからLiteLLM経由でOpenAI互換モックへ送った最終リクエストに、登録した機密情報が一切含まれない。
2. モックLLMがaliasをレスポンスへ含めた場合、Codex側へ返る前に正しく復元される。
3. 同一セッションでは同じ秘密が同じaliasになる。
4. 別セッションでは同じ秘密が別aliasになる。
5. 並列セッション間で対応表が混ざらない。
6. aliasがSSEチャンク境界で分割されても復元できる。
7. ツール引数のJSONが複数deltaへ分割されても、復元後に有効なJSONになる。
8. tool名、tool call ID、schema key、event typeが変更されない。
9. 生成コード、シェルコマンド、diff、patchがマスクのために構文破壊されない。
10. 会話履歴を再送しても二重マスクされない。
11. LiteLLM、SecurityMasker、Presidio、テスト用ログに元の秘密が出ない。
12. Detector障害時に元データを外部へ送らない。
13. 日本語の氏名、電話、メール、住所、郵便番号、マイナンバーについてテストがある。
14. APIキーと秘密鍵はデフォルトで環境変数参照へ変換される。
15. CodexとClaude Codeの設定例がREADMEにある。
16. LiteLLMをforkしていない。
17. SecurityMaskerを無効化すれば通常のLiteLLMとして動作する。
18. LiteLLM統合部分が独立した小さなアダプターになっている。
19. 対応バージョンがドキュメント化されている。
20. `docker compose up`と数個のコマンドでローカルデモを再現できる。

# 39. 成果物

次を提出してください。

- 完全なソースコード
- `pyproject.toml`
- lock file
- LiteLLM設定例
- SecurityMasker設定例
- Codex設定例
- Claude Code設定例
- Dockerfile
- Docker Compose
- README
- SECURITY.md
- threat model
- architecture document
- compatibility document
- Japanese PII document
- ADR
- unit tests
- integration tests
- E2E tests
- mock OpenAI server
- mock Anthropic server
- evaluation fixtures
- benchmark
- CI設定
- サンプル実行手順

READMEには、最低限、次のデモを載せてください。

```text
入力:
株式会社極秘技研のprod-db01.internal.exampleへ接続するコードを作ってください。
担当者は山田太郎です。

外部LLMへ送信:
SM_ORG_7F3A91のsm-host-7f3a91.example.invalidへ接続するコードを作ってください。
担当者はSM_PERSON_2B891Cです。

外部LLMの応答:
SM_PERSON_2B891C向けに、sm-host-7f3a91.example.invalidへ接続するコードです。

Codexへ返す応答:
山田太郎向けに、prod-db01.internal.exampleへ接続するコードです。
```

# 40. 実装時の判断原則

設計判断に迷った場合は、次の優先順位に従ってください。

1. 元の機密情報を外部へ送らない。
2. セッションやテナントをまたいで秘密を混ぜない。
3. JSON、コード、ツール呼び出しを壊さない。
4. 不明な場合はfail-closedにする。
5. LiteLLM本体をforkしない。
6. Protocol adapterとmasking coreを分離する。
7. 標準仕様と未知フィールドを可能な限り透過的に扱う。
8. ログへ機密情報を残さない。
9. PresidioやNERだけに依存しない。
10. ユーザー登録辞書を最も信頼する。
11. APIキーや秘密鍵は実値復元より環境変数参照を優先する。
12. 機能追加より、テスト可能性と保守性を優先する。

最初に以下を提示してから実装を開始してください。

1. 採用するLiteLLM、Presidio、Pythonのバージョン
2. 確認したLiteLLM hookの正確なシグネチャ
3. 全体アーキテクチャ
4. 脅威モデルの要約
5. ディレクトリ構成
6. Phaseごとの実装計画
7. 主要な設計判断と代替案
8. MVPで対応しない範囲

その後、Phase 0から順に、実行可能なコードとテストを作成してください。説明だけで終了せず、少なくともPhase 1とPhase 2の動作する実装を完成させてください。Phase 3以降も可能な限り実装し、未完部分は具体的なTODO、理由、インターフェース、テスト方針を残してください。