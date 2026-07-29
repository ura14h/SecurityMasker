# Third-party notices

## Scope

Repository rootの`LICENSE`は、SecurityMasker自身のsource codeと文書に適用されます。
SecurityMaskerが利用、取得、または将来artifactへ同梱する第三者componentのlicenseをMITへ
変更するものではありません。

## Source release

通常のsource archiveは、Python dependencyと日本語NER model weightを同梱しません。
`scripts/setup`が利用者環境で次を取得します。

- `requirements.lock`、`requirements-torch-cpu.lock`、`requirements-ner.lock`に固定した
  Python distribution
- 固定revisionと全runtime artifact digestをmanifest化した日本語NER model

Python distributionは各distributionのlicenseとnoticeに従います。導入後の`.dist-info` metadata、
各upstream repository、lock fileを対応付けて確認してください。source archiveを配布することと、
setupが第三者componentを利用者環境へdownloadすることを区別します。

## Japanese NER

標準NERの出典と確認済み表記は次です。

| 対象 | 固定対象 | 公開元のlicense表記 |
|---|---|---|
| 採用model | `tsmatz/xlm-roberta-ner-japanese@aba094e118d5ffc622e9b25e07edc49f9dd85feb` | MIT |
| base model | `FacebookAI/xlm-roberta-base` | MIT |
| 学習dataset | `stockmarkteam/ner-wikipedia-dataset` | CC BY-SA 3.0 |

一次情報、artifact manifest、再配布判断は
[`docs/reference/model-licenses.md`](docs/reference/model-licenses.md)に記録しています。

## Binary release

PyInstaller one-fileはPython runtime dependencyとmodel weightを再配布するため、source releaseとは
条件が異なります。次が完了するまで外部公開しません。

1. 対象artifactへ含まれる全transitive componentのversionとlicenseを列挙する。
2. 各licenseが要求するcopyright、license本文、NOTICE、attributionをartifactから閲覧可能にする。
3. model weightと学習datasetの関係について、作者・権利者または適切な法務確認を得る。
4. clean-machine gateでnotice一式が実artifactへ含まれることを検査する。

`securitymasker.spec`のmetadata収集だけを完全なlicense inventoryとは扱いません。binaryの公開状態は
[`docs/development/status.md`](docs/development/status.md)を正とします。
