# Windows native source版の導入手順

この手順は対応範囲に含まれるWindows native source版を導入します。Windows one-file版、Windows 10、
ARM64、Python 3.11／3.13以降、ReFS、removable／network／subst driveは対象外です。

対象はWindows 11 x64 build 26100以降、64-bit Python 3.12、local fixed NTFS上のsource archive、
standard userと標準のcmd.exeです。Visual Studioは不要です。
対応条件とDesktopの検証範囲の詳細は[対応環境](../reference/compatibility.md)を参照してください。

## 1. Setupする

source archiveをlocal fixed NTFSへ展開し、repository rootのcmd.exeで実行します。

```bat
py -3.12 --version
scripts\setup.cmd
set "SM=.venv\Scripts\python.exe securitymasker.py"
```

`scripts\setup.cmd`はWindows専用lockからwheelだけを導入し、固定NER modelを取得してdigestを
検証します。既存のCodex／Claude Code設定は変更しません。

## 2. modeを初期化する

Codex用は`chatgpt` modeです。

```bat
%SM% init --mode chatgpt --port 4000
set "CFG=%LOCALAPPDATA%\SecurityMasker\chatgpt\securitymasker.config"
```

Claude Code用は`claude` modeです。

```bat
%SM% init --mode claude --port 4001
set "CFG=%LOCALAPPDATA%\SecurityMasker\claude\securitymasker.config"
```

両方を使う場合は別々に初期化し、config、state、DB、key、portを共有しません。`CFG`は現在の
cmd.exeだけに設定されます。`SM`と`CFG`は新しいcmd.exeへ引き継がれません。新しいwindowでは
repository rootへ移動し、前節の`set "SM=..."`と操作対象modeの`set "CFG=..."`を実行してください。

## 3. 外部へ送らずmaskを確認する

外部へ送信しない確認を先に実行します。

```bat
%SM% config-check --config "%CFG%"
%SM% preview "担当は山田太郎です" --config "%CFG%"
```

`preview`に元の合成値ではなく`SM_PERSON_...`が表示されることを確認します。

## 4. Gatewayを起動する

専用のcmd.exeを一つ開き、`SM`と`CFG`を設定してから起動します。

```bat
%SM% gateway --config "%CFG%"
```

`gateway_started`が表示され、promptが戻らない状態が正常です。このwindowを閉じず、clientは別の
windowまたはDesktop appから起動します。終了するときはGatewayのwindowで`Ctrl+C`を1回押します。

## 5. Codexを接続する

### Codex CLI

`chatgpt` modeの`CFG`を使ってsnippetを表示します。

```bat
%SM% client-config --config "%CFG%"
```

表示されたTOMLを`%USERPROFILE%\.codex\config.toml`へ手動で反映します。`model_provider`は最初の
`[table]`より前に置きます。provider設定はproject-local `.codex\config.toml`では無視されるため、
必ずuser-level configへ置きます。既存の`model_provider`は削除せず、元へ戻す値を控えます。

次にread-only診断を実行します。

```bat
%SM% doctor --config "%CFG%"
%SM% doctor --require-ready --config "%CFG%"
codex
```

新しいCodex taskでstarter辞書の合成値だけを使い、表示上で元の値へ復元されることを確認します。

### Codex app

Windows nativeのCodex appとCodex CLIは`%USERPROFILE%\.codex`を共有します。appの
**Settings > Configuration > Open config.toml**から、前節と同じuser-level configを開いて設定します。
保存後に新しいlocal taskを開始します。cloud taskやremote environmentからはPCの
`127.0.0.1`へ接続できません。

Codexの設定仕様は[OpenAI Config basics](https://developers.openai.com/codex/config-basic)と
[Configuration Reference](https://developers.openai.com/codex/config-reference)を参照してください。

## 6. Claude Codeを接続する

### Claude Code CLI

`claude` modeのGatewayとは別のcmd.exeから、次を実行します。

```bat
set "ANTHROPIC_BASE_URL=http://127.0.0.1:4001"
%SM% doctor --require-ready --config "%CFG%"
claude
```

`set`の効果は現在のcmd.exeと、そこから起動したprocessだけです。永続的なuser設定へ自動変更しない
ため、最初はこの方法を使います。終了後はwindowを閉じるか、次で解除できます。

```bat
set "ANTHROPIC_BASE_URL="
```

### Claude Code Desktop

新しいsessionのenvironmentで**Local**を選び、environment dropdownでLocalへpointerを合わせて
gear iconからLocal environment editorを開きます。次を登録します。

```text
ANTHROPIC_BASE_URL=http://127.0.0.1:4001
```

Windows版Desktopはuser／system環境変数を継承しますが、PowerShell profileは読みません。
Local environment editorの値は新しいlocal sessionへ適用されます。remote／SSH sessionはlocal
Gatewayと同じprocess境界ではないため、この手順の対象外です。

Claude Codeの設定仕様は[Environment variables](https://code.claude.com/docs/en/env-vars)と
[Desktop](https://code.claude.com/docs/en/desktop)を参照してください。

## 7. 設定を解除する

- Codexはuser-level `config.toml`の`model_provider`を導入前の値へ戻します。
- Claude Code CLIは設定したcmd.exeを閉じるか`set "ANTHROPIC_BASE_URL="`を実行します。
- Claude Code DesktopはLocal environment editorから`ANTHROPIC_BASE_URL`を削除します。
- clientを終了してからGatewayを`Ctrl+C`で停止します。

SecurityMaskerはclient設定を自動変更しません。詳しい削除範囲は
[アンインストールと設定の復旧](../operations/uninstall.md)を参照してください。
