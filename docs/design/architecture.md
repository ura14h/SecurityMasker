# Architecture

SecurityMaskerは、単一利用者のローカルPCで動く可逆マスキング境界です。

```text
Codex または Claude Code
              │  provider protocol + client auth
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
4. 不明・障害時はfail-closedとする。重大secretは常にblockする。
5. protocol adapterとmasking coreを分離する。
6. 未知field/event/headerは、leak guard通過後だけ可能な限り透過する。
7. log、error、audit、telemetryへ原文、鍵、平文対応表を残さない。

## modeとprotocol

- `chatgpt`: OpenAI Responses互換routeだけを公開し、CodexのChatGPT認証を
  `https://chatgpt.com/backend-api/codex` へ透過する。
- `claude`: Anthropic Messages互換routeだけを公開し、Claudeの認証を
  `https://api.anthropic.com` へ透過する。

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

## sessionとSQLite

sessionごとにHMAC index keyとAES-GCM keyを暗号乱数で生成します。同じ原文でもsessionが違えば
aliasは独立します。aliasから原文へ戻せる対応表は中核状態であり、通常運用ではSQLiteへ永続化します。

SQLite内ではsession全体をmaster keyでAES-256-GCM封緘し、raw session/response IDをHMAC lookup
へ変換します。AADにはschema version、database ID、mode、record type、lookup keyを含めます。
master keyはsidecar fileに置き、DBへ保存しません。DB/key/mode不一致、tamper、二重writerは
fail-closedです。

previewとunit testだけがin-memory storeを使用します。

## authentication

SecurityMaskerはprovider認証を終端・保存・復号しません。対応providerの認証headerだけを
allowlistで上流へ透過し、log・scan結果・errorへ含めません。クライアント自身の認証状態と、
SecurityMaskerへのrouting設定が必要です。

## 設計判断

現行パッケージ方針は [ADR-0012](../adr/0012-renew-package-design.md)、
専用proxy化は [ADR-0006](../adr/0006-drop-litellm-purpose-built-proxy.md)、
alias暗号は [ADR-0005](../adr/0005-alias-hmac-aes-gcm.md)、
model供給網は [ADR-0010](../adr/0010-model-supply-chain.md) を参照してください。
