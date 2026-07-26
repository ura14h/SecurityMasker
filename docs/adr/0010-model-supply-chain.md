# ADR-0010 — model supply chainの固定・検証・拒否形式

- 状態：採用
- 日付：2026-07-26
- 関連：ADR-0009（日本語NER backend）、doc/06 §P2-3

## 背景

Hugging Face NER model（ADR-0009）の採用により、third-party binary artifact が
security boundary の一部になります。これには性質の異なる二つの risk があります。

1. **誤ったartifactをloadする。** revision 固定は要求した commit を表すだけで、
   disk 上の byte が同一とは保証しません。部分 download、破損 cache、実行間の
   cache poisoning、手動変更後の file も正常な directory に見えます。
2. **artifactのloadでcodeを実行する。** `.bin`／`.pt`／`.pth`／`.ckpt`／`.pkl` は
   pickle 形式であり、load により資格情報と平文を保持する process 内で任意 code を
   実行できます。

masking proxy では、どちらも許容できません。

## 決定

**revisionだけでなく完全なmanifestを固定します。** loader が読む全 artifact の
SHA-256 と size を記録します。weight だけでなく全 file が対象です。`config.json` の
`id2label` を差し替えると検出対象が変わり、tokenizer file の差し替えも text 分割を
変えるため、いずれも固定します。

**directoryではなくmanifestを基準に検証します。** manifest を走査し、記載済みで
存在しない artifact を missing とします。directory を走査するだけでは、存在する
file しか検証せず不完全な download が通ります。

**変換後ではなく配布されたfile byteをdigest化します。** 末尾 newline も含めます。

**pickle形式をfile名で拒否し、`use_safetensors=True`を強制します。** safetensors が
ない場合だけ拒否する方式では、同居する pickle を transformers が選ぶ可能性があるため、
存在自体を不適格とします。

**未知modelは既定で拒否します。** manifest のない model は検証できません。受理には
`ner.allow_unverified_model=true` の明示が必要で、結果にも未検証と記録します。

**fetch時だけでなくload時にも検証します。** fetch 後の cache 変更を検出するため、
runtime は信頼前に再検査します。`local_files_only=True` と組み合わせ、user input を
契機とした download を防ぎます。

**`trust_remote_code=True`は使用しません。**

## 影響

model の追加・移動では、fetch、6 digest の取得、manifest 記録が必要です。これは意図した
cost です。

manifest の誤りには、不正 model を受理する方向と正規 model を拒否する方向があります。
後者を実際に経験しました。旧 test は直前に書いた fixture をその場で hash しており、
拒否対象しか検証していませんでした。末尾 newline を除いて取得した digest は正規 model
を拒否しましたが、suite はすべて通り、fetch と NER 有効起動が壊れていました。
`tests/unit/test_model_supply_chain.py` は現在、固定 manifest を実 cache snapshot と
照合します。model 不在時は pass ではなく skip します。

一般則として、拒否すべき入力だけを見た check は、何かを正しく受理できるとは限りません。

## 検討した代替案

- **revision固定だけ**：低 cost で一般的ですが、現実的な local threat である cache
  改変を検出できないため不採用。
- **weightをrepositoryへvendorする**：1.1 GBの binary を Git に入れることになり、
  model を commit しない明示指示に反するため不採用。
- **signature／provenance検証（sigstore、model signing）**：digest より強く、将来
  採用すべき方式です。必要 infrastructure がないため未実装とし、
  `docs/operations.md` に supply chain gap として明記します。
