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
- 通常testではproviderへ合成promptを含めて送らない。下記の明示opt-in実OpenAI E2Eだけを
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

実OpenAIサーバとの互換性E2Eは、実Codex app-serverの既存ChatGPT認証を表示・複製せず、
command lineの一時config overrideで行います。このtestは外部送信とモデル利用を伴います。
固定した合成PERSONだけを送ることを確認し、明示的にopt-inした場合だけ実行します。

```console
SM_RUN_OPENAI_E2E=1 .venv/bin/python -m pytest -q \
  tests/integration/test_real_openai_e2e.py
```

標準では一つのCodex turn内でdynamic toolを8回直列実行します。4〜20回の範囲で変更できます。

```console
SM_RUN_OPENAI_E2E=1 SM_OPENAI_E2E_TOOL_CALLS=12 \
  .venv/bin/python -m pytest -q -s tests/integration/test_real_openai_e2e.py
```

成功条件は実Codexのturn完了、tool call数の一致、WebSocket接続数1、完了response数がtool
call数+1以上、各tool resultのmask、最終responseでの合成値復元、alias非残存のすべてです。
transportの比較を行う場合は、同じprocess条件で同一tool chainをWebSocket、HTTPの順に実行し、
wall timeの生値と差を記録します。

```console
SM_RUN_OPENAI_E2E=1 SM_OPENAI_E2E_COMPARE_HTTP=1 \
  SM_OPENAI_E2E_TOOL_CALLS=4 \
  .venv/bin/python -m pytest -q -s tests/integration/test_real_openai_e2e.py
```

外部serviceの負荷、prompt cache、生成時間をtransport固有の時間から分離できないため、単回の
大小関係は合否条件にしません。`serverOverloaded`だけはfresh Codex/Gateway processで1回再試行
しますが、leak block、protocol error、timeoutは再試行して成功扱いにしません。実行時のJSON
出力へ両transportのwall timeと短縮率を残し、一般的な性能保証値にはしません。
通常の利用者設定fileは変更せず、threadには`ephemeral`を指定します。transport互換性に
detector modelの揺らぎを混ぜないため、このtest専用の一時configだけ日本語NERを無効にし、
辞書で固定合成値を検出します。WebSocket接続数と完了response数はDEBUG eventで検証するため、
同じ一時configの`logging.level`だけを`DEBUG`にします。

## Desktopの扱い

自動testはCodex CLIとClaude Code CLIをDesktopのprotocol surrogateとして使います。DesktopとCLIが
共有する設定生成元をtestしますが、Desktop UIそのものを自動操作したとは表現しません。

手動Desktop smoke testを行う場合も合成promptだけを使い、結果をwire-level証明として扱いません。
