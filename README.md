# SecurityMasker

SecurityMasker は、ローカルの Codex または Claude Code と外部サービスの間で動く、
可逆マスキングプロキシです。送信前に機密情報をセッション固有の仮名へ置き換え、応答に含まれる
仮名をローカルで元の値へ戻します。認証情報はクライアントから上流へ透過し、保存しません。

1プロセスは `chatgpt` または `claude` の一方だけを、loopback上の1ポートで扱います。
両方を使う場合は、別config・別DB・別keyで2プロセス起動してください。

## 対応環境

source版の検証済み環境は、macOS arm64（Python 3.11 / 3.12）とLinux arm64
（Python 3.12）です。その他のOS・architectureは検証済みの対応環境ではありません。

**Windowsは現在非対応です。** Windows用setup、機密fileのACL検査、PowerShell向けclient設定、
native E2Eが未実装・未検証です。コードの一部にWindows分岐はありますが、安全性を保証できない
ため、Windows nativeを対応済みとは扱いません。WSL2またはDocker DesktopでLinux版を評価する
未サポートの手順と免責は[Windows番外編](docs/user/getting-started-windows.md)にあります。

one-file binaryはmacOS arm64での技術検証に留まり、現時点では公開配布対象ではありません。
詳細は[互換性](docs/development/compatibility.md)と
[開発・リリース状況](docs/development/status.md)を参照してください。

## 目的別の入口

- まず動かす: [導入ガイド](docs/user/getting-started.md)
- commandや設定を確認する:
  [CLIリファレンス](docs/user/cli-reference.md)、
  [設定リファレンス](docs/user/configuration.md)
- sourceの処理を追う: [コード読解ガイド](docs/development/codebase-guide.md)
- 安全性と対応範囲を確認する:
  [Security policy](SECURITY.md)、
  [脅威モデル](docs/design/threat-model.md)、
  [開発・リリース状況](docs/development/status.md)

文書全体の地図と、現行文書・履歴文書の区別は[文書案内](docs/README.md)にあります。

## source版を試す

必要条件は Python 3.11 以上です。setup時だけ依存パッケージと固定済み日本語NER modelを
取得します。`python3` が古くても `python3.12` または `python3.11` がPATHにあれば自動選択
します。prompt処理中にmodelをdownloadすることはありません。

公開repositoryの `Code` メニューからcloneするか、Releaseのsource archiveを展開し、
repository rootで次を実行します。

```console
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

`chatgpt` modeでは、Codex CLIまたはCodex appが使用する `config.toml` にcustom providerを
追加します。
認証は `requires_openai_auth = true` によりクライアント自身の ChatGPT 認証を透過します。
`claude` modeでは、Claude Code CLIまたはClaude Code Desktopを起動する環境へ
`ANTHROPIC_BASE_URL=http://127.0.0.1:<port>` を設定します。

設定後に Gateway の状態を確認してください。

```console
python3 securitymasker.py doctor
```

クライアントが本当にこのbase URLへ向いている場合だけ通信が保護されます。通常のWeb版ChatGPT、
remote session、外部MCPなど、localhost Gatewayを通らない通信は対象外です。

## Gatewayを終了する

クライアントでの操作を終えてから、Gatewayを起動したterminalで`Ctrl+C`を1回入力し、
shellのpromptが戻るまで待ちます。2つのmodeを別processで起動している場合は、それぞれ終了します。
background processには`SIGTERM`を送り、通常の終了で応答しない場合を除いて強制終了しないで
ください。

## binary版

one-file版も同じコマンドと隣接ファイルを使います。

```console
./securitymasker init --mode chatgpt --port 4000
./securitymasker preview "確認したい合成テキスト"
./securitymasker preview < prompt.txt
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

詳しい利用方法、設計、開発文書、ADRは[文書案内](docs/README.md)から辿れます。
現行の正は[architecture](docs/design/architecture.md)と
[status](docs/development/status.md)です。config schemaをv1とした最新判断は
[ADR-0016](docs/adr/0016-reset-config-schema-version.md)、現行package設計は
[ADR-0012](docs/adr/0012-renew-package-design.md)にあります。

## 開発

利用者向けsetupとtest setupは分離しています。

```console
./scripts/test-setup
./scripts/release-check
```

実providerへテストpromptを送りません。開発手順と必須gateは
[testing](docs/development/testing.md)、実装を読む順序は
[コード読解ガイド](docs/development/codebase-guide.md)を参照してください。
