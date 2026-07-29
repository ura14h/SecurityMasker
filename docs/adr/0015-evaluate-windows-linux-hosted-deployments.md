# ADR-0015 — Windows向けLinux-hosted deploymentはWSL2とDocker Composeを同一gateで比較する

- 状態：採用（検証方針）
- 日付：2026-07-28
- 関連：[ADR-0012](0012-renew-package-design.md)（現行パッケージ設計）、
  [ADR-0013](0013-reject-best-effort-windows-support.md)（Windows native）、
  [development status](../development/status.md)、
  [compatibility](../reference/compatibility.md)
- 対象：Windows host上でLinux版SecurityMaskerを動かし、WindowsまたはLinux側の
  Codex / Claude Codeから利用する構成

> このADRはWindows対応を追加する判断ではない。Windows nativeを未検証のまま対応扱いにしない
> ADR-0013を維持したうえで、WSL2直接実行とDocker Desktop上のDocker Composeを独立した候補として
> 同じsecurity gateで比較する。どちらもgate完了までは実際の機密情報を扱えるとは表明しない。

## 背景

ADR-0013は、Windows nativeのfile ACL、dependency、SQLite lock、process終了、client設定、
binary build、実CLI E2Eが未完成であるため、部分的な動作を根拠にbest-effort対応を公開する案を
却下した。同時に、WSL上の動作をWindows native対応の証拠とみなす案も却下した。

一方、WSL2とDocker DesktopのLinux containerはWindows native processではない。Linux kernel、
POSIX file permission、Linux向けnative dependency、Linux process signalを利用できるため、
Windows native固有のgapを回避しながら、Windows clientからloopback Gatewayを利用できる可能性が
ある。

Docker Composeも単なる`docker run`の短縮ではない。SecurityMaskerの次の構成を宣言的に固定できる。

- `chatgpt`と`claude`を別service、別process、別portで起動する。
- modeごとにconfig、dictionary、SQLite、master keyを別volumeへ保存する。
- model取得と`init`をone-shot profileへ分離する。
- healthcheck、restart、停止signal、grace periodを固定する。
- image、network、volume、capabilityを一つの検査可能な構成として配布する。

したがって、WSL2を先に採用してDocker Composeを後回しにするのでも、Composeだけを先に選ぶのでも
なく、両者を同じ合格条件で比較する必要がある。

## 候補

### A. WSL2直接実行

Windows userごとのWSL2 distribution内へsource archiveを展開し、現行の`./scripts/setup`で
Linux source環境を作る。

二段階で評価する。

1. Gateway、Codex CLI、Claude Code CLIを同じWSL2 distribution内で実行する。
2. GatewayをWSL2内、clientをWindows側で実行し、Windowsの`localhost` forwarding経由で接続する。

config、dictionary、DB、key、model cacheはWSLのLinux root filesystemに置く。`/mnt/c`等のDrvFS、
SMB、network filesystem、removable driveへ機密artifactを置く構成は対象外とし、検出時は
fail-closedとする。

### B. Docker Desktop + Docker Compose

Docker DesktopのLinux containerを、Compose SpecificationとCompose V2の`docker compose`で管理する。
legacyな独立`docker-compose` V1 binaryは対象にしない。

検証topologyは次とする。

| Compose resource | 契約 |
|---|---|
| `chatgpt` service | 1 process、`chatgpt` mode、loopback port 4000、専用product volume |
| `claude` service | 1 process、`claude` mode、loopback port 4001、専用product volume |
| model setup service | profileで明示実行し、固定modelを取得・digest検証 |
| mode別init service | profileで明示実行し、config、dictionary、keyを一度だけ生成 |
| mode別product volume | config、dictionary、DB、keyを保持し、他modeからmountしない |
| model volume | setup時だけwrite、Gatewayからread-only、両modeで共有可能 |

runtime serviceはnon-root userで動かし、Docker socketをmountせず、`privileged`、追加capability、
host filesystemの機密pathへのbind mountを使用しない。root filesystemはread-onlyとし、書込み先を
mode別named volumeと必要最小限の`tmpfs`へ限定する。

## 判断

WSL2直接実行とDocker Composeを、Windows向けLinux-hosted deploymentの対等なtechnical spikeとして
採用する。実装順や一方の手動起動成功を、他方を検討せず製品採用する理由にしない。

この判断は次を意味する。

1. Windows native source / `.exe`は引き続き非対応とし、ADR-0013を変更しない。
2. WSL2とDocker Composeのspikeには合成dataだけを使用し、通常運用の機密情報を入力しない。
3. masking core、protocol adapter、alias、session formatを候補ごとにforkしない。
4. WSL2とComposeへ同じ漏洩ゼロ、session分離、構造保持、fail-closed gateを適用する。
5. 一方だけがgateを満たした場合は、その方式だけを後続ADRで採用できる。
6. 両方が独立してgateを満たし、保守costが許容できる場合は両方を採用できる。
7. どちらも満たさない場合は、Windows向けLinux-hosted deploymentも非対応のままとする。

ADR-0012がDocker / Docker Composeを標準製品範囲から外した判断は、検証中は維持する。Composeを
利用者向けruntimeとして採用する場合だけ、後続ADRでADR-0012の適用範囲を
「通常のmacOS / Linux source経路では不採用、Windows向けLinux-hosted経路では採用」のように
明示的に変更する。technical spikeの存在だけで標準製品範囲へ戻さない。

## network設計

### WSL2

最初のtargetはWindows 11 x64、WSL2、既定NAT、IPv4 localhost forwardingに限定する。
Gatewayは現行どおり`127.0.0.1`だけへbindする。WSL distributionのIPへの直接接続、
`0.0.0.0` bind、`netsh interface portproxy`、LAN公開は使用しない。

mirrored networkingは別matrixとする。採用する場合は、Windows host、WSL、Hyper-V firewallを含め、
Windows localhostから到達し、LAN上の別hostから到達しないことをnative testする。

### Docker Compose

最初のtargetはDocker Desktop 4.34以降で明示的に有効化したhost networkingとする。各Gateway
serviceへ`network_mode: host`を指定し、SecurityMasker自身は現行どおり`127.0.0.1`へbindする。
`ports`は併用しない。

通常のbridge networkと次のpublishだけを使う案は、最初のspikeでは採用しない。

```yaml
ports:
  - "127.0.0.1:4000:4000"
```

この構成ではcontainer内の別network namespaceへ転送するため、SecurityMaskerを`0.0.0.0`または
container interfaceへbindする変更が必要になる。application自身はhost側publishが本当に
loopback限定か検証できず、public bindを許可しない現行契約を弱めるためである。

Docker Desktopのhost networkingはEnhanced Container Isolationと併用できない。このtrade-offを
残存riskとして記録し、non-root、capability削除、read-only root、Docker socket非mount、
固定image dependencyで補えるかをspikeで評価する。解決できない場合はCompose案を不採用とする。

## storage設計

### WSL2

- 機密artifactはWSLのLinux root filesystemだけへ置く。
- DrvFS上でLinux metadataを有効にして`0600`に見せる方法も採用しない。Windows processがLinuxの
  mode bitとは別のACLで読める可能性を製品が検査できないためである。
- Windowsから`\\wsl$`経由で編集したfileが安全なmodeを失った場合は、既存のpermission検査で拒否する。
- backupをWindows filesystemへ出す場合は別のWindows ACL契約が必要になるため、最初のspikeでは
  Linux filesystem内のbackup / restoreだけを合格対象とする。

### Docker Compose

- modeごとのproduct dataを別named volumeへ保存し、DB/keyを共有しない。
- Windows filesystemからconfig、dictionary、key、DBをbind mountしない。
- Compose YAML、`.env`、image layer、environment variableへmaster keyやdictionary値を入れない。
- `docker compose down`ではvolumeを残し、`down -v`がDB/keyを破壊する操作であることを明示する。
- dictionaryの安全なimport / export、config変更、DB/key pair backup / restoreを、秘密値を
  stdout、log、image layerへ出さない専用workflowとして設計する。
- volumeのowner / mode不一致、mode取り違え、wrong key、同じvolumeの二重writerを拒否する。

named volumeへ置けば安全と無条件に仮定しない。Docker Desktop user、他container、backup artifact、
volume exportの境界をthreat modelへ追加し、同一Windows user権限での侵害を対象外とする現行契約との
整合を確認する。

## Compose lifecycle

canonical Compose構成で次を固定する。

- setup / init serviceはprofile付きone-shot serviceとし、通常の`docker compose up`で起動しない。
- Gateway serviceは`init: true`、`SIGTERM`、十分な`stop_grace_period`を使用する。
- `/ready`をhealthcheckとし、NER、config、DB/keyが利用可能になる前にhealthyとしない。
- restart policyを明示し、Windows reboot、Docker Desktop restart、container再作成後の挙動をtestする。
- `chatgpt`と`claude`のport、volume、modeを固定し、combined modeや同じDB/keyを許可しない。
- resource limitを設定し、標準NERを二processで動かすmemory / disk costを実測する。
- `docker compose config`の解決結果をrelease artifactとして検査し、未知fieldのwarningを
  成功扱いにしない。

## 共通の合格条件

各候補は、独立して次をすべて満たさなければsupported候補にしない。

1. 対象Windows、WSL / Docker Desktop、Linux architecture、Python / Compose versionを固定する。
2. clean machineでsetup、init、config validation、標準NER、両mode Gatewayを完走する。
3. 固定NERのdownload、全artifact digest検証、offline load、推論、欠落・改竄拒否を確認する。
4. config、dictionary、DB、key、model cacheのpermission / mount契約をaccept / reject両方向でtestする。
5. Windows clientからGatewayのloopback portへ到達し、LAN上の別hostから到達しない。
6. 外部networkを構造的に遮断した環境で実Codex CLI / Claude Code CLIを動かし、local mockへ届いた
   最終payloadに元の合成機密値がない。
7. buffered / streaming、tool argument delta、特殊文字、全alias分割位置でmask / restoreを確認する。
8. session、response binding、mode、DB/key、同時requestでaliasや原文が混ざらない。
9. process / container / WSL restart、graceful stop、forced stop後もfail-closedで再開する。
10. backup / restore、wrong pair、tamper、二重writer、DB障害を検証する。
11. Windows用Codex設定、PowerShell用Claude環境設定、read-only doctorの検査経路を提供する。
12. log、error、healthcheck、Compose output、`docker inspect`、backup操作へ原文・鍵・対応表を出さない。
13. setupやclient設定を利用者の既存fileへ自動適用しない。

一方の方式が成功しても、もう一方の未検証項目を成功扱いにしない。

## 比較基準

security gateを満たした候補だけを、次の運用costで比較する。

| 観点 | WSL2直接実行 | Docker Compose |
|---|---|---|
| 利用者前提 | WSL distribution、Linux shell、Python | Docker Desktop、Compose V2 |
| dependency再現性 | distribution / Pythonの影響を受ける | imageで固定しやすい |
| 2 mode運用 | 利用者が2 processを管理 | 2 serviceを一括管理 |
| file保護 | Linux filesystemとPOSIX mode | named volumeとcontainer user |
| client連携 | WSL localhost forwarding | Docker Desktop host networking |
| setupの見通し | 現行scriptを再利用しやすい | image buildとsetup profileが必要 |
| config / dictionary編集 | Linux fileを直接編集可能 | 安全なimport / export workflowが必要 |
| backup / restore | 現行pair運用を再利用しやすい | volume向け専用workflowが必要 |
| 追加attack surface | WSL integration | Docker daemon、image、Compose、host networking |
| 通常停止 / 再起動 | Linux processとWSL lifecycle | Compose lifecycleで宣言可能 |

操作数の少なさだけで選ばず、permission、network、backup、failure時の安全性を優先する。

## 影響

- Windows nativeを実装せず、Linux runtimeを使う現実的な候補を検証できる。
- Composeを1 process / 1 modeの原則と両立する形で評価できる。
- Windows対応のtest matrixと保守対象は増える。採用方式ごとのversion更新時にnative gateが必要になる。
- Compose案ではDockerfile、Compose、image supply chain、volume運用を再導入する可能性がある。
- WSL2案ではfilesystem種別検査、Windows client設定、host / guest横断E2Eが必要になる。
- standard NERを無効化した軽量Windows経路は作らない。
- technical spike中は現行のmacOS / Linux source release判断を変更しない。

## 検討した代替案

- **WSL2だけを先に採用する**：Composeによるdependency固定、2 mode管理、named volumeの利点を
  同じgateで比較できないため却下。
- **Docker Composeだけを先に採用する**：現行source scriptを再利用できるWSL2の単純さと、
  Docker daemonを追加しない利点を比較できないため却下。
- **Composeの`ports`に合わせてcontainer内public bindを許可する**：host側の公開範囲を
  applicationが検証できず、loopback-only契約を弱めるため最初の候補から除外する。
- **Windows directoryをbind mountする**：Windows ACL / DrvFSの未検証問題をcontainer内へ
  持ち込むため却下。
- **DB/keyを同じvolumeで両modeへ共有する**：mode分離とsingle-writer契約に反するため却下。
- **master keyをCompose secret、environment、YAMLへ移す**：現行のDB/key pair contractを変え、
  inspect、process environment、host fileへ新しい漏洩経路を作るため却下。
- **標準NERを外してimageを軽量化する**：標準保護能力をplatformによって下げるため却下。
- **一方の起動成功だけでexperimental対応とする**：漏洩ゼロとfailure gateを完了せず対応表明する
  ADR-0013の却下対象を再導入するため却下。

## 参照

- [Microsoft: Accessing network applications with WSL](https://learn.microsoft.com/en-us/windows/wsl/networking)
- [Microsoft: File Permissions for WSL](https://learn.microsoft.com/en-us/windows/wsl/file-permissions)
- [Docker: Compose Specification](https://docs.docker.com/reference/compose-file/)
- [Docker: Services top-level element](https://docs.docker.com/reference/compose-file/services/)
- [Docker: Using profiles with Compose](https://docs.docker.com/compose/how-tos/profiles/)
- [Docker: Volumes in Compose](https://docs.docker.com/reference/compose-file/volumes/)
- [Docker: Host network driver](https://docs.docker.com/engine/network/drivers/host/)
- [Docker: Port publishing and mapping](https://docs.docker.com/engine/network/port-publishing/)
