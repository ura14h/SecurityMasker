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

## 完全に初期化する

既存のconfig、辞書、session、alias対応表をすべて不要と判断した場合は、Gatewayを正常終了してから
対象directoryを明示して完全初期化できます。

```console
python3 securitymasker.py init --force --directory . --mode chatgpt --port 4000
```

`--force`は標準`securitymasker.config`、`securitymasker.dict`、`securitymasker.state/`を一組として
削除し、新しいstarter辞書とmaster keyを生成します。これはrestore、config migration、rekeyでは
ありません。実行前の状態が必要になる可能性があれば、先にこの文書の手順でbackupしてください。

稼働中Gateway、管理外のstate entry、symlinkなどを検出した場合は何も削除せず失敗します。成功後は
`config-check`を実行し、古いaliasを継続利用せずclientで新しい会話を開始してください。
