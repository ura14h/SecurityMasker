# Credentialを環境変数から登録する

API key、password、private key、DB接続文字列などは、辞書の`values`へ平文で書くより
`value_from_env`を優先します。

## 辞書へ環境変数名を登録する

```yaml
entities:
  - id: deployment_password
    type: PASSWORD
    value_from_env: DEPLOYMENT_PASSWORD
    replacement_profile: environment_reference
    restore_policy: env_reference
    priority: 200
```

既存辞書へ追加する場合は、2つ目の`entities:`を作らず、既存listへ
`- id: deployment_password`から始まる項目を追加します。

環境変数を設定したprocessからGatewayを起動します。

```console
export DEPLOYMENT_PASSWORD="合成した確認用の値"
python3 securitymasker.py config-check
python3 securitymasker.py gateway
```

実際のcredentialをcommand lineへ直接書くとshell historyへ残ることがあります。利用環境のsecret
管理方法から環境変数を渡してください。SecurityMaskerは環境変数が未設定または空なら起動を
拒否します。

`env_reference`では、外部LLMのresponseやtool argumentへ実値を戻さず、
`${SECURITYMASKER_SECRET_...}`形の参照を残します。重大secretに辞書で弱いpolicyを指定しても、
組込みの安全下限より弱くなりません。

全policyは[設定リファレンス](../reference/configuration.md#restore_policy)を参照してください。
