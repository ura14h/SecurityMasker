# Trusted local toolを設定する

`trusted_local_tools`は高度で高riskな設定です。通常は空のまま使用してください。

既定では、表示textに含まれるaliasは復元しても、tool argument内のaliasは実値へ戻しません。
これにより、外部MCP、provider-hosted tool、実体不明のtoolへ原文を渡すことを防ぎます。

## 登録を検討できる条件

次をすべて管理できるtoolだけを登録できます。

- tool processが利用者のPC内で動く
- argumentの保存先とlogを確認できる
- telemetryとnetwork送信を管理できる
- 子processへ渡す値を管理できる
- 同名のremote toolと取り違えない

一つでも確認できなければ登録しません。

```yaml
tool_trust:
  trusted_local_tools:
    - local_database_client
```

登録すると、その名前のtool argumentへ`literal` policyの原文を復元します。名前の完全一致だけで
判定するため、localという名前や説明だけを信頼根拠にしてはいけません。

変更後は合成値だけを使って`config-check`、`preview`、対象toolの動作を確認してください。
仕様の詳細は[設定リファレンス](../reference/configuration.md#tool_trust)にあります。
