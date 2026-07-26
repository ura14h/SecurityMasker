# 日本語PII検出方針

一つの検出方式だけで十分とは仮定せず、三層を重ねます。

1. ユーザー辞書: 氏名、会社名、顧客名、project名など。最優先・最信頼。
2. 決定論的detector: 形式、checksum、文脈語で判定できるPII/secret。
3. 標準日本語NER: 固定modelで未登録の一般的な人名、組織名、地名を補完。

## 主な決定論的検出

| entity | 判定の要点 | 既定restore |
|---|---|---|
| `JP_MY_NUMBER` | 12桁と公式check digit、文脈語 | `block` |
| `PHONE` | 固定/携帯/+81/括弧/内線。区切り無しは文脈必須 | `literal` |
| `JP_POSTAL_CODE` | `〒`、郵便番号、後続県名などの文脈 | `literal` |
| `JP_ADDRESS` | 都道府県、市区町村、丁目/番地/号等を複合span化 | `literal` |
| `DATE_OF_BIRTH` | 生年月日等の文脈がある日付 | `literal` |
| `EMAIL` / `IP_ADDRESS` / `CREDIT_CARD` | email構文、IP range、Luhn | cardは`block` |
| API key等 | provider key、JWT、PEM、DB URL等 | `env_reference` |

正規化はNFKCを使い、検出位置を原文offsetへ戻して元の表記を保持します。辞書と決定論的detectorは
codeを含む全contextで動きます。fuzzy NERだけがcode系contextをskipします。

## 標準日本語NER

採用modelは次で固定しています。

```text
tsmatz/xlm-roberta-ner-japanese
revision: aba094e118d5ffc622e9b25e07edc49f9dd85feb
```

model、tokenizer、configの全artifactについてsizeとSHA-256をmanifestへ固定します。runtimeは
検証済みlocal snapshotだけを読み、`trust_remote_code`を使わず、request中にnetworkへ到達しません。
欠落、digest不一致、load/推論異常は空結果へ変換せずfail-closedです。

NERは一般的な固有表現を補いますが、社内code nameなど任意の組織固有語は保証しません。
その用途には `securitymasker.dict` を使ってください。modelとdatasetの出典・配布判断は
[model licenses](../model-licenses.md) に記録しています。

## 評価

`tests/evaluation` は実在人物や実番号を含まない合成corpusで回帰を検出します。そこで得られる
precision/recall/F1は実用環境での性能保証ではありません。公開前に実データをtest fixtureへ
持ち込まず、利用者自身の重要語をlocal `preview` で確認してください。
