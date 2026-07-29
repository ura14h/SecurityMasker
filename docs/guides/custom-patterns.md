# 独自patternを追加する

規則的な社内ticket番号や顧客IDは、Python `re`形式のpatternで検出できます。正規表現が不要な
固有名詞には、より安全で分かりやすい[辞書entity](customize-dictionary.md)を使ってください。

## Ticket番号を登録する

```yaml
patterns:
  - id: internal_ticket
    pattern: 'INC-([0-9]{6})'
    group: 1
    type: CUSTOMER_ID
    replacement_profile: numeric
    restore_policy: literal
    priority: 120
```

この例では`INC-`を残し、capture group 1の6桁だけをmaskします。match全体をmaskする場合は
`group: 0`を指定します。

starter辞書にはすでに`patterns:`があります。`patterns:`を重複させず、既存listへ新しい項目を
追加してください。

## 安全に確認する

Gatewayを停止し、短い正常例、境界値、matchしない例、長い合成入力で確認します。

```console
python3 securitymasker.py config-check
python3 securitymasker.py preview < synthetic-pattern-cases.txt
```

存在しないcapture groupや既知の危険なbacktracking形状はload時に拒否されます。ただし、
すべての高costな正規表現を自動判定できるわけではありません。広すぎるpatternを避け、必要な形式を
できるだけ具体的に記述してください。

fieldの詳細は[設定リファレンス](../reference/configuration.md#patterns)にあります。
