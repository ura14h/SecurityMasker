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

Docker等のLinux containerで代用する場合も、test setupとCLI取得をnetwork有効時に済ませた後、
test process全体を `--network none` で起動します。停止中のkernel tunnel deviceは接続経路と
みなさず、IFF_UPな非loopback interfaceまたはdefault routeが一つでもあればfail-closedします。

### 0.1.0 release candidate実績（2026-07-26）

- `ruff` / `mypy`: 成功
- unit/evaluation（固定NER必須）: 586件成功
- mock upstream live Gateway: 3件成功
- Linux arm64 `--network none` 実CLI E2E: Codex CLI 0.145.0 / Claude Code 2.1.212、
  2件成功
- macOS arm64 clean one-file binary E2E: 3件成功
- source archive: 二つのclean worktreeでbyte一致、展開後setup/smoke成功

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
