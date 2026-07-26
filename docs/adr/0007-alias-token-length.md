# ADR-0007 — alias token長と形状制約のあるalias空間

- 状態：採用
- 日付：2026-07-25
- 置換対象：なし。ADR-0005（alias HMAC／AES-GCM）、doc/06 P1-8 に関連。

## 背景

監査により、alias 空間が文書上の保証を支えるには小さすぎると判明しました。

- prose alias（`SM_PERSON_7F3A91`）は **6 hexadecimal文字 = 24 bit**だった。
- IPv4 alias は RFC 5737 の `/24` 一つ、すなわち**254通り**しかなかった。
- 短い `numeric` alias は原文の桁数を継承するため、4桁なら10,000通りしかない。
- collision は session 内で検出していたが session 間では検出せず、一方で文書は
  「session ごとに異なる alias」と保証していた。

24 bit には二つの弱点があります。birthday collision は、一つの session に1,000件の
mapping がある場合、少なくとも一度衝突する確率が
`1 - exp(-1000^2 / 2^25) ≈ 3 x 10^-2`、約34分の1になります。衝突を稀にする設計と
しては高すぎます。また、1,670万候補は offline で容易に総当たりでき、攻撃者が alias
形式を知れば特定 token 空間を低 cost で探索できます。

## 決定

1. **既定のalias tokenを12 hexadecimal文字（48 bit）へ拡大します。**
   session ごとの mapping 上限10,000件（`MAX_MAPPINGS_PER_SESSION`）では、
   birthday collision 確率は約
   `1 - exp(-10^8 / 2^49) ≈ 1.8 x 10^-10` です。衝突時の延長処理は残すため、
   12文字は上限ではなく下限です。
2. **IPv4にはRFC 5737の三つのdocumentation rangeをすべて使います。**
   `192.0.2.0/24`、`198.51.100.0/24`、`203.0.113.0/24` により、session ごとの
   alias は254件から762件になります。
3. **形状保持profileの空間が小さいことを受け入れ、枯渇時はfail-closedにします。**
   IPv4 alias は有効かつ非 routable な IPv4 である必要があるため762件が正直な
   上限です。4桁の numeric alias も10,000件を超えられません。全候補が埋まったら
   `aliases.factory` は `AliasCollisionError` を送出し、request を拒否します。
   異なる二つの秘密へ同じ alias を再利用しません。
4. **session間の性質を正確に記述します。** 鍵は session ごとに独立しているため、
   同じ秘密も別 session では独立した alias を得ます。大きな空間では一致確率は
   無視できますが、IPv4／numeric は空間が小さく別 session 間で一致し得ます。
   したがって「必ず異なる alias」ではなく、**mapping のunlinkability**を保証します。

## 影響

- `SM_PERSON_7F3A91` は `SM_PERSON_7F3A9155C21B` のように長くなります。
  prompt はわずかに増えますが、可読性への影響はありません。
- 762件を超える異なる IPv4、または桁数が許す候補数を超える numeric 値をマスクする
  session は、alias を黙って再利用せず fail-closed になります。該当 operator は
  形状制約のない `prose_identifier` profile へ切り替えてください。
- documentation range は定義上非 routable であり、alias が実 endpoint と誤認されたり
  誤接続されたりすることはありません。

## 検討した代替案

- **RFC 6598 shared address space（100.64.0.0/10）**：約400万の IPv4 alias を
  得られますが、carrier 内では実際に routing される空間です。到達可能に見える alias
  は §9.4 に反するため不採用。
- **IPv4の形状保持をやめて`SM_IP_...`にする**：設定ファイル、connection string、
  parser を壊すため既定にはしません。entity ごとに別 profile を選ぶことはできます。
- **sessionをまたぐglobal collision検出**：session 横断の alias index が必要となり、
  session ごとの鍵で防いでいる相関を再導入するため不採用。
