# Release gate

この文書はsource版とbinary版を公開可能と判断するための合格条件を定めます。過去の実行結果は
`release-evidence/`、現在のblockerは[開発・リリース状況](status.md)に記録します。

## Source release gate

```console
./scripts/release-check
```

このlocal scriptは次を必須として実行します。

- ruff
- mypy strict
- 固定NER model必須のunit/evaluation
- mock upstreamを使うlive Gateway test
- 実Codex app-serverとOpenAI実サーバを使うWebSocket反復E2EとHTTP性能比較
- 実Codex CLIと実Claude Code CLIを使うE2E

実OpenAI E2Eは外部送信とモデル利用を伴い、固定した合成値だけを送ります。既存のCodex認証を
表示・複製せず、一時overrideで一つのturnに標準8回のdynamic tool callを実行します。
WebSocket接続数1、各requestのmask、response復元、alias非残存を確認し、同一tool chainの
HTTP比較でwall timeと差を記録します。release担当者は実行前に外部送信を認識し、Codexへ
login済みである必要があります。接続数と完了response数を数えるtest専用一時configは
`logging.level: DEBUG`を使い、利用者の通常configは変更しません。

その後のlocal mock実CLI E2EはLinux network namespace内で、外向きinterfaceとdefault routeが
ないことを構造検査してから実行します。隔離を証明できないhostでは成功扱いにせず、
release gateを失敗させます。この隔離gateは実providerへbodyを送りません。

containerで代用する場合も、test setupとCLI取得をnetwork有効時に済ませた後、test process全体を
`--network none`で起動します。IFF_UPな非loopback interfaceまたはdefault routeが一つでもあれば
fail-closedとします。

macOS arm64のDocker DesktopでLinux arm64 gateを代替する場合は、hostのCodexへlogin済みである
ことと、実OpenAIへ合成値を送ることを確認してから次を実行します。

```console
./devtools/run_linux_arm64_release_gate.sh
```

この検証専用imageは、固定したPython 3.12、Linux版Codex CLI／Claude Code CLI、dependency、NER
modelをnetwork有効時に構築します。online source gateにはhostのCodex認証fileだけをread-onlyで
mountし、imageへ含めません。その後、同じimageを`--network none`で別起動し、local mockを使う
実CLI E2Eを実行します。このDocker資材は製品runtimeまたは公開binaryの対応範囲を広げません。

## Source release artifact

version確定・commit後、clean worktreeからarchiveとchecksumを生成します。

```console
./scripts/package-source
```

`dist/securitymasker-<version>-source.tar.gz`と同名の`.sha256`を生成します。既存artifactは
上書きしません。tagを作る場合は、そのtag名を第1引数として指定できます。

次を確認します。

- cleanな展開先からsetup、init、validate、NER preview、client config生成が成功する
- 別clean worktreeから生成したarchiveがbyte-for-byte一致する
- archiveへmodel weight、state、key、test-only assetが混入しない
- version、checksum、release note、source tagが対応する

## Binary gate

PyInstallerはcross compilerではありません。対象OSごとにnative clean buildします。

```console
PYTHON_COMMAND=python3.12 ./scripts/build-binary
./scripts/test-binary ./dist/securitymasker
```

binary testは隔離HOME/TMPDIRでinit、config validation、標準NER preview、両modeのmock Gateway、
SQLite永続化、mask/restore、上流原文ゼロ、SIGTERM cleanupを検査します。

公開にはさらに署名、対象OSごとのclean-machine gate、同梱componentとmodel weightの再配布条件確認が
必要です。one-file artifact、model weight、build directoryはGitへcommitしません。

macOS arm64のDocker DesktopでLinux arm64 one-fileをnative buildし、Pythonを含まないclean
runtimeまで検証する場合は次を実行します。

```console
./devtools/run_linux_arm64_binary_gate.sh
```

このrunnerはPyInstaller buildと既存binary E2Eをbuilder stageで実行した後、one-fileだけを
Debian 12 slim stageへコピーします。最終imageをread-only・`--network none`で起動し、Pythonが
存在しないこと、init、config validation、標準NER previewを確認してから、検証済みartifactを
`dist/securitymasker-linux-arm64`へ取り出してsizeとSHA-256を表示します。

## Evidenceの記録

公開versionごとに`release-evidence/<version>.md`を作成し、次を記録します。

- 実行日と対象commit
- OS、architecture、Python、client version
- 実OpenAI WebSocket反復E2Eのtool call数、接続数、完了response数
- 同じtool chainのWebSocket／HTTP wall timeと差（promptや認証情報は記録しない）
- 実行したgateと結果
- test件数
- artifact名、size、checksum
- 未実施項目がないこと

`1.0.0.md`は、1.0.0の全gateが成功してから作成します。release candidateの結果を正式版のevidenceへ
流用しません。
