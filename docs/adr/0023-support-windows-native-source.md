# ADR-0023 — Windows 11 x64のnative source版を対応範囲へ加える

> Windows source版の既定data配置は、後続の
> [ADR-0024](0024-unify-source-adjacent-layout.md)で置き換えた。現行のlauncher隣接layoutは
> 2026-08-03にWindows native再検証を完了した。

- 状態：採用
- 日付：2026-08-02
- 関連：[ADR-0013](0013-reject-best-effort-windows-support.md)、
  [ADR-0021](0021-add-windows-native-source-target.md)、
  [ADR-0022](0022-add-atomic-backup-restore.md)、
  [Windows x64 source evidence](../development/release-evidence/windows-x64-source-2026-08-02.md)

## 背景

ADR-0013はWindows分岐や部分的な起動だけを根拠にbest-effort対応を表明することを却下し、native
source版の再検討条件を定めた。ADR-0021は最初のtargetをWindows 11 x64、CPython 3.12 x64、local
fixed NTFSへ限定し、DACL、native dependency、NER、SQLite、clientと外向き通信遮断gateを具体化した。

2026-08-02に専用standard userのfresh profileとsource archiveからsetupと両modeを再現し、Windows
Firewallでそのuserのloopback以外の外向き通信を遮断して実Codex／Claude Code CLI E2Eを完走した。
その後の最新treeでもnative security境界と最終pre-release gateを再確認した。

## 再検討条件の監査

| 条件 | 結果 |
|---|---|
| target OS、architecture、Python | Windows 11 x64 build 26100以降、CPython 3.12 x64へ限定 |
| DACL | current user、SYSTEM、Administratorsだけのprotected DACLをWindows APIで作成・検査 |
| negative matrix | permissive／継承／wrong owner／unknown ACE／NULL DACL／UNC／removable／subst／reparseを拒否 |
| native setup | cmd.exe、Windows専用lock、wheel-only、Visual Studioなしで成功 |
| 固定NER | 6 artifactのdigest、offline load、CPU推論、実snapshot改竄拒否に成功 |
| SQLite | 暗号化、restart、DB/key pair再読込、wrong key／mode、tamper、WAL、二重writer、終了・強制終了後復旧に成功 |
| client | Codex user config、Claude環境変数、両Desktopのlocal起動方法と解除を文書化し、doctorをread-onlyで接続 |
| 漏洩ゼロE2E | 専用userの外向きIPv4／IPv6をFirewallでblockし、実CLI 2件とlocal mockで成功 |
| fresh source | 新規standard user／profile、checksum検証済みarchive、Python 3.12、setup、両mode、release gateを完走 |

最新treeのpre-release gateはruff、mypy strict 73 source files、unit／evaluation 753件（5 skip）、mock
Gateway E2E 4件、Windows native process 3件に成功した。実CLI E2EではCodex CLI 0.146.0とClaude
Code 2.1.220を使い、local mockの最終payloadに合成原文がなく、client表示で原文が復元されaliasが
残らないことを確認した。

## 決定

Windows native source版を次の条件で対応範囲へ加える。

- Windows 11 x64 build 26100以降
- 64-bit CPython 3.12
- standard userが標準cmd.exeからsource archiveを実行
- source、製品data、model cacheをlocal fixed NTFSへ配置
- `scripts\setup.cmd`でWindows専用lockからwheelだけを導入
- 製品dataはmode別`%LOCALAPPDATA%\SecurityMasker\<mode>`の既定directory、または同じsecurity契約を
  満たす明示directoryへ配置
- 1 process、1 mode、1 loopback port、1 workerの既存製品契約を維持

Windows 10、ARM64、Python 3.11／3.13以降、ReFS、FAT、removable／network／subst drive、UNC path、
WSL2／Docker Desktop、Windows one-file版はこの判断に含めない。未検証条件をbest-effortで許容せず、
製品dataのsecurity検査に失敗した場合は起動を拒否する。

## 以前の条件からの具体化

- ADR-0013のPowerShell向けrunnerという表現は、ownerが使用する標準cmd.exeのpublic runnerへ置き換える。
  Windows APIや管理者操作に必要な内部PowerShellはlocale依存textを製品判定へ使わない。
- ADR-0021のclean VMは、最初のsource targetでは新規local standard user、fresh profile、checksum検証済み
  source archive、user単位Firewall境界による再現へ置き換える。既存developer profile、認証、`.venv`、
  製品dataを共有しないことをpreflightする。
- backup媒体、退避fileの運用とrestore作業はADR-0022どおり製品範囲外であり、Windows対応条件へ
  含めない。配置された稼働dataのDB/key不一致やtamperは引き続きfail-closedで拒否する。
- Codex appとClaude Code DesktopはCLIと共有する設定・protocolの手順を提供するが、自動gateはCLIを
  protocol surrogateとして使う。Desktop UIの手動smoke testがないreleaseをDesktop実証済みとは
  表現しない。cloud／remote sessionはloopback Gatewayへ接続できないため対象外である。

## 結果

- 限定したWindows native source環境では、利用者文書に従って実際の機密情報を扱える。
- Windows one-file版のbuild、署名、配布は引き続き別判断とする。
- Windows build、Python minor、dependency lock、Codex／Claude Codeのprotocol baselineを更新するときは、
  native pre-release gate、fresh source gate、Firewall隔離実CLI E2Eを再実行する。
- enterprise policy、antivirus、filesystem filter等が契約を妨げる環境ではfail-closedな拒否を優先し、
  対応条件を暗黙に緩和しない。
