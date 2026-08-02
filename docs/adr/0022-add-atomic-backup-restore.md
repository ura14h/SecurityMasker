# ADR-0022 — backup／restoreを製品機能の範囲外とする

- 状態：採用
- 日付：2026-08-02
- 関連：[ADR-0019](0019-add-explicit-destructive-init.md)、
  [ADR-0021](0021-add-windows-native-source-target.md)、
  [backup and restore](../operations/backup-restore.md)

## 背景

SecurityMaskerのconfig、辞書、暗号化SQLite、master keyは一つの運用単位である。特にDBとkeyは
1対1で、異なる時点やmodeの組合せでは復号できない。この性質から、製品CLIでatomicなbackupと
restoreを実装し、保存先のowner、権限、filesystemまで強制する案を検討した。

本製品はlocal PC上でlocal userが運用するproxyである。稼働中の製品dataは製品のsecurity境界だが、
利用者が退避したfileの保存先、暗号化、access control、世代管理と廃棄は利用者の運用境界である。
Windowsだけに保存先制約を追加すると、removable mediaや管理されたbackup先を不必要に拒否する。

## 決定

- `securitymasker backup`と`securitymasker restore`は追加しない。
- backup媒体、退避したfileの保護と保管、restore作業は製品範囲外とする。
- 製品文書は、同じ時点のconfig、辞書、DB、keyを一組として扱う必要性と、製品再開時の
  `config-check`、`doctor --require-ready`による確認だけを案内する。
- 稼働中の標準layoutに対するowner、権限、DACL、local fixed driveの契約はADR-0021どおり維持する。
  その契約をbackup媒体へ拡張しない。
- DB/key mismatch、wrong mode、tamperを検出した製品dataは従来どおりfail-closedで拒否する。
- Windows native sourceの対応判断に、製品によるbackup／restore実装を要求しない。

## 結果

- 製品が利用者のbackup方針や保存媒体を制限しない。
- backupの取得時点、完全性、機密性、可用性とrestore作業は利用者が責任を持つ。
- 製品は配置された稼働dataの安全な読込みと、異常時のfail-closedに責任を限定できる。
- 将来backup／restoreの自動化が必要になった場合は、Windows固有機能ではなく全対応OSに共通する
  新機能として別ADRで判断する。

## 却下した代替案

- **製品CLIでatomic backup／restoreを実装する**：local userの運用範囲へ製品責任を広げ、保存媒体と
  filesystemへ過剰な制約を持ち込む。
- **Windows専用cmdで実装する**：同じ運用をOSごとに重複実装し、Windowsだけを特別扱いする。
- **backup先にも稼働中layoutと同じDACLを強制する**：利用者が選択した外部媒体や管理された保存先を
  正当な理由なく拒否する。
