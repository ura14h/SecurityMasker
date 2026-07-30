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
この日常suiteは実providerへtest bodyを送りません。

WebSocketを含むsource Gatewayとlocal mockの実プロセスE2Eは明示的に実行します。

```console
SM_RUN_LIVE=1 .venv/bin/python -m pytest -q tests/integration/test_live_gateway.py
```

## Test data

- 実在人物、実際のsecret、API key、credentialをfixtureへ入れない。
- 通常testではproviderへ合成promptを含めて送らない。下記の明示opt-in実OpenAI smokeだけを
  例外とし、固定した合成値以外を送らない。
- 最重要assertionは「上流が受けた最終payloadに元の合成機密値が存在しない」こと。
- streamingはaliasの全分割位置、tool argument delta、特殊文字をproperty testする。
- session並行性とresponse bindingでalias混在がないことを検査する。
- reject testだけでなくclean inputが受理されるtestも必ず置く。
- test時のHOME、config、state、model cacheを通常利用者の環境と分離する。

## 実CLIと実サーバ

実Codex／Claude Codeとlocal mockを使うegress検証は、全processを外向きinterfaceとdefault routeの
ないLinux network namespaceへ入れます。Codex側がHTTPへfallbackして成功しただけでは
WebSocketの証拠にならないため、mock upstreamの記録で`transport == "websocket"`をassertします。

```console
devtools/run_cli_e2e.sh
```

実OpenAIサーバとの互換性smokeは、実Codexの既存ChatGPT認証を表示・複製せず、一時的な
config overrideで行います。このtestは外部送信とモデル利用を伴います。固定した合成PERSONと
予約済み`.example` hostnameだけを送ることを確認し、明示的にopt-inした場合だけ実行します。

```console
SM_RUN_OPENAI_E2E=1 .venv/bin/python -m pytest -q \
  tests/integration/test_real_openai_e2e.py
```

成功条件は実Codexの終了成功、`sm_websocket_connected`、mask件数、responseでの合成値復元、
alias非残存のすべてです。通常の利用者設定fileは変更せず、`--ignore-user-config`と
`--ephemeral`を使用します。

## Desktopの扱い

自動testはCodex CLIとClaude Code CLIをDesktopのprotocol surrogateとして使います。DesktopとCLIが
共有する設定生成元をtestしますが、Desktop UIそのものを自動操作したとは表現しません。

手動Desktop smoke testを行う場合も合成promptだけを使い、結果をwire-level証明として扱いません。
