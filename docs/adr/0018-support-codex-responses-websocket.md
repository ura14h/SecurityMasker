# ADR-0018 — Codex向けResponses WebSocket transportを同一masking境界で扱う

- 状態：採用
- 日付：2026-07-30
- 関連：[architecture](../development/architecture.md)、
  [development status](../development/status.md)、
  [ADR-0006](0006-drop-litellm-purpose-built-proxy.md)、
  [ADR-0012](0012-renew-package-design.md)

## 背景

Codexのcustom model providerには、Responses APIのWebSocket transportを使うかを示す
`supports_websockets`がある。SecurityMaskerは従来これを`false`としてHTTP POSTとSSEだけを
受理していた。

Responses WebSocket modeは`/v1/responses`への接続を維持し、一つの接続で
`response.create`を繰り返す。request bodyはHTTPのResponses createとほぼ同じだが、
`stream`は暗黙であり、`background`は使わない。server eventと順序はResponsesの既存streaming
event modelと同じで、接続は一度に一つのresponseだけを処理する。

Codexの長いtool loopでWebSocketを無効にし続けると、clientが提供する低遅延transportを
利用できない。一方、単なるbyte relayとして有効化すると、client eventに含まれる原文や
tool outputがmasking coreを通らず、製品の最重要不変条件に反する。

Codexの`ModelClientSession`は一つのuser turnごとに作られ、そのturn内のprewarmと複数の
Responses API callで一つのWebSocketを再利用する。したがって「複数user turnを同じsocketで
送れたこと」ではなく、「一つのturnでtool callとtool resultを数回から十数回往復し、同じ
socketでResponsesを継続できたこと」を実互換性と性能の受入条件にする。

## 決定

`chatgpt` modeだけで、既存の`/responses`と`/v1/responses`へHTTP POSTに加えて
WebSocket接続を公開する。Codex用snippetは`supports_websockets = true`を生成する。
`claude` mode、未知route、public bind、複数workerは変更しない。

WebSocketは新しいmasking方式ではなくtransport adapterとする。JSON objectの
`response.create`からtransport固有の`type`を除いたpayloadを、既存
`openai_responses.mask_request`、最終payload leak guard、暗号化session storeへ通す。
上流server eventは既存`ResponsesStreamProcessor`と同じevent変換規則で復元する。
protocol adapterとmasking coreは引き続き分離する。

上流接続にはasync WebSocket client、ASGI server側のWebSocket protocol実装として
`websockets` packageをruntime依存へ追加し、source setupのlockでversionを固定する。
HTTP/SSEは引き続き`httpx`を使う。provider SDKは導入・forkしない。

## 接続とsessionの境界

一つのdownstream WebSocket接続に一つのsession keyを割り当てる。

1. handshake headerに既存の安定IDがあれば、それをHTTPと同じ優先順位で使う。
2. 安定IDが無ければ接続専用の一時IDを生成し、接続中は変えない。
3. `previous_response_id`はsession keyとして使わず、既存response bindingの検索だけに使う。
4. binding先が接続のsessionと異なる場合は、別sessionのaliasを混ぜずlocal errorにする。
5. 上流のresponse IDは復元前に現在sessionへbindingし、同じ接続の次responseと再接続後の
   HTTP／WebSocket continuationの双方で利用できるようにする。

上流WebSocketはdownstream接続ごとに一つ作る。認証headerは保存・scan・logせず、
既存のOpenAI header allowlistを通して対応providerだけへ送る。handshakeの未知headerを
上流へ透過しない。

## event処理とfail-closed

clientから受理するframeはUTF-8 textのJSON objectだけとし、frame sizeをHTTP bodyと同じ
hard limitで制限する。binary、不正UTF-8、不正JSON、array、過大frameは上流へ送らない。

`response.create`は次を満たした場合だけ上流へ送る。

- Codex 0.145.0がHTTP互換のため付与する`stream: true`はtransport adapterで除く。
  `stream`の他の値と`background`は拒否し、上流へ送らない。
- input、instructions、tool output、tool schemaなどを既存Responses adapterで検査・maskする。
- 未知fieldを含むmask後のobject全体と非認証headerが最終leak guardを通る。
- session storeのget／create／mask／saveを一つのlock区間で完了する。

Codexが現在利用しない未知client eventは、構造を推測して部分maskしない。既知の登録値または
重大secretがevent全体のleak guardで見つかれば接続を閉じ、cleanなeventだけをそのまま透過する。
`response.inject`など、原文を含み得るeventを製品対応と表明するには別途protocol adapterと
E2Eが必要である。

上流からのtext frameはJSON objectとしてparseし、Responses server event processorで
表示textと信頼済みlocal tool argumentだけを復元する。未知server eventは順序と内容を維持する。
response完了後に来る既知の接続単位event `responsesapi.websocket_timing`は、active responseが
なくてもevent全体のleak guardを通して透過し、接続を維持する。それ以外のresponse外server
eventは、responseとの対応を推測せずfail-closedにする。
binary、不正JSON、過大frame、tool argument未完・不正・上限超過、response binding失敗では、
未検査内容をclientへ渡さず安全なerror eventを返して接続を閉じる。

Codexが`previous_response_id`、`prompt_cache_key`、`client_metadata`、input itemの
`id`／`call_id`へ生成するUUID、prefix付きID、millisecond timestampをopaqueなtransport token
として扱う。これらのtokenにも辞書、user regex、重大secret patternを引き続き適用するが、
偶然Luhn等へ一致する一般PII format検査だけを除外する。同じmetadata文字列内のtoken以外の値、
未知field、通常のprompt/tool値は従来どおり全scannerを通す。

HTTP/SSE経路で成功responseの`Content-Type`が欠落し、Responses stream processorを使っている
場合だけ`text/event-stream`を補う。明示されたmedia typeとerror responseには補完しない。

upstream接続失敗、切断、timeoutをHTTPへ自動fallbackしない。fallbackによって同じturnを
二重送信したり、接続localな`previous_response_id`を別transportへ誤用したりしないためである。
clientが新しい接続またはHTTPで再試行するかを決める。

## 検証

実装完了の受入条件を次とする。

1. unit testで、request原文が上流frameに存在せず、response aliasがclient frameで復元される。
2. text deltaの全alias分割位置、tool argument delta、未知clean event、特殊文字を検査する。
3. `stream: true`の正規化と、malformed／binary／oversize／不正`stream`／`background`／
   重大secret／store障害で上流送信0を確認する。
4. 同一接続の複数response、`previous_response_id` binding、異なる接続の並行sessionでaliasが
   混在しない。
5. mock upstreamを使うlive WebSocket E2Eで、最終上流frameに合成機密値が無く、
   clientには原文が復元される。
6. 隔離`CODEX_HOME`と実Codex CLIを使い、生成snippetの
   `supports_websockets = true`でWebSocket経路を完走する。
7. 実Codex app-serverからOpenAI実サーバへ固定合成値だけを送る明示opt-in E2Eで、一つのturnに
   4〜20回（標準8回）のdynamic tool callを直列実行する。接続数が1、完了response数が
   tool call数+1以上、各tool resultがmask済み、最終responseで合成値が復元済み、aliasが
   非残存であることを確認する。
8. 同じtool chainをWebSocketとHTTPで連続実行し、wall timeの生値と差を記録する。
   外部serviceの変動があるため一般的な短縮率は保証しないが、その実行ではWebSocketがHTTPより
   短いことを受入条件にする。providerの`serverOverloaded`だけはfresh processで1回再試行できる。
9. HTTP buffered／SSE、Claude Messages、preview、doctorの既存回帰testが成功する。
10. ruff、mypy strict、unit、evaluationを通す。

実Codex CLIが環境や対象versionの都合でWebSocketを選ばなかった場合、HTTP成功を
WebSocket E2Eの証拠にしない。mockの記録へtransport種別を残し、WebSocket handshakeと
`response.create`受信を明示assertする。

## 文書と運用への影響

architecture、codebase guide、compatibility、導入、troubleshooting、testing、release gate、
statusを更新する。`doctor`はCodex providerの`supports_websockets = true`も検査する。
WebSocketの60分接続上限、再接続時の`previous_response_id`条件、HTTP/SSEも引き続き
利用可能であることを運用文書へ記載する。

## 却下した案

### WebSocketを無効のままにする

安全だが、Codex向けResponses transportの対応要求を満たさず、長いtool loopで利用可能な
継続接続を使えない。

### frameを無検査で双方向relayする

原文がmasking coreを迂回するため却下する。

### WebSocket専用のmasking実装を複製する

HTTPと規則が乖離し、未知field、tool argument、response bindingの回帰を増やすため却下する。

### upstream失敗時にHTTPへ自動fallbackする

送信済みか不明なturnの二重実行と、接続local cacheを使うresponse chainの誤継続を招くため
却下する。

## 参照

- [OpenAI Responses WebSocket mode](https://developers.openai.com/api/docs/guides/websocket-mode)
- [OpenAI Responses WebSocket events](https://developers.openai.com/api/reference/resources/responses/websocket-events)
- [OpenAI HTTP vs WebSocket performance](https://developers.openai.com/api/docs/guides/responses-multi-agent#http-vs-websocket-performance)
- [Codex `ModelClientSession` implementation](https://github.com/openai/codex/blob/main/codex-rs/core/src/client.rs)
- [Codex app-server protocol](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)
