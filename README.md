# SecurityMasker

SecurityMasker は、ローカルの ChatGPT/Codex または Claude Code と外部サービスの間で動く、
可逆マスキングプロキシです。送信前に機密情報をセッション固有の仮名へ置き換え、応答に含まれる
仮名をローカルで元の値へ戻します。認証情報はクライアントから上流へ透過し、保存しません。

1プロセスは `chatgpt` または `claude` の一方だけを、loopback上の1ポートで扱います。
両方を使う場合は、別config・別DB・別keyで2プロセス起動してください。

## 5分で試す（source版）

必要条件は Python 3.12 以上です。setup時だけ依存パッケージと固定済み日本語NER modelを
取得します。`python3` が古くても `python3.12` がPATHにあれば自動選択します。prompt処理中に
modelをdownloadすることはありません。

```console
git clone <repository-url> SecurityMasker
cd SecurityMasker
./scripts/setup
. .venv/bin/activate
python3 securitymasker.py init --mode chatgpt --port 4000
python3 securitymasker.py preview \
  "株式会社極秘技研の山田太郎です。key=sk-abcdefghijklmnopqrstuvwxyz0"
python3 securitymasker.py gateway
```

`init` は実行ファイルの隣に次を作ります。既存ファイルは上書きしません。

```text
SecurityMasker/
├── securitymasker.py
├── securitymasker.config
├── securitymasker.dict
└── securitymasker.state/
    └── securitymasker.key
```

`securitymasker.db` は Gateway の初回起動時に作成されます。`securitymasker.dict` の合成例を
自分の組織名・人名・project名へ置き換えてから通常利用してください。

## クライアントを手動設定する

SecurityMasker は利用者の設定ファイルを自動変更しません。別terminalで次を実行し、表示された
設定を適用します。

```console
python3 securitymasker.py client-config
```

`chatgpt` modeでは ChatGPT/Codex が使用する `config.toml` に custom provider を追加します。
認証は `requires_openai_auth = true` によりクライアント自身の ChatGPT 認証を透過します。
`claude` modeでは、Claude Code/Desktopを起動する環境へ
`ANTHROPIC_BASE_URL=http://127.0.0.1:<port>` を設定します。

設定後に Gateway の状態を確認してください。

```console
python3 securitymasker.py doctor
```

クライアントが本当にこのbase URLへ向いている場合だけ通信が保護されます。通常のWeb版ChatGPT、
remote session、外部MCPなど、localhost Gatewayを通らない通信は対象外です。

## binary版

one-file版も同じコマンドと隣接ファイルを使います。

```console
./securitymasker init --mode chatgpt --port 4000
./securitymasker preview "確認したい合成テキスト"
./securitymasker gateway
```

現在は macOS arm64 で技術検証済みですが、署名・notarization、他OSのclean-machine検証、
同梱NER weightの再配布確認が未完了です。このため、現時点の公開可能な経路はsource版です。

## 保護層と限界

- ユーザー辞書: 組織固有の人名、会社名、project名。最優先で検出します。
- 決定論的検出: API key、秘密鍵、メール、電話、カード、公的識別子など。
- 標準日本語NER: 辞書未登録の一般的な人名・組織名・地名を補完します。

未知の組織内用語まで100%推測することはできません。重要語は必ず
`securitymasker.dict` に登録し、`preview` で期待するmaskを確認してください。障害時は既定で
fail-closedとなり、上流へ送りません。

file・image・audioのprotocol-native添付は、base64、URL、provider上のfile IDを含めて内容を
完全検査できないため、上流へ送らずblockします。local fileの内容が通常のprompt textとして
展開された場合はマスク対象です。添付を使う必要がある場合は、必要な部分をtextとして入力して
ください。

詳しい利用方法は [導入ガイド](docs/user/getting-started.md)、
[設定リファレンス](docs/user/configuration.md)、
[トラブルシューティング](docs/user/troubleshooting.md) を参照してください。
設計は [architecture](docs/design/architecture.md)、開発状況は
[status](docs/development/status.md)、最新の大規模変更は
[ADR-0012](docs/adr/0012-renew-package-design.md) にあります。

## 開発

利用者向けsetupとtest setupは分離しています。

```console
./scripts/test-setup
./scripts/release-check
```

実providerへテストpromptを送りません。開発手順と必須gateは
[testing](docs/development/testing.md) を参照してください。
