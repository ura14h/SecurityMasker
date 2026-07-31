# ADR-0019 — 明示的な`init --force`で標準layoutを完全初期化する

- 状態：採用
- 日付：2026-07-31
- 置換・修正する決定：
  [ADR-0012](0012-renew-package-design.md) の「`--force`でも既存DBに対応するkeyを交換しない」
  という記述
- 関連：[architecture](../development/architecture.md)、
  [backup and restore](../operations/backup-restore.md)

## 背景

通常の`securitymasker init`は、config、辞書、state、master keyのいずれかが存在すれば拒否する。
これは再実行による設定消失やDB/key不一致を防ぐ既定動作として正しい。一方、利用者が既存の辞書、
session、alias対応表を不要と判断し、製品を意図的に初期状態へ戻したい場合にも、手作業で複数fileを
削除する必要があった。部分的な削除はDB/key不一致や古い設定の残存を起こしやすい。

最初の安定releaseはapplication `1.0.0`、config schema v1から開始し、それ以前の公開configを
移行する契約はない。現在必要なのはschema migrationではなく、既存状態を捨てることを明示した
完全初期化である。

## 決定

- 通常の`init`は既存artifactを一切変更せず、従来どおり拒否する。
- `init -f`と`init --force`を追加する。破壊対象を明示させるため、`--force`には
  `--directory`を必須とする。
- `--force`は指定directory直下の標準`securitymasker.config`、`securitymasker.dict`、
  `securitymasker.state/`を一組として破棄し、新しいconfig、starter辞書、256-bit master keyへ
  置換する。既存SQLite、全session、response binding、alias対応表は失われる。
- 新しいSQLiteは通常の`init`と同様に作らず、Gatewayの初回起動時に作成する。
- 古いconfigに書かれた外部pathを追跡して削除しない。指定directory内でも標準stateに管理外entry、
  symlink、異なるfile種別、別ownerのentryがあれば、対象を推測せず拒否する。
- master keyのnon-blocking lockを取得できない場合は、Gatewayが稼働中とみなして拒否する。
- 新しいlayoutを先に一時directoryへ生成してconfig検証を完了し、その後に旧layoutを一時退避して
  切り替える。切替失敗時は旧layoutへrollbackする。成功後は退避した旧layoutを削除する。
- client設定、NER model cache、指定directory内の管理外fileを変更しない。
- key、辞書内容、alias対応表を標準出力、標準error、logへ表示しない。

`--force`はconfig migration、rekey、backupではない。既存状態を残す必要がある場合は、事前に
config、辞書、DB、keyを同じ時点の組としてbackupする。

## 結果

- 通常利用者の非破壊な既定動作を維持しつつ、手動の部分削除をせず完全初期化できる。
- `--force`後に古いsessionのaliasを復元できないため、利用者はclientで新しい会話を開始する必要が
  ある。
- 稼働中state、管理外file、曖昧なdirectoryに対してはfail-closedとなる。
- 将来、公開済みschema間の移行が必要になった場合は、状態を破棄する`--force`とは別の設計判断と
  commandを追加する。
