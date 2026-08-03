# 導入ガイド

このページでは、macOS、Linux、Windowsのsource版を初期化し、合成データでmaskを確認して、
CodexまたはClaude CodeをSecurityMaskerへ接続します。実際の機密情報は、最後の確認が終わるまで
入力しないでください。

## 始める前に

- 対応環境はmacOS arm64のPython 3.11／3.12、Linux arm64のPython 3.12、Windows 11 x64 build
  26100以降のPython 3.12 x64です。Windowsのlauncher隣接layoutもnative検証済みです。
- Windowsではlocal fixed NTFS上のsource archive、standard user、標準の`cmd.exe`を使います。
  Windows 10、ARM64、ReFS、removable／network／subst driveは対象外です。
- setup時にPython packageと固定済み日本語NER modelを取得します。数GBの空き容量を確保してください。
- 通常利用中にmodelをdownloadすることはありません。
- SecurityMaskerはCodexやClaude Codeの設定を自動変更しません。

対応範囲の詳細は[対応環境](reference/compatibility.md)、安全に使うための要点は
[安全な使い方](security/safe-use.md)にあります。

## 1. Setupする

repositoryをcloneするか、Releaseのsource archiveを展開し、repository rootで実行します。

macOS／LinuxではPOSIX shellを使います。

```console
./scripts/setup
. .venv/bin/activate
```

Windowsではlocal fixed NTFSへ展開し、`cmd.exe`を使います。Visual Studioは不要です。

```bat
py -3.12 --version
scripts\setup.cmd
.venv\Scripts\activate.bat
```

setupはOS別の固定lockからruntimeを導入し、固定revisionのNER modelを取得してSHA-256を検証します。
Windowsではwheelだけを導入します。成功するとrepository内に`.venv`が作られ、既存の
Codex／Claude Code設定は変わりません。

以降は有効化した仮想環境の`python`を使います。新しいterminalまたは`cmd.exe`を開いた場合は、
repository rootへ移動して仮想環境をもう一度有効にしてください。

## 2. 利用するclientを初期化する

Codexには`chatgpt` modeを使います。

```console
python securitymasker.py init --mode chatgpt --port 4000
```

Claude Codeには`claude` modeを使います。

```console
python securitymasker.py init --mode claude --port 4001
```

全OSでrepository rootに次のlayoutを作ります。Windowsではconfig、辞書、state directoryとその配下へ
current user、SYSTEM、Administratorsだけがアクセスできるprotected DACLを作成・検査します。
source root自体のDACLは変更しません。

```text
securitymasker.config
securitymasker.dict
securitymasker.state/
└── securitymasker.key
```

`securitymasker.db`はGatewayの初回起動時に作られます。config、辞書、DB、keyは機密fileです。
両modeを使う場合は別々のconfig、state、DB、key、portを明示し、共有しません。

既存の設定と状態を意図的にすべて捨てて初期状態へ戻す場合だけ、Gatewayを停止し、対象directoryを
明示して`init --force`を実行できます。次は`chatgpt` modeの例です。

```console
python securitymasker.py init --force --directory . --mode chatgpt --port 4000
```

この操作では辞書、全session、alias対応表、master keyを復元できなくなります。必要なら先に
[Backupとrestore](operations/backup-restore.md)に従って一組で保存し、実行後はclientで新しい
会話を開始してください。

## 3. 外部へ送らずmaskを確認する

starter辞書には合成した会社名と人名が入っています。

```console
python securitymasker.py config-check
python securitymasker.py preview "株式会社極秘技研の山田太郎が担当します"
```

成功すると、元の会社名や人名ではなく`SM_ORG_...`や`SM_PERSON_...`を含むmask後の
文字列と検出件数が表示されます。`preview`は外部providerへ接続しません。

実際の値を確認するときは、shell historyやprocess一覧へ残さないよう標準入力を使います。

```console
python securitymasker.py preview < prompt.txt
```

実データを使う前に、[辞書のカスタマイズ](guides/customize-dictionary.md)に従ってstarterの
合成値を自分の重要語へ置き換えてください。

## 4. Gatewayを起動する

```console
python securitymasker.py gateway
```

`gateway_started`が表示され、shellのpromptが戻らない状態が正常です。このterminalは開いたままに
します。clientは別のterminalまたはDesktop appから起動します。終了するときはGatewayのterminalで
`Ctrl+C`を1回押します。

Gatewayはloopbackだけで待ち受けます。public bind、共有server、複数workerには対応しません。

## 5. Codexを接続する

別terminalで仮想環境を有効にし、設定snippetを表示します。

```console
python securitymasker.py client-config
```

このcommandは設定fileを変更しません。表示されたTOMLをCodexのuser-level `config.toml`へ手動で
反映します。macOS／Linuxでは`~/.codex/config.toml`、Windowsでは
`%USERPROFILE%\.codex\config.toml`です。

```toml
model_provider = "securitymasker"

[model_providers.securitymasker]
name = "SecurityMasker Gateway"
base_url = "http://127.0.0.1:4000"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = true
```

`model_provider`は最初の`[table]`より前に置きます。project-local `.codex/config.toml`ではなく、
必ずuser-level configへ置きます。既存provider設定は、元へ戻すときに必要なので削除せず控えます。

Codex appでは**Settings > Configuration > Open config.toml**から同じuser-level configを開けます。
保存後、後述する`doctor --require-ready`の成功を確認してから新しいlocal taskを開始します。
cloud taskやremote environmentからはPCの`127.0.0.1`へ接続できません。

Codexの設定仕様は[OpenAI Config basics](https://developers.openai.com/codex/config-basic)と
[Configuration Reference](https://developers.openai.com/codex/config-reference)を参照してください。

## 6. Claude Codeを接続する

Claude Codeを起動する別terminalで仮想環境を有効にし、`ANTHROPIC_BASE_URL`を設定します。

macOS／Linux:

```console
export ANTHROPIC_BASE_URL="http://127.0.0.1:4001"
```

Windowsの`cmd.exe`:

```bat
set "ANTHROPIC_BASE_URL=http://127.0.0.1:4001"
```

この設定は現在のterminalと、そこから起動したprocessだけへ渡ります。永続的なuser設定へ自動変更
しないため、最初はこの方法を使います。

Claude Code Desktopでは、新しいsessionのenvironmentで**Local**を選び、Local environment editorへ
同じ`ANTHROPIC_BASE_URL`を登録します。Windows版Desktopはuser／system環境変数を継承しますが、
PowerShell profileは読みません。remote／SSH sessionはlocal Gatewayと同じprocess境界ではないため、
この手順の対象外です。

Claude Codeの設定仕様は[Environment variables](https://code.claude.com/docs/en/env-vars)と
[Desktop](https://code.claude.com/docs/en/desktop)を参照してください。

## 7. 接続状態を確認する

SecurityMaskerのcommandを実行する別terminalでは、仮想環境を有効にします。

```console
python securitymasker.py doctor
python securitymasker.py doctor --require-ready
```

`doctor --require-ready`が成功したら、辞書にある合成値だけで新しい会話を開始します。最初は
「山田太郎という文字列を一字一句そのまま返してください」のような確認に留め、表示上の値が
復元されることを確認してください。CLIは同じterminalから`codex`または`claude`で起動します。

`doctor`はconfig、辞書、key、NER、port、Gateway readinessをread-onlyで検査しますが、
Desktopの全通信経路までは証明しません。外部LLMが実際にaliasを受け取ったことを詳しく確認する場合は
[通信経路を詳しく検証する](guides/verify-routing.md)へ進んでください。

## 8. 終了する

clientでの操作を終え、Gatewayを起動したterminalで`Ctrl+C`を1回入力します。shellのpromptが
戻るまで待ってください。次回は同じconfigでGatewayを起動すれば、再初期化は不要です。

Claude Code CLIの設定は、terminalを閉じるか次のcommandで解除できます。

macOS／Linux:

```console
unset ANTHROPIC_BASE_URL
```

Windowsの`cmd.exe`:

```bat
set "ANTHROPIC_BASE_URL="
```

client設定はGateway終了後も残ります。SecurityMaskerを使わない状態へ戻す方法は
[アンインストールと設定の復旧](operations/uninstall.md)を参照してください。

## 次に読む

- 組織固有語を登録する: [辞書のカスタマイズ](guides/customize-dictionary.md)
- 毎日の起動と終了: [日常的な使い方](operations/daily-use.md)
- ChatGPTとClaudeを同時に使う: [2つのclientを使う](guides/use-both-clients.md)
- 問題が起きた: [トラブルシューティング](operations/troubleshooting.md)
