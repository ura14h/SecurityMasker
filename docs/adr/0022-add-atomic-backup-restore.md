# ADR-0022 — 標準layoutを停止中にbackupし、新規directoryへだけrestoreする

- 状態：採用
- 日付：2026-08-02
- 関連：[ADR-0019](0019-add-explicit-destructive-init.md)、
  [ADR-0021](0021-add-windows-native-source-target.md)、
  [backup and restore](../operations/backup-restore.md)

## 背景

SecurityMaskerのconfig、辞書、暗号化SQLite、master keyは一つの運用単位である。特にDBとkeyは
1対1で、異なる時点やmodeの組合せでは復号できない。従来文書はGateway停止後の手動copyを案内して
いたが、部分copy、稼働中copy、Windows DACLの欠落、既存復元先の一部上書きを製品側で防げなかった。

Windows native source targetでは、backup先とrestore先もlocal fixed NTFS、current user owner、
protected DACLの契約を満たす必要がある。POSIXでもownerと`0600`／`0700`を維持しなければならない。

## 決定

### Backup

- `securitymasker backup --directory BACKUP --config CONFIG`を追加する。
- 初版はconfig、辞書、stateが同じ標準layout内にある場合だけを扱う。外部pathを追跡しない。
- Gatewayのwriter leaseをnon-blockingで取得できない場合は、稼働中とみなして何もcopyしない。
- config、辞書、master keyと、存在する場合はSQLite DBを同じstaging directoryへ保存する。
- SQLite DBは単純な稼働中file copyではなく、停止確認後にSQLite backup APIでsnapshotを作る。
- format version、mode、file size、SHA-256を秘密値を含まないmanifestへ記録する。
- backup先は存在してはならない。隣接stagingで全検証を完了してからrenameし、既存backupを
  上書きしない。
- Windowsではbackup先と全fileへADR-0021のDACLを適用し、local fixed NTFS以外を拒否する。
  POSIXではdirectoryを`0700`、fileを`0600`にする。

### Restore

- `securitymasker restore --backup BACKUP --directory DIRECTORY`を追加する。
- backup manifestのformat、entry集合、size、SHA-256、file種別、owner／権限を先に検査する。
- restore先は存在しない新規directoryだけを許可する。既存layoutの上書きや部分置換はしない。
- 隣接stagingへ標準layoutを組み立て、config／辞書をloadし、DBがある場合は対応keyとmodeで開ける
  ことを確認してからrestore先へrenameする。
- 検査またはrenameに失敗した場合はstagingだけを削除し、既存dataを変更しない。
- restore後のconfigはbackup時の相対pathを維持する。別のportやmodeへの変換、migration、rekeyは
  行わない。

### 共通

- config本文、辞書値、key、DB内容、alias対応表をstdout、stderr、log、manifestへ表示しない。
- symlink、reparse point、管理外entry、unknown manifest field、DB/key mismatch、wrong mode、tamperは
  fail-closedで拒否する。
- backup directory自体が機密dataであり、repository、issue、ticket、chat、暗号化されていない共有先へ
  置かない。

## 結果

- 利用者は複数artifactを手作業で組み合わせず、停止中の整合した一組を作成できる。
- restoreが既存状態を破壊しないため、誤指定時のdata lossを避けられる。
- 元の場所へ戻す場合も、まず新規directoryへrestoreして検証し、利用者が明示的に切り替える。
- 既存layoutをatomicに置換するrestore、incremental backup、圧縮、remote storage、schema migration、
  rekeyは将来の別判断とする。

## 却下した代替案

- **稼働中の4 fileをそのままcopyする**：WALとDBの時点がずれ、対応表を欠落させ得る。
- **restoreで既存directoryへ上書きする**：一部成功時にconfig、DB、keyの組を壊す。
- **backupを単一archiveにする**：暗号化、path traversal、展開時権限を同時に持ち込むため、初版では
  private directoryを明示的な運用単位とする。
- **外部pathを自動追跡する**：意図しないdirectoryのcopyやrestore範囲拡大につながる。
