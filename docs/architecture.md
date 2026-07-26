# アーキテクチャ

SecurityMasker は、外部 LLM へ送る前に機密情報を仮名化し、戻り経路で復元する
専用の透過 proxy です（ADR-0006、`doc/00-First-Order.md` §3）。送受信の両方向を
一つの proxy が所有するため streaming response も復元できます。これが LiteLLM を
撤廃した理由です。

```text
Codex (OpenAI Responses /responses)・Claude Code (Anthropic /v1/messages) — SSE
   │   client 自身の資格情報（ChatGPT OAuth／Anthropic key）を付与
   ▼
SecurityMasker Proxy  (securitymasker.gateway — Starlette + httpx)
   │   session 解決 → request をマスク → 転送（認証は透過し、ログに残さない、§25）
   │   → response を復元（非 streaming JSON および SSE stream）
   ▼
OpenAI／Anthropic／ChatGPT backend   （マスク済みデータだけを受信）
```

一回の handler 呼び出しが session 解決、request のマスク、転送、response の復元を
すべて担います。したがって、マスクと復元は必ず同じ session を共有し、pre/post 間の
相関問題が生じません。

## レイヤー

- **gateway/** — proxy 本体。`app`（route）、`forwarder`（httpx による透過転送と
  認証 pass-through）、`session`（session ID 解決）、`identity`（tenant+user
  binding）、`runtime`（engine／store と upstream 設定）を所有します。SSE の復元は
  ここではなく `streaming/` が所有します。
- **integrations/** — `codex`／`claude_code` の client 設定 helper。LiteLLM には
  依存しません。
- **protocols/** — `openai_responses`、`anthropic_messages`、`sse`、
  `structured_walker`。どの field が user text を保持するかを判断し、構造には
  触れません。
- **engine** — normalize → detect → policy resolve → alias → replace → leak re-scan
  というマスク処理と、alias → original という復元処理を統括します。
- **detectors/** — `existing_alias`、`dictionary`、`regex`、`secret_patterns`、
  `formats`、日本語 recognizer、任意の `presidio`／`japanese_ner`。
- **aliases/** — replacement `profiles` と collision-safe な `factory`。
- **sessions/** — `store` Protocol、`memory`、`redis`、`crypto`。Redisではowner token
  の確認と暗号化sessionの `SET`／`DEL` をLuaで一つのatomic操作にし、lease失効後の
  stale workerが別workerのalias tableを上書き・削除できないようfenceします。
- **streaming/** — `text_replacer`（carry buffer）、`tool_arguments`、
  `openai_responses_stream`、`anthropic_messages_stream`。
- **policy / normalization / models / config / cli / logging / metrics**。

## データフロー（request）

1. session を解決する（header → `previous_response_id` →
   session-id／thread-id → ephemeral）。
2. endpoint で route を選ぶ（`/responses` = OpenAI、`/messages` = Anthropic）。
3. protocol 構造をたどり、各 user-text field に対して NFKC normalize →
   detector 実行 → overlap 解決（最長・最高 priority を優先し、既存 alias を保護）→
   alias の取得または作成 → 構造を保持した置換を行う。
4. 送信直前に再検査し、残存する秘密があれば fail-closed で拒否する。
5. マスク済み payload を転送する。

## データフロー（response／stream）

proxy が response を所有するため、旧 LiteLLM callback と異なり、復元結果は必ず
client まで到達します。

- 非 streaming：upstream JSON を parse し、text／tool argument field を復元して返す。
- streaming（SSE bytes → decode → parse → restore → re-serialize）：
  - OpenAI Responses（`streaming/openai_responses_stream`）：
    `output_text.delta` は block ごとの carry buffer で処理する。
    `output_text.done`／`content_part.*`／`output_item.*`／`response.completed`
    の全文を復元する。`function_call_arguments` は buffer に集約し、復元した一つの
    delta として再送する。
  - Anthropic（`streaming/anthropic_messages_stream`）：
    `text_delta` は block ごとの carry buffer で処理し、`input_json_delta` は
    再構築して一つの復元済み delta として再送する。
- 復元対象は、この session が発行した `literal` alias だけとする。
  `env_reference` は `${...}` のまま保持する（§10、§19、§20、§21）。

固定 version と検証済み upstream event shape は
[compatibility.md](compatibility.md)、設計判断は [adr/](adr/)、
特に ADR-0006 を参照してください。

## モジュールの責務（第4回監査後の構成）

一つの責務に一つの配置先を割り当てます。

- `context/` — segmentation だけを担当し、detector、HTTP、session を知りません。
- `detectors/` — 検出を担当します。`detectors/inference.py` は全 model-backed
  detector が共有する bounded pool を所有し、thread 上限を detector 単位ではなく
  process 全体へ適用します。
- `streaming/` — 両 protocol の SSE 復元を担当します。
  `openai_responses_stream.py` と `anthropic_messages_stream.py` は、実装順による
  偶発的な package 分割を解消して同じ場所に置きます。
- `gateway/` — request orchestration を担当します。`gateway/identity.py` が
  assertion 検証を所有するため、request handler に暗号処理を置かず、store には
  検証済み namespace だけを渡します。
- `integrations/` — Codex／Claude の client 固有知識を担当し、CLI から
  provider 分岐を排除します。
- `devtools/` — Compose demo の mock upstream と手動 tool を置きます。実行可能な
  service は test ではなく、test namespace を deployment dependency にしないため、
  意図的に `tests/` 配下へ置きません。

masking core（`engine`、`policy`、`aliases`、`detectors`、`normalization`）は
`gateway`、`integrations`、`cli`、`doctor` を import しません。任意依存
（torch、transformers、presidio、redis）は function 内で import し、最小構成では
読み込みません。
