# 辞書をカスタマイズする

このページでは、会社名、人名、顧客名、project名など、組織内で重要な語を
`securitymasker.dict`へ登録します。通常の固有名詞を追加するだけなら、完全な設定schemaを
理解する必要はありません。

## 会社名と表記揺れを登録する

`entities`へ1項目追加します。

starter辞書にはすでに`entities:`があります。`entities:`をもう一つ作らず、既存listへ
`- id: acme_company`から始まる項目を追加してください。

```yaml
entities:
  - id: acme_company
    type: ORGANIZATION
    values:
      - 株式会社極秘技研
      - 極秘技研
    replacement_profile: prose_identifier
    restore_policy: literal
    priority: 100
    case_sensitive: true
```

- `id`は設定内の識別子です。機密情報を含めません。
- `type`は値の種類です。会社名には`ORGANIZATION`を使います。
- `values`には検出したい表記を列挙します。
- 通常の固有名詞は`prose_identifier`と`literal`を使います。

## 人名やproject名を登録する

人名には`PERSON`、project名には`PROJECT_NAME`を使います。

```yaml
  - id: project_cedar
    type: PROJECT_NAME
    values:
      - Project Cedar
      - Cedar計画
    replacement_profile: prose_identifier
    restore_policy: literal
    priority: 100
    case_sensitive: true
```

API key、password、秘密鍵はこの方法で平文登録せず、
[credentialを環境変数から登録する](credentials.md)を参照してください。

## 変更を確認する

Gatewayを停止してから辞書を編集し、次を実行します。

```console
python3 securitymasker.py config-check
python3 securitymasker.py entities
python3 securitymasker.py preview < synthetic-prompt.txt
```

`entities`は値そのものを表示せず、ID、type、policy、variant件数を表示します。`preview`で期待する
語がmaskされ、関係のない文が過剰にmaskされないことを合成データで確認します。

確認後にGatewayを再起動します。設定や辞書のhot reloadは行いません。

## 詳細設定

全field、許容値、policyの安全下限は[設定リファレンス](../reference/configuration.md)にあります。
独自形式を検出する場合は[独自patternを追加する](custom-patterns.md)へ進んでください。
