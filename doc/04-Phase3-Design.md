# 04-Phase3-Design — Claude Code / Anthropic Messages 対応 設計メモ

正典 [`00-First-Order.md`](00-First-Order.md) Phase 3（§37, §23）＋ §16・§20・§21。
Phase 1/2 のコアと streaming/tool_arguments を再利用し、Anthropic Messages API へ配線する。

作成日: 2026-07-24

## コンポーネント

1. `protocols/anthropic_messages.py` — Anthropic アダプター。
   - マスク対象（§23）: `system`（str または text block の `text`）、`messages` の content
     （str または blocks: `text` / `tool_result` の content / `tool_use` の `input` 文字列値）、
     `tools[].description`。
   - 変更禁止（§16）: `type` / `role` / `id` / `name` / `tool_use_id` / `input_schema` キー・型 /
     `stop_reason` / `usage` / message `id`。
   - 非ストリーム復元: `content[]` の text block `text`、`tool_use` の `input` 文字列値。
2. ルーティング — `async_pre_call_hook` の `call_type`（Phase 0 で `anthropic_messages` を確認）で
   Anthropic/OpenAI を判定。プロトコルを `metadata` に stash し、post/streaming で参照。
3. streaming（`async_post_call_streaming_iterator_hook`）—
   - `content_block_delta` の `text_delta.text`: **ブロック index ごとの carry buffer** で復元。
     `content_block_stop` で当該ブロックの buffer を flush。
   - `content_block_delta` の `input_json_delta.partial_json`: **ブロック index ごとに蓄積**し、
     `content_block_stop` まで保留 → 完了時に JSON parse → 文字列値復元 → 再 serialize を
     単一 delta として発行（§21）。不完全 JSON は fail-closed（§24）。
   - `message_start` / `message_delta` / `message_stop` / 未知イベントは透過（usage 不変）。
4. ヘッダー透過 — `anthropic-beta` 等は LiteLLM が転送。SecurityMasker は認証以外を削らない（§23）。

## セッション特定

Phase 2 の runtime を再利用（`X-SecurityMasker-Session-ID` ヘッダー優先）。Claude Code は
カスタムヘッダーを付与できるため、ラッパー CLI（`securitymasker run claude`）で
`SECURITYMASKER_SESSION_ID` を設定。

## テスト

- unit: mask（system/messages/blocks/tool_use input/tool_result/tools desc）、restore（content text /
  tool_use input）、構造キー不変、unknown block 透過。
- streaming（純粋）: content_block_delta の text_delta 分割復元、input_json_delta 蓄積→復元、
  block index ごとの独立性、content_block_stop flush。
- live E2E: mock（Phase 0 の /v1/messages）経由で 0 漏えい＋復元（非stream/stream）。

## 実装時に実確認する点（Phase 2 同様、経験的に）

- LiteLLM iterator hook が Anthropic passthrough でも発火するか、チャンク型は何か。
  発火しない/生 SSE の場合の扱いを決める（compatibility.md へ記録）。

## Phase 3 で扱わない

- thinking blocks の本格処理（透過。text があれば復元対象、その他は透過）。
- citations 等の追加ブロック（透過）。
- Claude Code 実バージョン E2E（optional）。
