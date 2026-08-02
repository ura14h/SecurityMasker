# ADR-0021 — Windows native source版は専用NTFS directoryと保護DACLを必須にする

> Windows source版の既定data配置とsource rootの扱いは、後続の
> [ADR-0024](0024-unify-source-adjacent-layout.md)で置き換えた。

- 状態：採用（主要実装済み・native gate検証中）
- 日付：2026-08-02
- 関連：[ADR-0013](0013-reject-best-effort-windows-support.md)（best-effort対応の却下）、
  [ADR-0012](0012-renew-package-design.md)（package設計）、
  [development status](../development/status.md)、
  [compatibility](../reference/compatibility.md)
- 対象：Windows native source版の最初の対応targetとlocal file保護

> このADRはWindows対応済みという判断ではない。ADR-0013の再検討条件をすべて満たすまで、
> Windows nativeは公開対応範囲外であり、合成dataだけを使って実装・検証する。

## 背景

Windows native source版を成立させるには、POSIXのownerと`0600`／`0700`をWindows DACLへ
読み替えるだけでは足りない。config、辞書、master key、SQLiteとsidecarを保護しても、親directoryを
別principalが変更できれば、起動前のfile差し替えや削除が可能になる。`doctor`も、検査していないDACLを
`private`と表示してはならない。

一方、source checkout直下はGitやeditorが管理する開発directoryであり、`init`がrepository全体のDACLを
書き換えることは過剰である。Windowsでは製品dataをsource checkoutから分離し、専用directory全体へ
一つの契約を適用する必要がある。

## 判断

### 最初の対応target

Windows source版の最初のnative gateを次へ限定する。

- Windows 11 x64、build 26100以降
- CPython 3.12 x64（最初の実測versionは3.12.10）
- local fixed drive上のNTFS
- standard user権限でのsetup、init、通常運用
- `cmd.exe`を主手順とし、PowerShellを必須にしない
- standard日本語NERを既定ONのまま使用する

Python 3.13以降、Windows 10、ARM64、ReFS、removable drive、UNC、network drive、subst drive、
Dev Driveは別matrixであり、このgateの成功だけでは対応を表明しない。one-file `.exe`は
ADR-0013の別条件へ残し、このADRでは扱わない。

### Windows data directory

Windowsの`init`は、既定ではmode別の専用directoryを使用する。

```text
%LOCALAPPDATA%\SecurityMasker\chatgpt\
%LOCALAPPDATA%\SecurityMasker\claude\
```

各directoryは`securitymasker.config`、`securitymasker.dict`、`securitymasker.state`を含む。
source checkoutへ設定を隣接させる既存のmacOS／Linux経路は変更しない。Windowsで`--directory`を
指定する場合も、local fixed NTFS上の専用directoryで、管理対象外entryを含まないことを要求する。
既存repository rootや一般用途directoryのDACLを`init`が暗黙に変更してはならない。

### DACL contract

管理対象directoryとartifactは次をすべて満たさなければならない。

1. ownerはprocessを実行するcurrent user SIDである。
2. DACLは存在し、NULL DACLでなく、継承から保護されている。
3. access-allowed ACEを持てるprincipalは次の三つだけである。
   - current user SID
   - `NT AUTHORITY\SYSTEM`（`S-1-5-18`）
   - built-in Administrators（`S-1-5-32-544`）
4. 三principalはいずれもFull Controlを持つ。directoryのACEは子directoryとfileへ継承させる。
5. inherited ACE、unknown SID、Everyone、Users、Authenticated Users、service SID、domain group、
   access-denied ACE、object-specific ACE、callback ACE、未知ACEを許可しない。
6. 管理対象自身または管理対象へ至る専用directory内にreparse pointを許可しない。
7. DACL、owner、filesystem種別、drive種別、reparse pointを判定できない場合はfail-closedとする。

AdministratorsとSYSTEMを許可するのは、同一machineの管理者侵害から秘密を隔離する契約ではないためで
ある。ただし一般userや別accountへの権限継承は許可しない。

同じDACL実装を次へ接続する。

- `init`による専用directory、config、辞書、state、master keyの作成
- config load
- SQLite DB、lock、WAL、SHMの作成・再検査
- Windowsでmaster keyを置換可能に保つroot-level `securitymasker.state.lock` writer lease
- `init --force`の置換前検査とstaging／backup
- read-only `doctor`
- backup／restore（実装時）

`icacls`の表示textはlocaleに依存するため、製品の判定やparseには使わない。Windows APIからSID、owner、
DACL、ACE、volume、reparse pointを検査する。利用者による手動ACL設定を安全性の前提にしない。

### setupとdependency

`scripts\setup.cmd`、`scripts\test-setup.cmd`、`scripts\release-check.cmd`を提供し、
`.venv\Scripts\python.exe`を使用する。Python選択は`py -3.12`で固定でき、global `python` aliasや
Visual Studio Build Toolsを前提にしない。

Windows用dependency lockはtarget上でwheelだけから解決・固定する。standard日本語NERのtransitive
dependencyがsource buildを要求した場合、利用者へcompiler導入を求めず、wheelの存在するversionへ
契約を見直す。

### 実CLI E2Eの外向き通信境界

Windowsでの実CLI漏洩ゼロE2Eは、外向きrouteのないVMに加えて、専用local standard userへ
Windows Firewallの明示deny ruleを適用する構成を認める。operatorとCodex Desktopは別userで動作し、
試験userの全processだけを境界内へ置く。

Firewall構成は次をすべて満たす場合だけVMのnetwork断と同等のrelease evidenceとして扱う。

1. ruleはadministratorがPersistentStoreへ作成し、試験userはadministrator groupへ属さない。
2. LocalUser条件を試験user SIDへ固定し、Domain／Private／Publicの全profileで有効にする。
3. outboundの全IP protocolについて、IPv4は`127.0.0.0/8`以外、IPv6は`::1`以外をblockする。
4. test開始前にActiveStoreのrule、SID、action、direction、profile、protocol、address rangeを再検査する。
5. 構造検査後に外部IPv4／IPv6へのcanary接続が失敗し、loopback上のGateway／mockだけが成功する。
6. pytest、Gateway、mock、Codex CLI、Claude Code CLIを同じ試験userで起動する。
7. ruleが欠落、不一致、判定不能、または試験userがadministratorの場合はE2Eを開始しない。

Windows Firewallでは明示block ruleが競合するallow ruleより優先される。host administratorはDACL契約と
同様に脅威境界外だが、試験user自身がruleを無効化できないことを必須にする。実行fileだけへruleを
付ける方式は、CLIが子processへnetwork処理を委譲した場合に境界から漏れるため採用しない。

## native gate

ADR-0013の条件を具体化し、少なくとも次をすべてWindows nativeで成功させる。

1. 正しいowner／DACL／NTFS専用directoryのaccept test。
2. permissive ACL、継承、wrong owner、unknown ACE、NULL DACL、network／removable drive、reparse
   pointのreject test。
3. init、config load、doctor、SQLite作成後の同一契約の確認。
4. model download、全artifact digest、offline load、推論、改竄拒否。
5. SQLite暗号化、restart、wrong key／mode、tamper、二重writer、WAL、graceful／forced termination。
6. `cmd.exe`からsource archiveの展開、setup、両mode init、preview、Gateway、停止、再起動。
7. 外向きrouteのないWindows VM、または上記Firewall境界で実Codex／Claude Code CLIとlocal mockを
   使う漏洩ゼロE2E。
8. standard userのclean Windows VMでsource release gateを最初から再現する。

test dataは合成値だけを使い、外部networkを構造的に遮断する最終E2Eが完成するまで実providerへ
送信しない。一項目でも未確認ならWindows対応済みとしない。

## 影響

- WindowsのOS差分はsecurity／setup／test harness境界へ隔離し、masking coreとprotocol adapterを
  forkしない。
- source checkoutと機密dataを分離でき、repository ACLを製品が変更せずに済む。
- mode別directoryによりconfig、key、DBを別のDACL境界へ置ける。
- Windows利用者はconfig pathを明示するか、Windows既定data directoryの解決を利用する。
- target matrixとnative gateが増える。Python、Windows、native wheel更新時に再検証が必要になる。
- 実装途中は[development status](../development/status.md)のWindowsを`対応外`のまま維持する。

## 却下した代替案

- **source checkout直下を自動保護する**：repository全体のACLを暗黙に変更し、他の開発toolへ影響する。
- **artifactだけを保護して親directoryを検査しない**：別principalによる差し替えを防げない。
- **継承ACLを許可し、危険なACEだけを探索する**：未知ACEや将来の継承変更を安全と誤判定し得る。
- **current userだけのDACLにする**：Windowsのservice／管理・復旧semanticsと衝突する一方、同一machineの
  administratorを脅威境界へ含める設計にはなっていない。
- **`icacls`の出力をparseする**：localeとOS versionで表示が変わり、fail-closedな判定根拠にできない。
- **Python 3.14を最初のtargetにする**：現行のmacOS／Linux source evidenceと離れ、native wheelの
  成立確認をWindows ACL実装へ同時に持ち込むため、3.12を先に固定する。
