# Testing and release gates

## 利用者setupとtest setup

通常利用者は次だけを使います。

```console
./scripts/setup
```

開発者は別の入口でtest/lint/type依存を追加します。

```console
./scripts/test-setup
```

mock upstream、合成credential、隔離HOME、network namespace、test-only環境変数は
`devtools` と `tests` だけに置き、通常運用手順へ混ぜません。

## source release gate

```console
./scripts/release-check
```

このlocal scriptは次を必須として実行します。

- ruff
- mypy strict
- unit/evaluation（固定NER model必須）
- mock upstreamを使うlive Gateway test
- 実Codex CLIと実Claude Code CLIを使うE2E

実CLI E2EはLinux network namespace内で、外向きinterface/default routeが無いことを構造検査して
から実行します。隔離を証明できないhostでは成功扱いにせず、release gateを失敗させます。
実providerへtest bodyを送りません。

## binary gate

PyInstallerはcross compilerではありません。対象OSごとにnative clean buildします。

```console
PYTHON_COMMAND=python3.12 ./scripts/build-binary
./scripts/test-binary ./dist/securitymasker
```

binary testは隔離HOME/TMPDIRでinit、config validation、標準NER preview、ChatGPT/Claude mock、
SQLite永続化、mask/restore、上流原文ゼロ、SIGTERM cleanupを検査します。

one-file artifact、model weight、build directoryはGitへcommitしません。

## source release artifact

version確定・commit後、clean worktreeからarchiveとchecksumを生成します。

```console
./scripts/package-source
```

`dist/securitymasker-<version>-source.tar.gz` と同名の `.sha256` を生成します。既存artifactは
上書きしません。tagを作る場合は、そのtag名を第1引数として指定できます。

## test data

- 実在人物、実際のsecret/API key/credentialをfixtureへ入れない。
- providerへ合成promptを含めて送らない。
- 最重要assertionは「上流が受けた最終payloadに元の合成機密値が存在しない」こと。
- streamingはaliasの全分割位置、tool argument delta、特殊文字をproperty testする。
- session並行性とresponse bindingでalias混在がないことを検査する。
- reject testだけでなくclean inputが受理されるtestも必ず置く。

## Desktopの扱い

自動gateはCodex CLIとClaude Code CLIをDesktopのprotocol surrogateとして使います。DesktopとCLIが
共有する設定生成元をtestしますが、Desktop UIそのものを自動操作したとは表現しません。
release ownerが可能なら、実アカウントで合成promptだけを使う手動Desktop smoke testを行います。
