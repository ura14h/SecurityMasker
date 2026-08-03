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
- 実Claude Code CLIとAnthropic実サーバを使うMessages単一turn E2E
- 実Codex CLIと実Claude Code CLIを使うE2E

実OpenAI E2Eは外部送信とモデル利用を伴い、固定した合成値だけを送ります。既存のCodex認証を
表示・複製せず、一時overrideで一つのturnに標準8回のdynamic tool callを実行します。
WebSocket接続数1、各requestのmask、response復元、alias非残存を確認し、同一tool chainの
HTTP比較でwall timeと差を記録します。release担当者は実行前に外部送信を認識し、Codexへ
login済みである必要があります。接続数と完了response数を数えるtest専用一時configは
`logging.level: DEBUG`を使い、利用者の通常configは変更しません。

実Anthropic E2Eも固定した合成PERSONだけを送ります。実Claude Codeの既存認証をCLI自身に
使わせ、一時product layoutと空の作業directoryで単一turnを実行します。requestの送信前mask、
Anthropic SSEの完走、最終responseの復元、alias非残存を確認します。通常のClaude設定とsession
履歴は変更せず、built-in tool、telemetry、update、error reportingを無効にします。release担当者は
Claude Codeへlogin済みである必要があります。

test専用stdio MCP probeを直列に呼ぶMessages tool chainは、Claude Code自身のMCP初期化・tool公開も
同時に検査する拡張互換性試験です。`SM_RUN_ANTHROPIC_MCP_E2E=1`で明示的に実行しますが、Claude
Code versionやplatform固有のMCP挙動をSecurityMasker 1.0.0 source版の公開blockerにはしません。
成功時は追加evidenceとして記録し、失敗時も単一turn必須gateの結果と混同しません。

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

Windowsの標準cmd.exeでは同じ契約のcmd版を実行します。

```bat
scripts\package-source.cmd
```

別のPythonを使う場合は、実行前に`SECURITYMASKER_PYTHON`へその実行ファイルの絶対pathを
設定します。未指定時はrepositoryの`.venv`を使います。

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
PYTHON_COMMAND=python3.12 ./scripts/build-binary --profile lite
./scripts/test-binary --profile lite ./dist/securitymasker-lite

PYTHON_COMMAND=python3.12 ./scripts/build-binary --profile full
./scripts/test-binary --profile full ./dist/securitymasker-full
```

`scripts/build-binary-lite`と`scripts/build-binary-full`は同じ共通buildへprofileを渡す短いwrapperです。
Lite版はmodelを同梱せず、binary自身の`model-load`でlocal cacheを準備します。Full版はbuild時に同じ
固定modelを取得・検証してone-fileへ同梱します。成果物の`--version`が指定profileと一致しなければ
成功扱いにしません。

binary testは隔離HOME/TMPDIRでinit、config validation、標準NER preview、両modeのmock Gateway、
SQLite永続化、mask/restore、上流原文ゼロ、SIGTERM cleanupを検査します。Lite版では空のmodel cacheで
previewがfail-closedになること、`model-load`後は同じtestが成功することも検査します。

公開にはさらに署名、対象OSごとのclean-machine gate、同梱componentの再配布条件確認が必要です。
Full版にはmodel weightの再配布条件確認も必要です。one-file artifact、model weight、build directoryは
Gitへcommitしません。

Windows 11 x64では、64-bit CPython 3.12と固定Windows lockから両profileをnative buildし、同じ
binary integrationを実行します。

```bat
scripts\windows-binary-gate.cmd
```

このrunnerの成功だけでは公開可能としません。新しいstandard userまたは同等のclean Windows環境、
Authenticode署名、署名後artifactの再検証、再配布条件の確認を別途必要とします。

macOS arm64のDocker DesktopでLinux arm64 one-fileをnative buildし、Pythonを含まないclean
runtimeまで検証する場合は次を実行します。

```console
./devtools/run_linux_arm64_binary_gate.sh
```

このrunnerは同じDockerfileをLite／Fullそれぞれにnative buildし、profile別binary E2Eをbuilder
stageで実行します。Lite版のmodelはbinary外のlocal cacheとして最終test imageへ渡し、Full版は
one-file内のmodelを使用します。最終imageをread-only・`--network none`で起動し、Pythonが存在しない
こと、profile表示、init、config validation、標準NER previewを確認します。検証済みartifactを
`dist/securitymasker-linux-arm64-lite`と`dist/securitymasker-linux-arm64-full`へ取り出し、sizeと
SHA-256を表示します。

## Evidenceの記録

公開versionごとに`release-evidence/<version>.md`を作成し、次を記録します。

- 実行日と対象commit
- OS、architecture、Python、client version
- 実OpenAI WebSocket反復E2Eのtool call数、接続数、完了response数
- 同じtool chainのWebSocket／HTTP wall timeと差（promptや認証情報は記録しない）
- 実Anthropic Messages単一turn E2Eのturn数、wall time、完了response数
- MCP拡張互換性試験を実行した場合はtool call数と結果（必須gateとは分離）
- 実行したgateと結果
- test件数
- artifact名、size、checksum
- 未実施項目がないこと

`1.0.0.md`は、1.0.0の全gateが成功してから作成します。release candidateの結果を正式版のevidenceへ
流用しません。
