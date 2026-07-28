# ADR-0013 — 安全性未検証のWindowsをbest-effort対応として公開する案

- 状態：却下
- 日付：2026-07-28
- 関連：[ADR-0012](0012-renew-package-design.md)（パッケージ設計）、
  [ADR-0015](0015-evaluate-windows-linux-hosted-deployments.md)（Linux-hosted検証方針）、
  [development status](../development/status.md)、
  [compatibility](../development/compatibility.md)
- 対象：Windows向けsource版、one-file版、Windows上のCodex / Claude Code連携

> このADRが却下するのはWindows対応そのものではない。機密file保護とnative release gateが
> 未完成の状態で、部分的に動くことを根拠にWindows対応を表明する方針を却下する。後述の再検討条件を
> 満たした場合は、source版とone-file版を別々に再評価する。

## 背景

SecurityMaskerのPython実装には、Starlette、httpx、pathlibなどWindowsでも利用可能な要素が多い。
file path aliasもWindows driveとUNCの形状を扱い、SQLite writer leaseには`msvcrt.locking`分岐が
存在する。このため、限定的な手動実行だけを見ればWindowsでも動く可能性がある。

しかし本製品の合格条件は、processが起動してrequestを転送できることではない。元の機密情報を外部へ
送らず、sessionを混ぜず、構造を壊さず、障害時にfail-closedとなり、原文・鍵・平文対応表をlocalの
権限境界から漏らさないことが必要である。一部だけ検査して成功を返すことも許されない。

現行実装とrelease手順には、Windowsについて次のgapがある。

1. config、辞書、master key、SQLiteとsidecarのDACLを作成・検査しない。
2. `init`の`0600` / `0700`指定と`chmod`はWindows DACLの保護契約にならない。
3. `doctor`はWindows ACLを検査していないfileを`private`として成功表示し得る。
4. setup、test、build、release scriptはPOSIX shellと`.venv/bin`を前提にする。
5. dependency lockはWindows targetで解決・検証されておらず、標準NERのnative wheel、
   CPU-only Torch、SQLite runtimeの組合せが未確定である。
6. `msvcrt.locking`、WAL、異常終了後の復旧、二重writer拒否をWindows native環境で検証していない。
7. Claude向けclient snippetは`export`形式だけで、PowerShellとDesktop起動環境を扱わない。
8. 実Codex / Claude Code E2EはLinux network namespace専用で、Windows上の最終payloadに
   合成機密値がないことを証明するgateがない。
9. one-fileのnative build、`.exe`成果物、temp展開、終了処理、Defender / SmartScreen、
   Authenticode署名、clean-machine testが未実施である。

## 検討した方針

次のような段階的なWindows対応を検討した。

- Python coreが概ねportableであることを根拠に、source版だけを先に対応扱いにする。
- POSIX scriptを手動のPowerShell commandへ読み替えれば使えるものとして案内する。
- Windows分岐が存在するSQLite leaseをnative testなしで利用する。
- ACLは利用者が`icacls`等で設定する前提とし、製品は警告だけを出す。
- 実client E2Eを省略し、unit testとmock protocol testだけで互換性を判断する。
- unsigned one-fileを`experimental`として配布し、起動できた環境だけを事例として扱う。
- WSL上で動くことをWindows native対応の証拠とする。WSL2直接実行とDocker Composeを
  Windows向けLinux-hosted deploymentとして別途検証する方針は
  [ADR-0015](0015-evaluate-windows-linux-hosted-deployments.md)で扱う。

## 判断

上記の方針をすべて却下する。

Windowsは、source版とone-file版のどちらも現行の公開対応範囲へ含めない。Windows向けcode分岐、
手動での起動成功、unit test成功は、Windows上で実際の機密情報を扱えることの根拠にしない。
利用者文書では非対応であることと、実際の機密情報を扱わないことを明示する。

Windows向けの移植作業や合成dataだけを使うtechnical spikeは許容する。ただし、再検討条件を
すべて満たすまでは`done`、supported、experimental support等の対応表現を使用しない。
未検証項目をwarningや利用者の手作業へ移して起動を継続するのではなく、秘密保護を確認できない
場合はfail-closedとする。

## 却下理由

### 1. local file保護がsecurity boundaryの一部である

辞書は原文を含み得る。master keyとSQLiteを同時に取得されると、暗号化したsessionを復号できる。
したがってfile permissionは補助的なhardeningではなく、可逆マスキングのsecurity boundaryである。

Windows対応では、少なくとも次のartifactと親directoryについてDACLの作成と検査が必要になる。

- `securitymasker.config`
- `securitymasker.dict`
- `securitymasker.key`
- `securitymasker.db`
- SQLiteの`-wal`、`-shm`
- `securitymasker.db.lock`
- これらを格納するstate directory

許可するprincipal、継承ACL、owner、network share / removable drive / filesystem差異を定義せず、
POSIX mode bitを指定するだけではprivateであると判断できない。手動設定だけに依存すると、設定漏れを
製品が検出できず、不明・障害時にfail-closedとする不変条件に反する。

### 2. native dependencyとprocess semanticsはunit testで代替できない

標準NERはtorch、transformers、tokenizers、sentencepiece、safetensors、numpy等のnative artifactを
含む。Windows targetとPython versionごとにwheelを固定し、model取得・digest検証・local-only loadを
clean環境で完走させる必要がある。

SQLiteのfile lock、WAL、process終了、one-fileのtemp展開もOS固有である。POSIX上で同じPython testが
成功しても、Windowsのfile sharing、`msvcrt.locking`、`TerminateProcess`、antivirusによるfile走査の
挙動は証明できない。

### 3. client互換性はWindows上の実clientで確認する必要がある

SecurityMaskerはupstream SDKをforkせず、Codex / Claude Codeが実際に送るprotocolへ追従する。
PowerShellで環境変数を設定できるだけでは、Codex appやClaude Code Desktopが同じ設定を読み、
loopback Gatewayだけへ接続することを保証しない。

最終gateでは、外部へrouteできないWindows環境内で実Codex CLIと実Claude Code CLIを動かし、
合成機密値について次を確認する必要がある。

- requestがlocal mock upstreamへ実際に到達する。
- 上流の最終payloadに元の合成機密値がない。
- responseが同じsessionの対応表で復元される。
- 別session、別mode、tool argument、stream分割で値が混ざらない。
- client設定をSecurityMaskerが自動変更しない。

外部接続可能なhostでdummy credentialやproxy設定だけに依存するtestは、誤設定時に実providerへ
送信できるためrelease gateとして採用しない。

### 4. `experimental`表記では不足を補えない

一般的なapplicationでは、未署名binaryや一部未検証の機能をexperimentalとして提供できる場合がある。
本製品では「機密情報がmaskされる」という期待が主要機能そのものであり、保護境界の未検証を
experimentalというlabelへ移してもriskは下がらない。

## 再検討条件

### Windows source版

次をすべて満たしたとき、Windows source版を公開対応範囲へ加える新しいADRを作成する。

1. 対象Windows version、architecture、Python versionを限定して明記する。
2. DACL contractを定義し、`init`、config load、SQLite作成、`doctor`へ同じ実装を接続する。
3. permissive ACL、継承違反、wrong owner、network share等の拒否testと、正しいACLのaccept testを
   Windows native環境で成功させる。
4. PowerShell向けsetup / test setup / release checkを提供し、Windows用dependency lockを固定する。
5. 標準NERのdownload、全artifact digest検証、offline load、推論、改竄拒否をclean環境で確認する。
6. SQLiteの暗号化、restart、backup / restore、wrong key / mode、tamper、二重writer、WAL、
   graceful / forced terminationをnative testする。
7. PowerShell、Codex config、Claude環境変数、Desktop起動方法を文書化し、read-only doctorで検査する。
8. 外部networkを構造的に遮断したWindows環境で、実Codex / Claude Code CLIの漏洩ゼロE2Eを成功させる。
9. source archiveの展開からsetup、init、validate、NER preview、両mode Gateway、停止、再起動までを
   標準user権限のclean machineで成功させる。

一項目でも未確認なら、残りだけを検査してWindows対応を表明しない。

### Windows one-file版

source版の条件に加え、次をすべて満たしたときだけone-file版を別途再検討する。

1. Windows上でnative PyInstaller buildを再現し、`.exe`用build / test / checksum手順を固定する。
2. 同梱modelの再配布条件をownerが確認する。
3. Authenticode署名方針とrelease時の署名・検証手順を確定する。
4. Python未導入の物理または同等のclean machineでbinary E2Eを成功させる。
5. cold / warm起動、NER推論、temp展開、Ctrl+C、強制終了、残存file、Defender / SmartScreenの
   挙動を記録する。

source版が対応済みでも、これらが未完ならone-file版は非対応のままとする。

## 影響

- Windows利用者には、部分的に動く可能性ではなく非対応という保守的な契約を提示する。
- Windows向けcodeを追加しても、それだけではstatusを変更しない。
- Windows移植ではmasking coreやprotocol adapterをforkせず、OS差分をpermission、setup、
  packaging、test harnessの境界へ隔離する。
- Windows対応のために標準NERを無効化した軽量版は作らない。標準保護能力は他platformと同じにする。
- Windows source版の成立とone-file公開を分離し、署名やmodel再配布判断がsource版まで不必要に
  blockしないようにする。
- macOS arm64 / Linux arm64の検証済みsource版の範囲は変更しない。

## 検討した代替案

- **Pythonが動けばsource版を対応扱いにする**：ACL、native dependency、SQLite、実clientの
  security boundaryを検証できないため却下。
- **ACLをwarningだけにする**：秘密保護を確認できない状態で起動を継続し、fail-closedに反するため
  却下。
- **利用者の手動`icacls`だけに依存する**：製品が設定漏れと安全な状態を判別できないため却下。
- **標準NERをWindowsだけ無効化する**：platformによって標準検出能力が変わり、未登録の日本語固有
  表現を保護できないため却下。
- **unit / mock testだけでclient互換を判断する**：最終payload、実clientの設定解釈、stream処理を
  end-to-endで証明できないため却下。
- **外部接続可能なWindows hostで実CLI E2Eを行う**：testの誤設定時に原文を実providerへ送信し得る
  ため却下。
- **unsigned binaryをexperimental配布する**：保護境界の未検証をlabelで補えず、one-file特有の
  展開・終了・antivirus挙動も未確認なため却下。
- **WSLをWindows native対応とみなす**：filesystem permission、process、client設定、Desktop連携が
  Windows nativeと異なり、native利用者の安全性を証明しないため却下。WSL2 / Docker Composeを
  独立したLinux-hosted targetとして比較することは却下対象ではなく、ADR-0015へ分離する。
