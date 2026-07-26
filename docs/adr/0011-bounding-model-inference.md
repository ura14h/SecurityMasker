# ADR-0011 — textを検出対象外にしないmodel inferenceの上限制御

- 状態：採用
- 日付：2026-07-26
- 置換対象：2026-07-25に導入したrequest単位の「detector pass budget」
- 関連：ADR-0009、ADR-0010、doc/06 §P1-5、§P1-7

## 背景

model-backed detection は deterministic detector より高 cost です。ここから独立した
二つの圧力が生じ、両者を混同したことで脆弱性が発生しました。

**圧力1 — 一requestに無制限のCPUを消費させない。** inference は同期的で CPU-bound な
Python 処理であり、割り込み不能です。`asyncio.wait_for` は待機を終えるだけで処理を
停止せず、cancel された request の worker も動き続けます。

**圧力2 — prose／code混在入力をsegment化する。** context segmentation（§17）は body
を typed span に分割し、「capitalised token = name」のように学習した fuzzy detector
を code identifier に適用しないために使います。全 detector を各 span に実行すると、
model 呼び出し回数が攻撃者の制御する segment 数に比例します。

最初の対策は request ごとの budget とし、先頭 N span だけ全 detector、それ以降は
deterministic detector だけを実行しました。しかし budget より後の text は NER に
一度も渡らず、clean scan と区別できません。inline code span を40個並べて末尾に
未登録名を置くと、日本語 NER の検出を確実にゼロにできました。

## 決定

**分割数ではなくtext量で処理を制限します。**

deterministic detector は安価で、秘密はどの context にあっても秘密であるため、全 span
へ個別・無制限に実行します（不変条件8）。

model-backed detector は fuzzy 対象 span を結合した text に対して**requestごとに一度**
実行し、検出位置を absolute offset へ戻します。code-like span の除外方針は変えません。
cost は prose 量の関数となり、同じ text の並べ替えや細分化で検出対象を隠せません。

この pass の選択には明示的な `fuzzy` marker を使い、`skip_code_contexts` を model
判定に流用しません。後者は user 設定可能で別の問いに答えるため、流用すると設定変更が
detector scheduling を黙って変えます。

**modelが実際に読める範囲へwindow化します。** transformer には512 token などの入力
上限があります。超過時に例外にならず tokenizer が警告して prefix だけを分類するため、
text を overlap 付き token-bounded window に分割します。境界上の氏名を両側で
見落とさないよう overlap を設け、後で重複を除きます。

**上限超過時は拒否します。** `defaults.max_fuzzy_chars` を超えた request は
fail-closed にします。一部だけ検査して成功を報告する挙動を防ぐため、上限による変化は
明示的な拒否でなければなりません。

**時間ではなくconcurrencyを制限します。** inference は固定 thread pool と admission
limit の下で実行し、上限超過時は待ち手のいない処理の後ろに queue せず request を
拒否します。放棄された処理も終了までは slot を占有します。

## 影響

- model は各 span を孤立してではなく周囲の prose とともに見るため、training 時の入力に
  近づきます。
- join boundary をまたぐ detection は、重なる各 span へ clip して span ごとに一度
  出力します。clip により model の要求より広くマスクすることはあっても狭くはしません。
- 非常に大きな prompt は部分検査せず拒否します。これは実際の挙動変更として文書化します。
- 誤った量を制限していた request 単位の pass budget は撤廃します。

## 検討した代替案

- **budgetを残し超過時にfail-closed**：正直な挙動で監査の最低条件を満たしますが、
  joined pass なら同じ cost で処理できる通常の code-heavy prompt まで拒否するため
  主案にはしません。
- **上限なしでspanごとにmodel実行**：blind spot はありませんが、攻撃者が制御する
  span 数に cost が比例します。
- **inferenceをtimeoutで囲む**：待機終了後も処理が続くため、処理量を制限しません。
