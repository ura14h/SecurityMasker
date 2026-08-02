# 導入ガイド

このページでは、source版を初期化し、合成データでmaskを確認して、CodexまたはClaude Codeを
SecurityMaskerへ接続します。実際の機密情報は、最後の確認が終わるまで入力しないでください。

## 始める前に

- 対応環境はmacOS arm64のPython 3.11／3.12、Linux arm64のPython 3.12、Windows 11 x64 build
  26100以降のPython 3.12 x64です。
- setup時にPython packageと固定済み日本語NER modelを取得します。数GBの空き容量を確保してください。
- 通常利用中にmodelをdownloadすることはありません。
- SecurityMaskerはCodexやClaude Codeの設定を自動変更しません。

対応範囲の詳細は[対応環境](reference/compatibility.md)、安全に使うための要点は
[安全な使い方](security/safe-use.md)にあります。

Windows利用者はここで[Windows native source版の導入手順](guides/windows-native-source.md)へ
進んでください。以下のcommand例はmacOS／Linux向けです。

## 1. Setupする

repositoryをcloneするか、Releaseのsource archiveを展開し、repository rootで実行します。

```console
./scripts/setup
. .venv/bin/activate
```

setupは固定lockからruntimeを導入し、固定revisionのNER modelを取得してSHA-256を検証します。
既定の`python3`が古い場合は、PATH上の`python3.12`、次に`python3.11`を自動選択します。

成功するとrepository内に`.venv`が作られます。既存のCodex／Claude Code設定は変わりません。

## 2. 利用するclientを初期化する

Codexには`chatgpt` modeを使います。

```console
python3 securitymasker.py init --mode chatgpt --port 4000
```

Claude Codeには`claude` modeを使います。

```console
python3 securitymasker.py init --mode claude --port 4001
```

`init`は実行ファイルの隣に次を作ります。通常は既存fileを上書きしません。

```text
securitymasker.config
securitymasker.dict
securitymasker.state/
└── securitymasker.key
```

`securitymasker.db`はGatewayの初回起動時に作られます。config、辞書、DB、keyは機密fileです。

既存の設定と状態を意図的にすべて捨てて初期状態へ戻す場合だけ、Gatewayを停止し、対象directoryを
明示して`init --force`を実行できます。

```console
python3 securitymasker.py init --force --directory . --mode chatgpt --port 4000
```

この操作では辞書、全session、alias対応表、master keyを復元できなくなります。必要なら先に
[Backupとrestore](operations/backup-restore.md)に従って一組で保存し、実行後はclientで新しい
会話を開始してください。

## 3. 外部へ送らずmaskを確認する

starter辞書には合成した会社名と人名が入っています。

```console
python3 securitymasker.py preview \
  "株式会社極秘技研の山田太郎が担当します"
python3 securitymasker.py config-check
```

成功すると、元の会社名や人名ではなく`SM_ORG_...`や`SM_PERSON_...`を含むmask後の
文字列と検出件数が表示されます。`preview`は外部providerへ接続しません。

実際の値を確認するときは、shell historyやprocess一覧へ残さないよう標準入力を使います。

```console
python3 securitymasker.py preview < prompt.txt
```

実データを使う前に、[辞書のカスタマイズ](guides/customize-dictionary.md)に従ってstarterの
合成値を自分の重要語へ置き換えてください。

## 4. Gatewayを起動する

```console
python3 securitymasker.py gateway
```

`gateway_started`が表示され、shellのpromptが戻らない状態が正常です。このterminalは開いたままに
します。別terminalで以降のcommandを実行してください。

Gatewayはloopbackだけで待ち受けます。public bind、共有server、複数workerには対応しません。

## 5. Clientを接続する

別terminalで、設定snippetを表示します。

```console
python3 securitymasker.py client-config
```

このcommandは設定fileを変更しません。

### Codex

出力された設定を、Codex CLIまたはCodex appが読む`config.toml`へ反映します。

```toml
model_provider = "securitymasker"

[model_providers.securitymasker]
name = "SecurityMasker Gateway"
base_url = "http://127.0.0.1:4000"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = true
```

`model_provider`は最初の`[table]`より前に置きます。既存provider設定は、元へ戻すときに必要なので
削除せず控えておいてください。Codexは同じGateway URLのResponses WebSocketを優先し、
利用できない場合の再試行方法はclientが決めます。SecurityMasker自身は同じturnをHTTPへ
自動再送しません。

### Claude Code

Claude Codeを起動する環境へ、表示された`ANTHROPIC_BASE_URL`を設定します。

```console
export ANTHROPIC_BASE_URL="http://127.0.0.1:4001"
```

この設定を行った同じshellからClaude Codeを起動します。永続化方法は起動方式ごとに異なるため、
SecurityMaskerは自動変更しません。

## 6. 接続状態を確認する

```console
python3 securitymasker.py doctor
python3 securitymasker.py doctor --require-ready
```

`doctor --require-ready`が成功したら、辞書にある合成値だけで新しい会話を開始します。最初は
「山田太郎という文字列を一字一句そのまま返してください」のような確認に留め、表示上の値が
復元されることを確認してください。

`doctor`はconfig、辞書、key、NER、port、Gateway readinessをread-onlyで検査しますが、
Desktopの全通信経路までは証明しません。外部LLMが実際にaliasを受け取ったことを詳しく確認する場合は
[通信経路を詳しく検証する](guides/verify-routing.md)へ進んでください。

## 7. 終了する

clientでの操作を終え、Gatewayを起動したterminalで`Ctrl+C`を1回入力します。shellのpromptが
戻るまで待ってください。次回は同じconfigでGatewayを起動すれば、再初期化は不要です。

client設定はGateway終了後も残ります。SecurityMaskerを使わない状態へ戻す方法は
[アンインストールと設定の復旧](operations/uninstall.md)を参照してください。

## 次に読む

- 組織固有語を登録する: [辞書のカスタマイズ](guides/customize-dictionary.md)
- 毎日の起動と終了: [日常的な使い方](operations/daily-use.md)
- ChatGPTとClaudeを同時に使う: [2つのclientを使う](guides/use-both-clients.md)
- 問題が起きた: [トラブルシューティング](operations/troubleshooting.md)
