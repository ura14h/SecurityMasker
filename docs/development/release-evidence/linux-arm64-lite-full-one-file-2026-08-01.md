# Linux arm64 Lite／Full one-file technical spike検証記録

## 位置付け

この文書は、Docker Desktopのnative arm64 builderでLite版とFull版を別々にbuildし、Pythonを含まない
Linux arm64 runtimeで検証したtechnical spikeのevidenceです。物理clean machineでの検証、署名済み
artifact、Linux distribution全般の互換性、公開releaseのbinary gate完了を意味しません。

## 対象

- date: 2026-08-01
- tested code commit: `e686cfe`
- host: macOS 26.5.2 arm64、Docker Desktop engine 27.5.1 arm64
- builder: `python:3.12.13-slim-bookworm`
- runtime: `debian:bookworm-slim`
- container platform: Linux 6.12.5 linuxkit aarch64、glibc 2.36
- Python: 3.12.13
- PyInstaller: 6.21.0
- model: `tsmatz/xlm-roberta-ner-japanese`
- revision: `aba094e118d5ffc622e9b25e07edc49f9dd85feb`
- binary profile: `lite`、`full`

builderとruntimeのbase imageはDockerfile記載のdigestへ固定しました。実在人物、実際のsecret、
実provider bodyは使用していません。Gateway E2Eはlocal mock upstreamと固定合成PERSONだけを使用し、
生成artifactはlocal `dist/`だけに置いてGitへcommitしていません。

## Build結果

| profile | model配置 | size | SHA-256 |
|---|---|---:|---|
| Lite | binary外の検証済みlocal cache | 248,563,712 bytes（約237 MiB） | `13931b457b9a6e277f6f8aeb18b0678d3ca2fdd520ce997ff4280340c53474f9` |
| Full | one-fileへ固定6 artifactを同梱 | 1,019,794,840 bytes（約972.6 MiB） | `dbe07b8440c30a6155dcea20425a5c7aedffac3187d78d523a02e5037be4f1cc` |

両方ともELF 64-bit LSB、ARM aarch64、GNU/Linux 3.7.0以降向けのone-fileとして生成されました。
`--version`はそれぞれ`securitymasker 0.1.0 (binary lite)`と
`securitymasker 0.1.0 (binary full)`を表示しました。

## Profile別binary integration

builder内で生成直後のbinaryを使い、共通のinit、config validation、標準NER preview、ChatGPT／Claude
両modeのmock Gateway E2E、SQLite永続化、上流原文ゼロ、SIGTERM終了、一時展開directory cleanupを
検査しました。

- Lite: 空cacheでのfail-closedと原文非表示を確認後、binary自身の`model-load`で固定6 artifactを取得・
  SHA-256検証し、`6 passed in 29.93s`
- Full: local cacheへ依存せず同梱modelを使い、Lite専用のmodel不足testだけをskipして
  `5 passed, 1 skipped in 60.47s`

## Python-free runtime smoke

profileごとにPythonを含まないDebian 12 slim imageへbinaryだけを配置し、次の制約で別containerを
起動しました。

- `--platform linux/arm64`
- `--network none`。loopback以外の稼働interfaceとIPv4／IPv6 default routeがないことを構造検査
- `--read-only`。書込み先は`/tmp`と`/work`のtmpfsだけ
- `python3` commandが存在しないことを確認

この環境でhelp、profile付きversion、init、config validation、日本語PERSONの標準NER previewを
両profileとも完了し、preview出力に原文がなく`SM_PERSON_` aliasがあることを確認しました。

## 結論と未完了

Linux arm64のLite／Full native build、profile別model経路、共通binary機能、Python-freeかつoffline・
read-only runtimeのtechnical spikeは成功です。ただし公開には次が残ります。

- 対象distributionまたは公開対象相当の物理clean machine gate
- 公開artifactに対する署名方針
- Lite／Fullへ同梱する全transitive componentのlicense inventoryとNOTICE検証
- Full版model weightの再配布判断
- 公開version、source tag、release note、公開artifact checksumとの対応
