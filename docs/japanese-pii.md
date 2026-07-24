# Japanese PII — 日本語 PII 検出方針（§14, §31）

日本固有情報は Presidio 標準だけでは十分に検知できない前提で、三層構成にしています（§14）。

1. **ユーザー登録辞書**（最優先・最信頼） — 氏名・会社名・プロジェクト名など（`entities:`）。
2. **形式・チェックサム・文脈語ベースの Recognizer**（決定論的） — 下表。
3. **日本語 NER**（任意・モデル差し替え可） — `ner.model` 設定時のみ有効（§14.1、ハードコードしない）。

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

## Presidio の位置づけ（§13, ADR-0004）

Presidio は**検出器**としてのみ利用（in-process、`presidio` extra）。alias 生成・可逆マッピング・
復元・構造保持・ストリーミング復元は SecurityMasker が管理。Presidio 未インストール時は当該
Detector が安全に no-op します。

## 評価（§31）

`tests/evaluation/`（`corpus.py` + `test_evaluation.py`）に匿名化・合成の日本語コーパス（自然文/
チャット/ログ/設定/コード、正例・負例）と precision/recall/F1 ハーネスを用意。エンティティ型別と
全体の指標を出力し、ベースライン閾値で回帰を検出します。**テストに実在人物・実際の番号は使いません（§30）。**
