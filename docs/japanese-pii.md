# 日本語PII検出方針（§14、§31）

日本固有情報を一つの検出方式だけで十分に扱えるとは仮定せず、三層構成にする（§14）。

1. **ユーザー登録辞書**（最優先・最信頼） — 氏名・会社名・プロジェクト名など（`entities:`）。
2. **形式・チェックサム・文脈語ベースの Recognizer**（決定論的） — 下表。
3. **日本語 NER**（標準・既定ON） — 固定modelで未登録の一般的な固有表現を補う。

## 決定論的 Recognizer

| エンティティ | Detector | 判定の要点 | 既定 restore |
|---|---|---|---|
| `JP_MY_NUMBER` | `japanese_my_number` | 12桁＋**公式チェックディジット検証**、文脈語で加点、チェック不一致は非検知（§14.5） | `block` |
| `PHONE` | `japanese_phone` | 固定/携帯/フリーダイヤル/+81/括弧/内線、区切りなし数字は文脈必須（§14.3） | `literal` |
| `JP_POSTAL_CODE` | `japanese_postal_code` | `〒`／「郵便番号」／後続県名がある時のみ（§14.6） | `literal` |
| `JP_ADDRESS` | `jp_address`（複合） | 郵便番号+都道府県+市区町村+丁目/番地/号+建物を**1スパン**に統合（§14.2） | `literal` |
| `DATE_OF_BIRTH` | `date_of_birth` | 生年月日/誕生日/生まれ等の文脈がある日付のみ昇格（§14.7） | `literal` |
| `EMAIL` / `IP_ADDRESS` / `CREDIT_CARD` | `formats` | メール構文、IPv4 レンジ検証、カードは **Luhn** 検証（§11） | email/ipv4=literal, card=`block` |
| API キー等 | `secret_patterns` | OpenAI/Anthropic/GitHub/AWS/JWT/PEM/DB URL 等 | `env_reference` |

正規化は **NFKC**（全角→半角等）で行い、検出位置は原文へオフセット復元して**元の表記を保持**します（§12, §14.4）。

## 誤検知対策（precision）

- コード領域では未登録氏名の NER を既定で保守的に（`ner.min_score` 高め、§17）。
- 「さくら」「葵」等の曖昧語は NER 単独で高信頼にしない（文脈語で加点、§14.1）。
- `123-4567`（商品番号）→ 郵便番号にしない、`build_id` の数字列 → 電話にしない（文脈必須）。
- チェックサム不一致のマイナンバー/カードは検知しない。

## 標準日本語NER

`scripts/setup`は標準依存を固定lockから導入し、次のmodelを利用者のローカルcacheへ取得する。

```bash
./scripts/setup
```

`securitymasker init`が生成するv2設定では既定ONである。

```yaml
japanese_ner:
  enabled: true
  model: tsmatz/xlm-roberta-ner-japanese
  revision: aba094e118d5ffc622e9b25e07edc49f9dd85feb
  min_score: 0.7
  local_files_only: true
  allow_unverified_model: false
```

model ID、revision、weight/tokenizer/config全artifactのsizeとSHA-256をmanifestへ固定する。
runtimeは検証済みsnapshotのローカルpathだけを`transformers`へ渡し、request処理中にnetwork
へ到達しない。model欠落、digest不一致、load失敗、label schema不一致、offset取得不能、
推論失敗は空結果へ変換せず、起動またはrequestをfail-closedで拒否する。

NERは**辞書未登録の氏名・組織・地名**を検出できる。ただし組織固有のproject名や秘密語を
推測できるとは保証しないため、利用者辞書が最優先の保護層である。NERはpriority 80とし、
重複時は辞書・決定論的Recognizerを優先する。code文脈ではfuzzy NERだけを既定で無効にするが、
辞書・secret pattern・形式検出は全contextで動作する。

実modelを使うrelease gateは`SM_REQUIRE_MODEL=1`を設定し、
`tests/unit/test_standard_ner.py`と`tests/unit/test_model_supply_chain.py`を実行する。
出典と再配布条件は[model-licenses.md](model-licenses.md)を参照。

## 評価（§31）

`tests/evaluation/`（`corpus.py` + `test_evaluation.py`）に匿名化・合成の日本語コーパス（自然文/
チャット/ログ/設定/コード、正例・負例）と precision/recall/F1 ハーネスを用意。エンティティ型別と
全体の指標を出力し、ベースライン閾値で回帰を検出します。**テストに実在人物・実際の番号は使いません（§30）。**
