# 03-Phase2-Design — Codex / OpenAI Responses 対応 設計メモ

正典 [`00-First-Order.md`](00-First-Order.md) Phase 2（§37, §22）＋ §16・§20・§21。
masking core（Phase 1）を壊さず、OpenAI Responses API のリクエスト/レスポンス/SSE へ配線する。

作成日: 2026-07-24

## コンポーネントと実装順

1. `streaming/text_replacer.py` — **StreamingRestorer**（carry buffer）。
   - alias がチャンク境界で分割されても復元（§20）。最大 alias 長ぶんだけ末尾を保留。
   - str 単位で処理（UTF-8 マルチバイトは分割しない）。`feed(chunk)->str` / `flush()->str`。
   - property-based（hypothesis）で「alias を全位置で分割しても復元」を検証（§30.2）。
2. `protocols/sse.py` — **SSE パーサ/シリアライザ**。`event:`/`data:`（複数行）/`id:`/`retry:`/コメント行を
   保持。未知イベント透過。`data: [DONE]` 透過。
3. `protocols/structured_walker.py` — JSON 構造を再帰走査し、**値の文字列だけ**を変換（§16）。
   構造キー・id・type・role・status・tool 名・schema キーは不変。パス指定で対象フィールドを限定。
4. `protocols/base.py` + `protocols/openai_responses.py` — Responses アダプター。
   - リクエストのマスク対象（§22）: `input`（str または message items の content parts の text）、
     `instructions`、tool definition の `description`、function tool の引数 JSON 内文字列。
   - 変更禁止（§16）: `model` / `*_id` / `type` / `role` / `status` / `previous_response_id` /
     tool `name` / JSON Schema キー・型 / SSE イベント名 / usage。
   - レスポンス（非ストリーム）: `output[].content[].text` 等のテキストを復元。
5. `streaming/tool_arguments.py` — **ツール引数の JSON 再構成**（§21）。
   - tool call id ごとに引数 delta を蓄積 → 完了で `json.loads` → 文字列値を再帰復元 → `json.dumps`。
   - 元値に `" \ 改行 タブ` を含んでも壊れないよう、復元後に必ず再シリアライズ（§21, §30.3）。
   - 復元不能・不完全 JSON は fail-closed（ツール引数では近似復元しない、§24）。
6. `integrations/litellm.py` 配線 —
   - `async_pre_call_hook`: `data` を Responses/chat と判定 → walker でマスク → fail-closed。
   - `async_post_call_success_hook`: 非ストリーム復元。
   - `async_post_call_streaming_iterator_hook`: ModelResponseStream チャンクのテキストを
     StreamingRestorer で復元、tool 引数は tool_arguments で再構成。usage/未知イベントは透過。
   - セッション特定: `X-SecurityMasker-Session-ID` → 会話 ID/previous_response_id → 一時。

## セッション特定（§7）

Phase 2 は優先順位のうち (1) ヘッダー `X-SecurityMasker-Session-ID` と
(2) `previous_response_id`（LiteLLM が base64 で書き換える点は Phase 0 で確認）を実装。
LiteLLM の hook では `data["metadata"]` / proxy_server の request 経由でヘッダーを拾えるかを実確認する。
取得不能時は client type 等から一時セッションを生成（Phase 5 で強化）。

## テスト方針

- text_replacer: hypothesis で alias 全分割位置・連続 alias・prefix のみ・stream 途中終了。
- sse: 複数行 data・コメント・event・retry・unknown・[DONE] のラウンドトリップ。
- walker: 構造キー/id/type 不変、値のみ変換、ネスト配列/オブジェクト。
- tool_arguments: 複数 delta 分割、`"`・`\`・改行を含む値、複数同時 tool call、不完全 JSON は拒否。
- 統合: mock upstream（Phase 0）経由で「送信ペイロードに元機密なし」「レスポンス alias 復元」。

## Phase 2 で扱わない

- WebSocket 版 Responses（§22、別フェーズ）。
- Hosted tool へ実値を渡す処理（§34）。
- Anthropic Messages（Phase 3）。
