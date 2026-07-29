# 安全な使い方

このページは、実際の機密情報を扱う前に利用者が確認する項目をまとめます。設計レビュー用の詳細は
[Threat model](threat-model.md)にあります。

## 使用前チェック

1. [対応環境](../reference/compatibility.md)に含まれるOSとarchitectureを使う。
2. `securitymasker.dict`へ重要な組織固有語を登録する。
3. 合成値または安全なlocal inputだけで`preview`する。
4. `config-check`と`doctor --require-ready`を成功させる。
5. clientがSecurityMaskerのlocalhost URLを向いていることを確認する。
6. 最初のclient確認にも実在人物や実credentialを使わない。

## 保護される通信

SecurityMaskerのGatewayを実際に通過した、対応protocolのtext requestが対象です。

- CodexのOpenAI Responses互換通信
- Claude CodeのAnthropic Messages互換通信
- request JSON内へ通常のtextとして展開された内容

通常のWeb版ChatGPT、remote session、外部MCPなど、localhost Gatewayを通らない通信は
保護できません。client設定に文字列があるだけではなく、実際に選択されたproviderとbase URLを
確認してください。

## 添付file

file、image、audioのprotocol-native添付は、base64、URL、provider上のfile IDを含めて内容全体を
安全に検査できないため、上流へ送らずblockします。

必要な部分を通常のprompt textとして入力した場合はmask対象になります。ただし、添付UIを使えば
自動的にtext化されるとは仮定しません。

## 検出の限界

日本語NERと決定論的detectorにはfalse positiveとfalse negativeがあります。特に社内code name、
顧客固有の略称、未公開project名は自動推測できない場合があります。

重要語は辞書へ登録し、関係する表記揺れも含めて`preview`してください。検出される例だけでなく、
無関係なclean inputが過剰にmaskされないことも確認します。

## Local fileを守る

次を機密fileとして扱います。

- `securitymasker.config`
- `securitymasker.dict`
- `securitymasker.state/securitymasker.db`
- `securitymasker.state/securitymasker.key`

POSIXではfileを`0600`、state directoryを`0700`にします。DBとkeyは同じbackup単位で扱います。
Git、issue、ticket、chat、暗号化されていない外部backupへ置かないでください。

## Credentialとtool

API key、password、秘密鍵は辞書へ平文で書かず、可能な限り`value_from_env`を使います。
`trusted_local_tools`は通常は空にします。外部MCPやprovider-hosted toolを登録してはいけません。

詳しい手順は[credentialの登録](../guides/credentials.md)と
[trusted local tool](../guides/trusted-tools.md)を参照してください。

## 問題がある場合

設定、model、DB/key、detector、protocol、最終検査に異常がある場合、SecurityMaskerは既定で
requestを上流へ送りません。警告や失敗を回避して実データを送らず、
[トラブルシューティング](../operations/troubleshooting.md)で原因を確認してください。
