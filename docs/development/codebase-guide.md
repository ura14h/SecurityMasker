# コード読解ガイド

この文書はSecurityMaskerのsourceを理解したい人向けの地図です。変更やpull requestを受け付ける
ためのCONTRIBUTINGではありません。現行製品の不変条件は[architecture](architecture.md)、
実装済み範囲は[development status](status.md)を正とします。

## 最初に読む順序

1. repository rootの`securitymasker.py`
2. `src/securitymasker/cli.py`
3. `src/securitymasker/config.py`
4. `src/securitymasker/gateway/runtime.py`、`gateway/app.py`、`gateway/websocket.py`
5. 対象providerの`protocols/` adapter
6. `engine.py`、`context/`、`detectors/`
7. `aliases/`、`sessions/`、`streaming/`

`securitymasker.py`はsource checkout用launcherです。実際のcommand定義は`cli.py`にあり、
常駐Gatewayは`GatewayRuntime`を構築してASGI applicationへ渡します。

## requestが上流へ出るまで

```text
securitymasker.py
  → cli.py: cmd_gateway
  → config.py: load_config / build_engine
  → gateway/runtime.py: GatewayRuntime
  → gateway/app.py / websocket.py: transport・route
  → gateway/request_pipeline.py: header・body・session・最終leak guard
  → protocols/: provider JSONのmask対象を選択
  → engine.py: context分割・検出・policy・alias置換・leak guard
  → gateway/forwarder.py: mask済みpayloadだけを送信
```

読むときの境界は次のとおりです。

- `gateway/`はHTTP/WebSocket、route、header、session binding、転送を担当する。
- `protocols/`はprovider固有JSONのどこをmask・restoreするかだけを担当する。
- `engine.py`はproviderを知らず、text、detector、policy、aliasを統合する。
- `detectors/`は検出spanを返し、置換やsession保存を行わない。
- `sessions/`はalias対応表とresponse bindingを保存し、protocolを知らない。

## responseが戻るまで

buffered responseはprovider adapterで復元します。streaming responseは
`streaming/openai_responses_stream.py`または`anthropic_messages_stream.py`がeventを解釈し、
`text_replacer.py`がchunk境界をまたぐaliasを完全一致で復元します。

tool argumentは表示textと異なります。`streaming/tool_arguments.py`でJSONを再構築し、
`tool_trust.py`のallowlistにあるlocal toolだけ原文へ復元します。外部・provider-hosted・
名前不明のtoolにはaliasを残します。

## moduleの役割

| 場所 | 主な責務 |
|---|---|
| `bootstrap.py` | config、辞書、state directory、master keyの安全な初期化 |
| `config.py` | strict schema、権限、path、env参照、detector構築 |
| `context/` | prose、code、shell、JSON、YAML、diffのlossless分割 |
| `detectors/` | 辞書、secret、format、日本固有PII、日本語NER |
| `policy.py` | 重複spanの優先順位とrestore policy |
| `aliases/` | session固有alias、構造を保つreplacement profile |
| `sessions/` | memory test store、暗号化SQLite、TTL、writer lease |
| `protocols/` | OpenAI Responses、Anthropic Messagesのadapter |
| `streaming/` | SSE差分、tool argument、分割aliasの復元 |
| `gateway/request_pipeline.py` | HTTPとWebSocketで共有するmask・session・最終leak guard |
| `gateway/websocket.py` | Codex Responses WebSocketの接続・frame・並行性制御 |
| `doctor.py` | 外部へbodyを送らないread-only診断 |
| `models_fetch.py` | 固定model revisionとartifact digestの取得・検証 |

## 動作を観察するノブ

実値を外部へ送らず、次の順で実装と挙動を対応付けられます。

```console
python3 securitymasker.py config-check
python3 securitymasker.py entities
python3 securitymasker.py preview < synthetic-prompt.txt
python3 securitymasker.py doctor --json
```

`preview`はGatewayと同じengineをmemory storeで動かします。合成値だけを使い、
detectorを読むときは対応するunit testを同時に実行してください。

```console
.venv/bin/python -m pytest -q tests/unit/test_config.py
.venv/bin/python -m pytest -q tests/unit/test_detectors.py
.venv/bin/python -m pytest -q tests/unit/test_leak_gate.py
.venv/bin/python -m pytest -q tests/unit/test_tool_arguments.py
```

loggingへ原文やalias対応表を追加して観察してはいけません。必要な観測は件数、provider、
固定reason code、原文を含まない型名に限定します。

## testの対応

| 読みたい性質 | 主なtest |
|---|---|
| config、権限、fail-closed | `test_config.py`、`test_config_fail_closed.py` |
| detectorと誤検出 | `test_detectors.py`、`test_japanese_pii.py`、`tests/evaluation/` |
| payload漏えい防止 | `test_leak_gate.py`、`test_protocol_compatibility.py` |
| protocol構造 | `test_responses_adapter.py`、`test_anthropic_adapter.py` |
| streaming | `test_responses_stream.py`、`test_anthropic_stream.py` |
| Responses WebSocket | `test_responses_websocket.py`、`test_live_gateway.py` |
| session分離と暗号化 | `test_multiturn_session.py`、`test_sqlite_store.py` |
| 実CLI境界 | `tests/integration/test_real_cli_e2e.py` |
| 実OpenAI WebSocket反復・HTTP比較 | `tests/integration/test_real_openai_e2e.py`（明示opt-in） |

全体の実行方法、network isolation、通常testの外部送信禁止、明示opt-in実OpenAI E2Eの条件は
[testing](testing.md)にあります。

## 読解時に外してはいけない前提

- clean inputが通ることと、検出漏れがないことを別々に確認する。
- unknown fieldを透過するときも最終leak guardを外さない。
- detector上限到達時にprefixだけを検査して成功を返さない。
- session IDが安定しない継続requestで既存aliasを別sessionへ混ぜない。
- protocol差分をengineへ持ち込まずadapterへ閉じ込める。
- unit testのmemory storeを通常運用の代替にしない。

これらを変更する場合は局所的な実装調整ではなく、architecture、threat model、ADR、release gateへ
影響する設計変更として読む必要があります。
