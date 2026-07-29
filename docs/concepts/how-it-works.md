# SecurityMaskerの仕組み

SecurityMaskerは、CodexまたはClaude Codeと外部LLMの間で動くlocal Gatewayです。入力に含まれる
機密情報をPC内でaliasへ置き換え、外部LLMにはmask済みの内容だけを送ります。responseに同じaliasが
含まれていれば、PC内で元の表記へ戻してclientへ返します。

## 外部LLMから見えるもの

例えば利用者が次を入力します。

```text
株式会社極秘技研のProject Cedarについて整理して
```

辞書で会社名とproject名を登録していれば、外部LLMには次のような値を送ります。

```text
SM_ORG_...のSM_PROJECT_...について整理して
```

外部LLMのresponseにaliasがそのまま含まれていれば、CodexまたはClaude Codeの表示では
`株式会社極秘技研`と`Project Cedar`へ戻ります。

## 3つの検出層

1. ユーザー辞書が、会社名、顧客名、project名など組織固有の語を検出します。
2. 決定論的detectorが、API key、秘密鍵、メール、電話、公的識別子などを検出します。
3. 日本語NERが、辞書にない一般的な人名、組織名、地名を補完します。

組織内だけで意味を持つ未知語をNERだけで100%推測することはできません。重要語はユーザー辞書へ
登録し、外部へ送らない`preview`で確認します。

## Sessionごとに分離する

aliasと元の値の対応はsessionごとに分かれます。同じ入力でも別sessionでは別aliasになります。
通常運用では対応表を暗号化SQLiteへ保存し、ChatGPT用とClaude用では別DBと別keyを使います。

別sessionのaliasを復元したり、別modeのstateを共有したりしません。

## 構造を保つ

文章だけでなく、JSON、code、shell command、patch、tool argumentでは構造を壊さない形の
placeholderを選びます。外部へ送る直前にpayload全体を再検査し、元の値が残っている場合は送信を
blockします。

tool argumentは表示textより慎重に扱います。既定では原文へ戻さず、明示的に信頼したlocal toolだけ
復元対象にできます。

## 認証情報

SecurityMaskerはproviderの認証を保存しません。対応する認証headerだけを、選択したmodeの上流へ
透過します。ChatGPT用portへAnthropic requestを送るなど、modeと違うrouteはlocalで拒否します。

## 障害時

config、model、detector、state、payload検査に問題がある場合は、保護能力を下げて送信を続けず、
requestを上流へ送りません。この動作をfail-closedと呼びます。

利用前に知るべき制限は[安全な使い方](../security/safe-use.md)、完全な技術境界は
[Threat model](../security/threat-model.md)、実装構成は
[Architecture](../development/architecture.md)を参照してください。
