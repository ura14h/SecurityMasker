# Architecture

SecurityMaskerは、単一利用者のローカルPCで動く可逆マスキング境界です。

利用者向けの平易な説明は[SecurityMaskerの仕組み](../concepts/how-it-works.md)を参照してください。

```text
Codex または Claude Code
              │  HTTP/SSEまたはWebSocket + client auth
              ▼
SecurityMasker Gateway（loopback、1 mode、1 worker）
  ├─ protocol adapter
  ├─ context segmenter
  ├─ dictionary / deterministic / Japanese NER detectors
  ├─ collision-safe alias factory
  ├─ encrypted SQLite session store
  └─ streaming restore
              │  mask済みpayload + auth passthrough
              ▼
ChatGPT backend または Anthropic API
```

## 不変条件

優先順位は次のとおりです。

1. 元の機密情報を外部へ送らない。
2. session間で秘密やaliasを混ぜない。
3. JSON、code、tool call、patch、shell commandの構造を壊さない。
4. 不明・障害時はfail-closedとする。検出した重大secretはfail-openしない。
5. protocol adapterとmasking coreを分離する。
6. 未知field/event/headerは、leak guard通過後だけ可能な限り透過する。
7. log、error、audit、telemetryへ原文、鍵、平文対応表を残さない。

## modeとprotocol

- `chatgpt`: OpenAI Responses互換routeだけを公開し、CodexのChatGPT認証を
  `https://chatgpt.com/backend-api/codex` へ透過する。`/responses`と`/v1/responses`は
  HTTP POST/SSEとWebSocketの双方を受理する。
- `claude`: Anthropic Messages互換routeだけを公開し、Claudeの認証を
  `https://api.anthropic.com` へ透過する。

Claude Codeが生成する`X-Claude-Code-Session-Id`はsession解決に使い、subagentで付与される
`X-Claude-Code-Agent-Id`と`X-Claude-Code-Parent-Agent-Id`はopaqueな一時transport IDとして
Anthropicへ透過します。これらを原文検出の入力、永続raw ID、logには使いません。

wrong-protocol routeは404でlocal拒否します。両方を使う場合は2プロセスに分けます。

## request処理

1. route、content type/encoding、JSON object、sizeを検査する。
2. client headerとpayloadから安定sessionを解決する。
3. prose/code/shell/JSON/YAML/diffへlosslessに分割する。
4. 辞書と決定論的detectorを全contextで実行し、fuzzy NERは対象contextだけで実行する。
5. session固有aliasを割り当て、protocol adapterがmask可能なvalueを置換する。
6. 未知field、schema key、headerを含む最終payload-wide leak guardを実行する。
7. mask済みpayloadとallowlist済みheaderだけを上流へ送る。
8. responseのtextとtool argumentを、同じsessionが発行したaliasだけ完全一致で復元する。

一部だけ検査して成功を返すことはしません。上限、timeout、model異常、store異常は送信前に
blockします。

## Responses WebSocket

WebSocketはHTTPとは別のmasking方式ではなく、同じrequest pipelineとstream processorを使う
transport adapterです。一つのdownstream接続を一つのsessionへ固定し、一度に一つの
`response.create`だけを処理します。Codexは一つのuser turn内のprewarmとtool loopで
同じ接続へ複数の`response.create`を送り、上流response IDはclientへ返す前にsessionへ
bindingします。

Codex 0.145.0がWebSocket frameへ付ける`stream: true`はadapterで除去します。`stream`の
他の値、`background`、binary、不正JSON、過大frame、別sessionの`previous_response_id`は
上流へ送らず接続をfail-closedで終了します。未知eventはevent全体のleak guardを通過した場合
だけ透過します。response完了後の`responsesapi.websocket_timing`も同じguard後に透過し、
次のresponseまで接続を維持します。上流接続失敗時にHTTPへ自動fallbackしません。

`previous_response_id`、`prompt_cache_key`、`client_metadata`、item `id`／`call_id`から
形式検証で抽出したCodex生成UUID、prefix付きID、millisecond timestampはopaque transport
tokenです。辞書、user regex、重大secretは検査しつつ、このtokenだけ一般PII形式の偶発一致を
除外します。同じmetadata内の他の値、prompt、tool output、未知fieldは全scannerを通します。

## sessionとSQLite

sessionごとにHMAC index keyとAES-GCM keyを暗号乱数で生成します。同じ原文でもsessionが違えば
aliasは独立します。aliasから原文へ戻せる対応表は中核状態であり、通常運用ではSQLiteへ永続化します。

SQLite内ではsession全体をmaster keyでAES-256-GCM封緘し、raw session/response IDをHMAC lookup
へ変換します。AADにはschema version、database ID、mode、record type、lookup keyを含めます。
master keyはsidecar fileに置き、DBへ保存しません。DB/key/mode不一致、tamper、二重writerは
fail-closedです。

通常の`init`は既存layoutを変更しません。明示的な`init --force --directory ...`だけが、
Gateway停止中の標準config、辞書、DB、keyを一組として完全初期化します。稼働中state、symlink、
管理外entryは拒否し、configに記載された外部pathを追跡して削除しません。

previewとunit testだけがin-memory storeを使用します。

## loggingとtelemetry

console logは固定schemaの安全なfieldだけを標準errorへ出し、原文、鍵、平文対応表、認証情報を
含めません。config schema v1の`logging.level`を表示閾値とし、製品起動・mask完了・終了をINFO、
当該Codex処理を継続できないblock・network/stream異常をWARNING、設定・SQLite・bindなどGatewayを
継続できない異常をERROR、接続・切断・通信statusをDEBUGへ分類します。

監査eventはmask件数、固定reason、不可逆session fingerprintだけを持ちます。接続・通信の補助eventは
DEBUGへ分離し、Uvicorn固有のlifecycle/access logには製品のlevel契約を担わせません。

## authentication

SecurityMaskerはprovider認証を終端・保存・復号しません。対応providerの認証headerだけを
allowlistで上流へ透過し、log・scan結果・errorへ含めません。クライアント自身の認証状態と、
SecurityMaskerへのrouting設定が必要です。

## 設計判断

現行パッケージ方針は [ADR-0012](../adr/0012-renew-package-design.md)、
config schemaをv1とする判断は
[ADR-0016](../adr/0016-reset-config-schema-version.md)、
Responses WebSocketの境界は
[ADR-0018](../adr/0018-support-codex-responses-websocket.md)、
明示的な完全初期化は
[ADR-0019](../adr/0019-add-explicit-destructive-init.md)、
専用proxy化は [ADR-0006](../adr/0006-drop-litellm-purpose-built-proxy.md)、
alias暗号は [ADR-0005](../adr/0005-alias-hmac-aes-gcm.md)、
model供給網は [ADR-0010](../adr/0010-model-supply-chain.md)、
現行製品でPython以外への全面移植を採用せず、Pythonを維持する判断は
[ADR-0014](../adr/0014-reject-non-python-port-for-current-product.md) を参照してください。
