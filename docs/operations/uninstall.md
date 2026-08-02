# アンインストールと設定の復旧

SecurityMaskerを使わない状態へ戻すには、先にclientの接続先を戻し、その後でlocal dataを
整理します。

## 1. Gatewayを終了する

Gatewayを起動したterminalで`Ctrl+C`を1回入力し、shellのpromptが戻るまで待ちます。

## 2. Client設定を戻す

### Codex

user-level `config.toml`の`model_provider`をSecurityMasker導入前の値へ戻します。SecurityMasker専用に追加した
`[model_providers.securitymasker]` tableは、他の設定から参照されていないことを確認してから
削除できます。

### Claude Code

Claude Codeを起動する環境から`ANTHROPIC_BASE_URL`のSecurityMasker設定を外します。現在のshellだけ
で設定した場合、macOS／Linuxは`unset ANTHROPIC_BASE_URL`、Windowsのcmd.exeは
`set "ANTHROPIC_BASE_URL="`で外せます。Claude Code DesktopのLocal environment editorへ設定した
場合は、同じ画面から削除します。

clientを再起動し、SecurityMaskerのlocalhost portを参照していないことを確認します。

## 3. 残すdataを判断する

次には機密情報または復元に必要な情報が含まれます。

- `securitymasker.config`
- `securitymasker.dict`
- `securitymasker.state/securitymasker.db`
- `securitymasker.state/securitymasker.key`

将来復元する可能性がある場合は、[backup手順](backup-restore.md)に従って保管します。DBまたはkeyの
一方だけを残しても復元できません。

不要であることを確認した場合だけ、対象fileを明示して削除します。repository全体、home directory、
共有model cacheを広いrecursive commandで削除しないでください。

`.venv`はSecurityMasker用のPython環境です。固定NER modelはHugging Faceの共有cacheにある場合が
あり、他のapplicationが同じartifactを利用していないか確認してから整理してください。
