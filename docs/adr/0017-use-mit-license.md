# ADR-0017 — project自身をMIT Licenseで公開する

- 状態：採用
- 日付：2026-07-28
- 関連：[model licenses](../model-licenses.md)、
  [development status](../development/status.md)

## 背景

最初の公開releaseに先立ち、SecurityMasker自身の利用・変更・再配布条件を単純にしつつ、
第三者dependencyと日本語NER modelの条件をproject licenseと混同させない必要がある。

license変更時点のrepository履歴ではauthorはHiroki Ishiura 1名であり、SecurityMasker自身の
copyright表記を明示できる。従来のApache-2.0はまだ公開releaseとして配布されていない。

## 決定

SecurityMasker自身のsource codeと文書を、Copyright (c) 2026 Hiroki Ishiuraの
MIT Licenseで提供する。repository rootの`LICENSE`、package metadata、配布container metadataを
MITへ統一する。

setupが取得するPython distribution、model、base model、学習datasetはこの変更の対象外であり、
各公開元のlicenseに従う。projectのMIT Licenseが第三者componentを再licenseするとは表現しない。
境界と配布形態はrepository rootの`THIRD_PARTY_NOTICES.md`に記録する。

## Binaryへの影響

source archiveはdependencyとmodel weightを同梱しない。one-file binaryはそれらを再配布するため、
全transitive componentのlicense/notice収集とmodel再配布判断が別途必要である。projectをMITへ
変更しても既存のbinary公開blockerは解除しない。
