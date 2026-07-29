# 日本語NER modelの出典と再配布条件

この文書は、固定model、base model、学習datasetの出典と、配布形態ごとの判断を記録します。

確認日: 2026-07-26

SecurityMaskerの標準NERは、次のmodel revisionをbyte単位で固定する。

| 対象 | 固定値 | 公開元のlicense表記 |
|---|---|---|
| 採用model | `tsmatz/xlm-roberta-ner-japanese@aba094e118d5ffc622e9b25e07edc49f9dd85feb` | MIT |
| base model | `FacebookAI/xlm-roberta-base` | MIT |
| 学習dataset | `stockmarkteam/ner-wikipedia-dataset` | CC BY-SA 3.0 |

機械可読な出典、license ID、参照URLは
`src/securitymasker/models_fetch.py`の`ModelManifest`へartifact digestと一緒に記録する。

一次情報:

- 採用model:
  <https://huggingface.co/tsmatz/xlm-roberta-ner-japanese>
- base model:
  <https://huggingface.co/FacebookAI/xlm-roberta-base>
- 学習dataset:
  <https://github.com/stockmarkteam/ner-wikipedia-dataset>

datasetの公開元は、Wikipedia日本語版と同じCC BY-SA 3.0に従うこと、商用利用できること、
改変・再配布時にはWikipediaの条件を参照することを明記している。

## 配布判断

ソース標準setupはmodelをGit repositoryへ収録せず、固定revisionを利用者のローカルcacheへ
取得してdigest検証する。この経路ではSecurityMasker release artifactがmodel weightを再配布
しない。

一方、PyInstaller one-fileへweightを埋め込んで公開する場合は、採用modelのMIT noticeを
同梱するだけで十分か、CC BY-SA 3.0のdatasetから学習したweightに追加条件が及ぶかを、
公開情報だけから断定しない。これは法的助言ではない。weight同梱binaryを公開する前に、
model作者・dataset権利者への確認または適切な法務確認をrelease gateとする。

確認が終わるまで許可するのは、source checkoutと、setupが利用者環境へ固定modelを取得する
配布形態である。weight同梱binaryの外部公開は許可しない。
