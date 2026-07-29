# Backupとrestore

config、辞書、DB、keyは一つの運用単位として保護します。特にDBとkeyは1対1です。keyを失うと
既存DBを復号できません。

## Backupする

Gatewayを正常終了してから、次を同じ時点の組として保存します。

- `securitymasker.config`
- `securitymasker.dict`
- `securitymasker.state/securitymasker.db`
- `securitymasker.state/securitymasker.key`

別名configやmode別state directoryを使う場合は、それぞれ対応する組を保存します。ChatGPT用と
Claude用のDB/keyを混ぜないでください。

辞書、DB、keyをrepository、issue、ticket、chatへ置かないでください。backup先の暗号化と
access controlは利用者の責任です。

## Restoreする

1. Gatewayが停止していることを確認します。
2. config、辞書、DB、keyを同じbackupから元の対応関係を保って戻します。
3. POSIXではfileを`0600`、state directoryを`0700`にします。
4. `config-check`を実行します。
5. Gatewayを起動し、`doctor --require-ready`を実行します。
6. 合成値だけで`preview`とclient接続を確認します。

DB/key mismatch、wrong mode、tamperを検出した場合、SecurityMaskerは起動を拒否します。新しいkeyを
自動生成して既存DBを開くことはありません。

復旧できない場合は、古いDBとkeyを上書きせず保全し、別directoryへ新規`init`します。その場合、
古いsessionのaliasは復元できないため、新しい会話を開始してください。
