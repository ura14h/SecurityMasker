# ADR-0009 — 日本語NER backendの比較と採用

- 状態：採用（[ADR-0012](0012-renew-package-design.md) で標準搭載・既定 ON へ変更）
- 日付：2026-07-25（2026-07-26更新）
- 関連：ADR-0010（model供給網）、ADR-0011（推論上限）、ADR-0012、doc/06 §5.3

> backend の比較・選定結果は維持する。任意 extra・既定 OFF・Presidio 併存という
> 配布判断だけを ADR-0012 で置き換える。

## 背景

dictionary は登録済み氏名を検出しますが、新規 customer、supplier、未登録の同僚などは
検出できません。複数回の監査で、未登録名が日本語対応の中核的な製品 gap とされました。

単に「Hugging Face NERを追加」すると torch が製品へ入るため、推測ではなく**実測**して
選定しました。以下は `tests/evaluation/ner_benchmark.py` を
`tests/evaluation/ner_corpus.py` に対して実行した値です。正例17件（gold span 35件）、
負例18件はすべて合成データです（§30）。漢字・ひらがな・カタカナ・romaji の氏名、
空白表記、敬称、架空の組織・地名、曖昧語（さくら／葵／ひかり）、code／shell／JSON／
YAML／diff 内の identifier を含みます。

PERSON の見逃しは漏えい、code fence 内 identifier の誤検出は code 破壊であり性質が
違うため、metric は entity ごとに示し、code の false positive を別集計します。

## 測定結果

Apple Silicon、CPU／MPS、model cache warm の条件です。各 backend の生の code 挙動を
測るため、測定時は `skip_code_contexts` を無効にしました。

| backend | PERSON F1 | ORG F1 | LOC F1 | prose FP | code FP | load | infer（35例） | peak RSS |
|---|---|---|---|---|---|---|---|---|
| presidio + `ja_core_news_md` | 0.80 | **0.46** | 0.89 | 2 | **5** | 0.9 s | 0.25 s | 481 MB |
| `tsmatz/xlm-roberta-ner-japanese` @0.5 | 0.95 | 0.71 | 1.00 | 5 | 0 | 3.4 s | 0.58 s | 825 MB |
| **`tsmatz/xlm-roberta-ner-japanese` @0.7** | **1.00** | **1.00** | **1.00** | **0** | **0** | 3.4 s | 0.58 s | 825 MB |
| `tsmatz/…` @0.9 | 0.75（R=0.60） | 1.00 | 1.00 | 0 | 0 | — | — | — |
| `Mizuiro-sakura/luke-japanese-base-finetuned-ner` | **拒否** | — | — | — | — | 72 s | — | 1320 MB |
| `jurabi/bert-ner-japanese` | **未評価** | — | — | — | — | — | — | — |

不採用候補の補足：

- **LUKE** は loader が拒否しました。tokenizer の `start`／`end` が `None` で、
  detection を文字 span に対応付けられず置換不能だったためです。旧実装は request
  途中で crash しましたが、現在は起動時に失敗します。また label が日本語
  （`人名`、`法人名`、`地名`）で旧 mapping に存在せず、**検出ゼロを clean result に
  見せる**問題もありました。漏えいを成功に見せる under-detection を防ぐため、後述の
  schema 検証を導入しました。
- **jurabi/bert-ner-japanese** は supply chain 上の理由で測定前に除外しました。
  `pytorch_model.bin` だけを公開し safetensors がなく、pickle weight の読み込みを
  禁止する規約に反します。CC-BY-SA-3.0 license の share-alike 義務も採用しません。

## 決定

**`tsmatz/xlm-roberta-ner-japanese`を標準 backend として採用**する。ADR-0012 の
release target では標準搭載・既定 ON とし、Presidio は撤去する。

Presidio は総合値だけでなく、ORG precision が0.38（raw）、F1が0.46で、10件の code
負例中5件を誤検出します。採用 model は `min_score=0.7` でこの corpus の全 entity が
満点となり、prose／code の false positive はゼロでした。skip-code policy 適用前から
code で誤検出しなかった唯一の候補です。

採用条件はすべて code で強制します。

1. **標準搭載、既定ON。** `scripts/setup` と binary build が固定依存と固定 model を
   準備する。model が利用できない状態を黙って決定論的検出器だけへ downgrade せず、
   起動時に fail-closed とする。明示的な無効化は診断で強く警告する。
2. **固定。** `ner.model` 指定時は `ner.revision` を必須とする。採用値：
   - model：`tsmatz/xlm-roberta-ner-japanese`
   - revision：`aba094e118d5ffc622e9b25e07edc49f9dd85feb`
   - `model.safetensors` sha256：`a042d71446dd23e16dc2dbb1c7bf5b56b616dd8a53cdbb9af26597ba978b40be`
   - `sentencepiece.bpe.model` sha256：`cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865`
   - `tokenizer.json` sha256：`62c24cdc13d4c9952d63718d6c9fa4c287974249e16b7ade6d5a85e7bbb75626`
3. **request処理中はoffline。** 既定を `local_files_only=True` とし、明示的な
   `securitymasker models fetch` または image build 時に取得する。user text を Hub
   へ送らない。
4. **safetensorsだけを許可し、remote codeを実行しない。**
   `trust_remote_code` は設定しない。
5. **load時にlabel schemaを検証する。** `id2label` を mapping と照合し、対応可能な
   label がない model は拒否する。未対応 label は token ごとに黙って捨てず記録する。
6. **load時にoffsetを合成probeで検証する。** span を報告できない tokenizer は
   request 途中ではなく起動時に失敗させる。
7. **event loop外で実行する。** 専用 bounded pool と admission limit を使用する
   （[ADR-0011](0011-bounding-model-inference.md)）。単純な `asyncio.to_thread` は
   default executor を無制限に増やし、stuck inference が process 全体を枯渇させる。
8. **`min_score`の既定値を実測上の最適値0.7にする。** 下げると prose false positive、
   上げると PERSON recall 0.60への低下が生じる。
9. **LOCを独立entity typeにする。** `LOCATION` と `JP_ADDRESS` を分ける。地名は
   個人住所ではなく、粗い NER hit に住所の sensitivity を継承させない。

## 影響

- 標準 setup と binary build は NER runtime と固定 model を含むため、配布容量と
  memory 使用量は増える。repository 自体には model weight を commit しない。
- 有効時は resident memory 約825 MB、起動約3.4秒です。long-lived proxy には許容
  できる一方、single-file binary は大きくなる。この trade-off は「機密情報をマスクする」
  という製品期待を優先して受け入れる。
- corpus は detector と同じ作成者による合成データです。**0.7でF1=1.00は実運用の
  1.00を意味しません。** 想定した failure shape に対する regression baseline です。
- NER は最も信頼度の低い signal とし、priority は最低、dictionary と deterministic
  detector との overlap には負け、code context では skip します。安全判定の根拠には
  しません（不変条件9）。

## 残存リスク

- rare surname や legal-form suffix のない組織など、合成 corpus が表さない誤りが
  残ります。
- revision と digest の固定は黙った置換を防ぎますが、配布元自体の侵害や training
  data は保証しません。NER は recall を広げるだけで他 control を緩めません。
- Hub は third party です。fetch は明示的で監査可能な step とし、許容できない場合は
  artifact を mirror して cache を向けてください。
