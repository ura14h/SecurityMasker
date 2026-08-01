# Security policy

## Supported scope

SecurityMaskerは、単一利用者のローカルPCで、loopbackにbindした1 process・1 mode・1 workerとして
使用してください。public bind、共有server、multi-user、multi-tenant、複数workerは対象外です。

検証済みplatform、client、protocol、配布形態は
[対応環境](docs/reference/compatibility.md)を正とします。現在の公開候補はsource版です。
one-file Lite／Full binaryは技術検証段階で、公開対象ではありません。どちらも署名と対象OS別gate、
同梱dependencyの再配布確認が必要です。model weightを同梱するFull版には、model再配布条件の確認も
追加で必要です。

Windows nativeは非対応です。Windows上で実際の機密情報を扱わないでください。

## Safe operation

実運用前の辞書、preview、routing、添付、local file、credentialに関する確認は
[安全な使い方](docs/security/safe-use.md)にまとめています。正式な信頼境界と対象外は
[Threat model](docs/security/threat-model.md)を参照してください。

次は機密fileです。

- `securitymasker.config`
- `securitymasker.dict`
- `securitymasker.state/securitymasker.db`
- `securitymasker.state/securitymasker.key`

POSIXではfileを`0600`、state directoryを`0700`にします。DBとkeyは1対1で、
[同じbackup単位](docs/operations/backup-restore.md)にします。keyを失うと既存DBを復号できません。
config、辞書、DB、keyをGitへcommitしないでください。

SecurityMaskerはprovider credentialを保存しません。対応providerの認証headerだけを上流へ透過し、
log、error、telemetryへ出しません。

## Failure behavior

config、辞書、model、DB/key、protocol、detector、leak guardに異常があれば、既定でrequestを上流へ
送りません。検出済みの重大secretをfail-openせず、未知fieldやeventは最終leak guardを通過できる
場合だけ透過します。

日本語NERは一般的な人名、組織名、地名を補完しますが、未知の社内code nameを100%検出するとは
保証しません。重要語はユーザー辞書へ登録してください。

## Reporting a vulnerability

公開repositoryのsecurity advisory機能、またはmaintainerが指定する非公開窓口を使用してください。
issue、log、screenshot、fixtureへ実際のcredential、原文、master key、辞書、DBを添付しないで
ください。

合成値で再現できない場合も、まずsecret非表示の`doctor --json`と影響範囲だけを共有してください。
