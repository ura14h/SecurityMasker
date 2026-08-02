# 私的なone-file binaryをbuildする

Lite版とFull版は技術検証用です。公開artifactではなく、署名、全dependencyの再配布確認、対象OS別の
clean-machine gateも完了していません。この手順で作ったbinaryを第三者へ配布しないでください。

## Profileを選ぶ

| profile | model | 初回準備 | 適する用途 |
|---|---|---|---|
| Lite | binaryへ同梱しない | 利用端末で`model-load` | 通常の私的運用、SecurityMaskerとmodelの独立更新 |
| Full | 固定modelを同梱 | build時に自動取得 | networkのない端末へ単一fileを私的搬入 |

Liteも検出機能を減らした版ではありません。modelをlocal cacheへ置く点だけがFullと異なり、取得後は
同じ日本語NER、detector、Gatewayをofflineで使用します。modelがない場合に決定論的detectorだけへ
downgradeせず、起動を拒否します。

## 必要条件

- macOS／Linuxでは対象OS上のPython 3.11以上とPOSIX shell、Windowsでは64-bit CPython 3.12とcmd.exe
- `venv`を作成できる環境
- build dependencyとmodelを取得するnetwork接続
- Liteはbuildと利用cache、Fullはbuildとone-file作成に十分な空き容量
- macOS／Linux arm64とWindows 11 x64以外は未検証

PyInstallerはcross compilerではありません。利用するOS／architecture上でnative buildします。

## Lite版

repository rootでbuildします。

```console
PYTHON_COMMAND=python3.12 ./scripts/build-binary-lite
```

成果物は`dist/securitymasker-lite`です。利用端末で初期化し、固定modelを明示的に準備します。

```console
./securitymasker-lite --version
./securitymasker-lite init --directory /path/to/product
./securitymasker-lite model-load \
  --config /path/to/product/securitymasker.config
./securitymasker-lite doctor \
  --config /path/to/product/securitymasker.config
./securitymasker-lite preview "担当者は山田太郎です。" \
  --config /path/to/product/securitymasker.config
```

`model-load`だけがmodel配布元へ接続します。取得した全artifactは固定manifestのsizeとSHA-256で検証し、
次回以降のpreview／Gatewayはlocal cacheだけを読みます。cacheを移動・削除した場合は再度`model-load`を
実行します。実際の機密値を接続確認へ使用しないでください。

buildしたartifactをprofile別integration testへ通します。

```console
./scripts/test-binary --profile lite ./dist/securitymasker-lite
```

このgate自身もbinaryの`model-load`を実行します。既定のtest cacheを変更する場合だけ、機密情報を
含まない専用directoryを`SM_BINARY_TEST_HF_HOME`で指定します。

## Full版

repository rootでbuildします。

```console
PYTHON_COMMAND=python3.12 ./scripts/build-binary-full
```

成果物は`dist/securitymasker-full`です。build時に固定modelを取得・検証して同梱するため、利用端末で
`model-load`は不要です。

```console
./securitymasker-full --version
./securitymasker-full init --directory /path/to/product
./securitymasker-full preview "担当者は山田太郎です。" \
  --config /path/to/product/securitymasker.config
```

profile別integration testは次です。

```console
./scripts/test-binary --profile full ./dist/securitymasker-full
```

## 共通build interface

wrapperの正は一つの共通scriptです。自動化では次を直接使用できます。

```console
./scripts/build-binary --profile lite
./scripts/build-binary --profile full
```

build directoryまたは同名artifactが存在する場合は上書きせず終了します。既存artifactが必要なら、
先に別の明示した保管先へ移動してください。profile不明の`dist/securitymasker`は生成しません。

`--version`は取り違え防止のため、次のいずれかを表示します。

```text
securitymasker 0.1.0 (binary lite)
securitymasker 0.1.0 (binary full)
```

## Windows x64

Windowsでは`scripts\test-setup.cmd`を先に完了し、cleanなbuild／dist directoryから次を実行します。

```bat
scripts\build-binary-lite.cmd
scripts\test-binary.cmd --profile lite
scripts\build-binary-full.cmd
scripts\test-binary.cmd --profile full
```

両profileを順にbuild・testする短いrunnerもあります。

```bat
scripts\windows-binary-gate.cmd
```

buildは`requirements-windows.lock`と`requirements-windows-build.lock`のwheelだけを使用し、Visual
Studioやsource buildへfallbackしません。別のPython 3.12 x64を使う場合だけ、その絶対pathを
`SECURITYMASKER_PYTHON`へ設定します。成果物は`dist\securitymasker-lite.exe`と
`dist\securitymasker-full.exe`です。

Windows one-file版はtechnical spikeであり、署名、clean-machine gate、再配布確認が終わるまで
第三者へ配布しません。検証結果は
[Windows x64 Lite／Full evidence](release-evidence/windows-x64-lite-full-one-file-2026-08-02.md)に
記録しています。

## Linux arm64 Docker gate

macOS arm64のDocker DesktopからLinux arm64の両profileをnative build・検査する開発者用runnerは次です。

```console
./devtools/run_linux_arm64_binary_gate.sh
```

同じ固定Dockerfileをprofile別にbuildし、PythonのないDebian 12 slim runtimeをread-onlyかつ
`--network none`で起動します。成果物は`dist/securitymasker-linux-arm64-lite`と
`dist/securitymasker-linux-arm64-full`です。このcontainer検証だけで物理clean machineや他のLinux
distributionへの対応を表明しません。

公開判断と記録項目は[Release gate](release.md)、現在のblockerは
[開発・リリース状況](status.md)、profile分離の理由は
[ADR-0020](../adr/0020-split-lite-and-full-binary-profiles.md)を参照してください。
