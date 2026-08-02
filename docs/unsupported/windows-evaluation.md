# Windows上のLinux環境で評価する（非対応）

Windows native source版は限定条件で対応しています。通常は
[Windows native source版の導入手順](../guides/windows-native-source.md)を使用してください。この番外編は、
Windows 11 x64上のWSL2またはDocker DesktopのLinux containerで、Linux版SecurityMaskerを評価する
ためのtechnical spikeです。
[ADR-0015](../adr/0015-evaluate-windows-linux-hosted-deployments.md)の共通gateは未完了であり、
どちらも対応環境ではありません。

> **評価版の免責**
>
> この手順と`docker/`のartifactは現状有姿で提供され、機密性、完全性、可用性、特定目的への
> 適合を保証しません。実データの投入は利用者自身の判断と責任で行ってください。未検出の値が
> 外部LLMへ送信されること、設定・辞書・DB・key・backupがWindows hostや他containerから
> 読み取られること、更新や障害で復元不能になることを含むriskがあります。
>
> 提供するvolume、network、user、capability、read-only設定を変更した構成も検証対象外です。
> とくにWindows directoryをbind mountしてconfig、辞書、DB、keyを外部編集可能にした場合、
> Windows ACLとcontainer内のPOSIX permission、編集のatomicity、DB/key pair、mode分離を
> SecurityMaskerは保証できません。免責の正式な条件はrepositoryの[LICENSE](../../LICENSE)に
> 従います。

最初の疎通確認は、実在人物や実際のsecretではなく、既定辞書の`山田太郎`等の合成値で行うことを
推奨します。実データへ進むかどうかにかかわらず、重要語は辞書へ登録し、local `preview`で
検出を確認してください。

## 方法1 — WSL2で直接実行する

この方法は、SecurityMasker、Python、NER model、config、DB、keyを一つのWSL2 distribution内へ
置きます。まずCodex CLIまたはClaude Code CLIも同じWSL2内で動かす構成を確認し、その後に必要なら
Windows側clientから`localhost`で接続します。

### 1. WSL2を準備する

Microsoftの[WSL導入手順](https://learn.microsoft.com/en-us/windows/wsl/install)に従い、
管理者権限のPowerShellでUbuntuを導入し、再起動後にLinux userを作成します。

```powershell
wsl --install -d Ubuntu-24.04
wsl --update
wsl --set-default-version 2
wsl --list --verbose
```

`VERSION`が`2`であることを確認します。以降のLinux commandはUbuntu terminal内で実行します。

```console
sudo apt update
sudo apt install --yes git python3 python3-venv
python3 --version
```

Python 3.11以上が必要です。

### 2. Linux filesystemへ展開する

repositoryと製品dataは`/home/<user>`配下へ置きます。`/mnt/c`、`/mnt/d`、SMB、network drive、
removable driveは使用しません。

```console
mkdir -p ~/src
cd ~/src
```

公開repositoryの `Code` メニューから得たURLでWSL内へcloneするか、Releaseのsource archiveを
`~/src/SecurityMasker`へ展開します。Windows側で展開して`/mnt/c`からコピーする方法は使いません。

```console
cd SecurityMasker
./scripts/setup
. .venv/bin/activate
```

この後の`init`、辞書編集、`preview`、Gateway起動は通常の
[導入ガイド](../getting-started.md)と同じです。

Codex用:

```console
python3 securitymasker.py init --mode chatgpt --port 4000
python3 securitymasker.py preview "担当者: 山田太郎"
python3 securitymasker.py gateway
```

Claude Code用:

```console
python3 securitymasker.py init --mode claude --port 4001
python3 securitymasker.py preview "担当者: 山田太郎"
python3 securitymasker.py gateway
```

両方を使う場合は、[CodexとClaude Codeを同時に使う](../guides/use-both-clients.md)に従い、
別directory、別config、別state、別port、別processにします。

### 3. clientを接続する

最初はclientも同じWSL2内で起動し、`client-config`の出力を手動で反映します。

```console
python3 securitymasker.py client-config
python3 securitymasker.py doctor
python3 securitymasker.py doctor --require-ready
```

Windows側のCodexまたはClaude Codeから接続する場合は、Microsoftの
[WSL networking](https://learn.microsoft.com/en-us/windows/wsl/networking)に記載された
既定NATとlocalhost forwardingを
使い、base URLをCodexでは`http://127.0.0.1:4000`、Claudeでは
`http://127.0.0.1:4001`にします。WSL distributionのIP、`0.0.0.0` bind、
`netsh interface portproxy`、LAN公開は使用しません。

Windows Firewallや企業policyによりlocalhost forwardingが使えない場合は、公開範囲を広げて
回避せず、WSL内clientへ戻してください。

### WSL2の注意事項

- `\\wsl$`からLinux filesystemを開くWindows processは、既定WSL user相当の権限でfileへ
  到達できます。同じWindows user権限での侵害から秘密を隔離する機構ではありません。
- `securitymasker.config`、`securitymasker.dict`、state、model cacheをWindows editorで直接
  変更しないでください。必要な編集はWSL terminal内で行います。
- Windows filesystemへのbackupには別のACL契約が必要です。この評価手順ではWSL Linux
  filesystem内にDB/key pairを揃えて保存します。
- WSL update、distribution update、Windows update後は、合成値による`preview`、`doctor`、
  client疎通を再確認します。

## 方法2 — Docker DesktopとDocker Composeで実行する

Codex用とClaude Code用は別のCompose projectです。

| mode | Compose file | port | product/model volume |
|---|---|---:|---|
| Codex | `docker/compose.chatgpt.yaml` | 4000 | Codex専用 |
| Claude Code | `docker/compose.claude.yaml` | 4001 | Claude専用 |

各projectはmodel取得、mode初期化、Gatewayを別serviceにし、通常起動ではone-shot setup serviceを
開始しません。2つのmode間でconfig、辞書、DB、key、model volumeを共有しません。

### 1. Docker Desktopを準備する

[Docker Desktopのhost networking](https://docs.docker.com/engine/network/drivers/host/#docker-desktop)
を利用できるDocker Desktop 4.34以降を導入し、次を確認します。

1. WSL2 backendとLinux containersを使用する。
2. SettingsのResources > Networkでhost networkingを有効にする。
3. [Enhanced Container Isolation](https://docs.docker.com/enterprise/security/hardened-desktop/enhanced-container-isolation/)
   が無効であることを確認する。host networkingとは併用できません。
4. Docker Desktopへ少なくとも8 GiB、4 CPU、modelとimage用の十分なdiskを割り当てる。
5. `docker compose version`がCompose V2を表示する。`docker-compose` V1は使用しない。

Enhanced Container Isolationを組織policyで無効化できない場合は、このCompose方式を使用せず
WSL2方式を評価してください。

### 2. Codex用を初期化する

PowerShellでrepository rootへ移動して実行します。

```powershell
docker compose -f docker/compose.chatgpt.yaml config
docker compose -f docker/compose.chatgpt.yaml build
docker compose -f docker/compose.chatgpt.yaml --profile setup run --rm model-setup
docker compose -f docker/compose.chatgpt.yaml --profile setup run --rm init
docker compose -f docker/compose.chatgpt.yaml up --detach gateway
docker compose -f docker/compose.chatgpt.yaml ps
```

`model-setup`は固定revisionのNER modelを取得し、全artifact digestを検証します。`init`は
config、合成辞書、state directory、master keyをnamed volumeへ一度だけ作成します。同じ
volumeへ`init`を再実行すると上書きせず失敗します。

readinessとclient設定を確認します。

```powershell
docker compose -f docker/compose.chatgpt.yaml exec gateway securitymasker doctor --config /var/lib/securitymasker-product/securitymasker.config --require-ready
docker compose -f docker/compose.chatgpt.yaml exec gateway securitymasker client-config --config /var/lib/securitymasker-product/securitymasker.config
```

表示されたCodex設定をWindows側の`config.toml`へ手動で反映します。SecurityMaskerは既存設定を
変更しません。

### 3. Claude Code用を初期化する

```powershell
docker compose -f docker/compose.claude.yaml config
docker compose -f docker/compose.claude.yaml build
docker compose -f docker/compose.claude.yaml --profile setup run --rm model-setup
docker compose -f docker/compose.claude.yaml --profile setup run --rm init
docker compose -f docker/compose.claude.yaml up --detach gateway
docker compose -f docker/compose.claude.yaml ps
```

readinessを確認します。

```powershell
docker compose -f docker/compose.claude.yaml exec gateway securitymasker doctor --config /var/lib/securitymasker-product/securitymasker.config --require-ready
```

Claude Codeを起動するPowerShellでbase URLを設定します。既存の永続環境を自動変更しないよう、
最初はそのterminalだけへ設定します。

```powershell
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:4001"
```

### 4. 停止・再開・削除

Codex用の例:

```powershell
docker compose -f docker/compose.chatgpt.yaml stop gateway
docker compose -f docker/compose.chatgpt.yaml start gateway
docker compose -f docker/compose.chatgpt.yaml down
```

`down`はcontainerとnetworkを削除しますがnamed volumeを残します。`down --volumes`または
`down -v`はconfig、辞書、DB、key、modelを破壊するため、復旧不能になってよい場合以外は
実行しません。

### Docker Composeの注意事項

- Gatewayは`network_mode: host`で動き、application自身は`127.0.0.1`だけへbindします。
  containerはhostのnetwork stackとhost上のportへ到達できるため、この追加attack surfaceも
  評価対象です。`ports`追加や`0.0.0.0` bindへの変更は行いません。
- imageは固定digestのPython baseと固定lockからbuildします。build時と`model-setup`時だけ
  package registryおよびmodel registryへ接続します。
- runtimeはnon-root、capability全削除、read-only root filesystem、no-new-privilegesで動き、
  Docker socketやWindows filesystemをmountしません。
- named volumeは同じWindows userが管理するDocker Desktopから操作できます。host compromise、
  Docker daemon権限の濫用、別containerからの意図的なmountを防ぐ境界ではありません。
- 辞書の安全なimport/exportとDB/key pairのbackup/restore workflowは未実装です。volumeを
  bind mountへ変更したり、`docker cp`等で外部編集・backupした場合は、冒頭の免責対象です。
- `docker compose config`、log、`docker inspect`、shell historyへsecretを置かないでください。
  master keyや辞書値をYAML、`.env`、environment variable、image layerへ入れません。
- Docker Desktop、Compose、base image、dependencyを更新した後は別の未検証matrixになります。

## 評価結果を扱う

起動成功だけでこのLinux-hosted経路を対応済みとは判断しません。少なくとも
[ADR-0015の共通の合格条件](../adr/0015-evaluate-windows-linux-hosted-deployments.md#共通の合格条件)
に沿って、
loopback限定、LANからの到達不能、実CLI経路、streaming、tool call、session分離、restart、
DB/key pair、log非漏洩を独立して確認する必要があります。

不具合報告には実データ、辞書内容、key、DB、認証headerを添付せず、`doctor --json`と合成値で
再現できる最小手順だけを使用してください。
