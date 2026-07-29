# Source版を更新する

更新前にrelease noteと[対応環境](../reference/compatibility.md)を確認し、
[config、辞書、DB、keyをbackup](backup-restore.md)します。

## 更新手順

1. Gatewayを正常終了します。
2. 現在使用中のversionとconfig pathを控えます。
3. 新しいsource archiveを別directoryへ展開するか、管理しているcheckoutを更新します。
4. 新しいsourceで`./scripts/setup`を実行します。
5. 既存configを指定して`config-check`を実行します。
6. 合成値だけで`preview`します。
7. Gatewayを起動し、`doctor --require-ready`を実行します。
8. 合成promptでclient接続を確認します。

新しいsourceが既存configやstateのmigrationを要求する場合は、そのrelease noteに従います。
config、辞書、DB、keyを暗黙に上書きして進めてはいけません。

確認に失敗した場合は実データを送らず、新しいGatewayを停止します。backupと旧sourceを使って
元の組へ戻してください。
