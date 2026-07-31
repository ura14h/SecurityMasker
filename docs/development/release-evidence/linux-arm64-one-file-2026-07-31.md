# Linux arm64 one-file technical spike検証記録

実行日: 2026-07-31

この文書は、Linux arm64 one-fileをDocker Desktop上でnative buildし、Pythonを含まない
runtimeで検証したtechnical spikeのevidenceです。公開releaseのbinary gate完了または
Linux arm64 one-fileの対応表明を意味しません。

## 対象

- base commit: `f4b4bdc843d363d95f4e776616b8307bcf16f9f2`
- tested snapshot: 上記commitにLinux arm64 binary gate資材、回帰test、文書変更を加えたtree
- image: `sha256:5ba23beab8c48d106874ce8cfd0fafcc1499e4637d3d82360ab0cf1e2153110c`
- host: macOS arm64 Docker Desktop 27.5.1
- build platform: Debian 12 bookworm、Linux arm64、Python 3.12.13
- clean runtime: Debian 12 bookworm slim、Linux arm64、Pythonなし、非root UID/GID 10001
- PyInstaller: 6.21.0
- Torch: 2.13.0+cpu

固定digestのPython 3.12.13 builderとDebian 12 slim runtime、固定lock、固定NER modelから
`docker/Dockerfile.binary-gate`で構築しました。buildは`Linux-6.12.5-linuxkit-aarch64-with-glibc2.36`
上で実行され、PyInstallerはaarch64 bootloaderを使用しました。

## 結果

| gate | 結果 |
|---|---|
| 固定NER modelの全artifact digest検証 | 成功 |
| native PyInstaller one-file build | 成功 |
| 既存binary E2E | 4件成功 |
| Pythonを含まない最終imageのbuild-time smoke | 成功 |
| `--network none`・read-only root filesystemでのclean runtime smoke | 成功 |
| artifact architecture確認 | ELF 64-bit ARM aarch64 |
| local ruff／mypy strict | 成功／71 source files成功 |
| local unit／evaluation | 689件成功、warning 1件 |

既存binary E2Eは隔離HOME／TMPDIRでhelp、init、config validation、標準NER preview、両modeの
mock Gateway、SQLite永続化、mask／restore、上流原文ゼロ、SIGTERM cleanupを検査しました。

clean runtime smokeは最終imageにPython commandが存在せず、UP状態の非loopback interfaceと
IPv4／IPv6 default routeがないことを確認しました。read-only root filesystemへ書き込み用tmpfsを
加えた条件でhelp、init、config validation、標準日本語NER previewが成功し、合成原文`山田太郎`が
出力から消え、`SM_PERSON_` aliasへ置換されることを確認しました。PyInstaller one-fileは展開した
共有libraryをloadするため、展開先`/tmp`には`exec` mount optionが必要です。

local warningはStarlette TestClientからhttpx2への将来移行を示すdeprecationで、test失敗または
security境界の縮小ではありません。

## Artifact

- path: `dist/securitymasker-linux-arm64`
- size: 1,019,789,720 bytes（約972.5 MiB）
- SHA-256: `06017179b58305b4e9beeefa89fccff9b4339371b2964887f91ef5f632b22934`
- file type: ELF 64-bit LSB executable、ARM aarch64、dynamic linker `/lib/ld-linux-aarch64.so.1`

artifactは検証用local生成物であり、Gitへcommitせず、公開もしません。

## 残件

- model weightとtransitive componentの再配布条件を確認する
- version、source tag、checksum、release noteとの対応を確定する
- 対象distributionとglibc baselineを決め、各clean-machine gateを実行する
- macOS binaryを公開する場合はDeveloper ID署名とnotarizationを行う

したがって、Linux arm64 one-fileの「未検証」はtechnical spikeとして解消しましたが、one-file
binary公開全体は引き続きblockedです。
