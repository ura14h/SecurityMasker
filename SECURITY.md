# Security policy

## Supported scope

SecurityMaskerは、単一利用者のローカルPCで、loopbackにbindした1 process・1 mode・1 workerとして
使用してください。public bind、共有server、multi-user、multi-tenant、複数workerは対象外です。

現在の公開候補はsource版です。one-file binaryは署名、他OS、model weight再配布条件の確認が
終わるまで公開対象ではありません。詳しくは
[development status](docs/development/status.md) を参照してください。

## Sensitive local files

次を機密として扱ってください。

- `securitymasker.config`
- `securitymasker.dict`
- `securitymasker.state/securitymasker.db`
- `securitymasker.state/securitymasker.key`

POSIXではfileを `0600`、state directoryを `0700` にします。DBとkeyは1対1で、同じbackup単位に
します。keyを失うと既存DBは復号できません。config、辞書、DB、keyをGitへcommitしないでください。

SecurityMaskerはprovider credentialを保存しません。認証headerは対応providerへだけ透過し、
log/error/telemetryへ出しません。

## Safe operation

1. `securitymasker.dict` に重要な組織固有語を登録する。
2. `preview` で合成または安全なlocal入力のmask結果を確認する。
3. `doctor` と `doctor --require-ready` を実行する。
4. `client-config` の出力を、実際にclientが読む設定へ手動反映する。
5. clientがlocalhost Gatewayを向いていることを確認してからpromptを入力する。

client設定は自動変更されません。Web版ChatGPT、remote session、外部MCP等、Gatewayを迂回する
通信は保護されません。

## Failure behavior

設定、辞書、model、DB/key、protocol、detector、leak guardに異常があれば既定でfail-closedとなり、
上流へrequestを送りません。未知field/eventは最終leak guardを通過できる場合だけ透過します。

日本語NERは一般的な人名・組織名・地名を補完しますが、未知の社内code nameを100%検出するとは
保証しません。重要語はユーザー辞書へ登録してください。

## Reporting a vulnerability

公開repositoryのsecurity advisory機能、またはmaintainerが指定する非公開窓口を使用してください。
issue、log、screenshot、fixtureへ実際のcredential、原文、master key、辞書、DBを添付しないで
ください。合成値で再現できない場合も、まずsecret非表示の `doctor --json` と影響範囲だけを
共有してください。
