# Testing

この文書は開発中のtest方法と、test dataを外部へ出さない条件を説明します。公開判断に使うgateは
[Release gate](release.md)、現在の結果は[開発・リリース状況](status.md)を参照してください。

## 利用者setupとtest setup

通常利用者はruntimeだけを導入します。

```console
./scripts/setup
```

開発者は別の入口でtest、lint、type check依存を追加します。

```console
./scripts/test-setup
```

mock upstream、合成credential、隔離HOME、network namespace、test-only環境変数は`devtools`と
`tests`だけに置き、通常運用手順や配布binaryへ混ぜません。

## 日常的な検証

```console
.venv/bin/ruff check src tests devtools
.venv/bin/mypy src
.venv/bin/python -m pytest -q tests/unit
.venv/bin/python -m pytest -q tests/evaluation
```

変更範囲に対応するtestを先に実行し、統合境界へ影響する場合はmock Gateway testも実行します。
実providerへtest bodyを送りません。

## Test data

- 実在人物、実際のsecret、API key、credentialをfixtureへ入れない。
- providerへ合成promptを含めて送らない。
- 最重要assertionは「上流が受けた最終payloadに元の合成機密値が存在しない」こと。
- streamingはaliasの全分割位置、tool argument delta、特殊文字をproperty testする。
- session並行性とresponse bindingでalias混在がないことを検査する。
- reject testだけでなくclean inputが受理されるtestも必ず置く。
- test時のHOME、config、state、model cacheを通常利用者の環境と分離する。

## Desktopの扱い

自動testはCodex CLIとClaude Code CLIをDesktopのprotocol surrogateとして使います。DesktopとCLIが
共有する設定生成元をtestしますが、Desktop UIそのものを自動操作したとは表現しません。

手動Desktop smoke testを行う場合も合成promptだけを使い、結果をwire-level証明として扱いません。
