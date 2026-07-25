# 05-Phase6-Design — 自作透過マスキングプロキシ設計メモ

方針転換の詳細設計（[ADR-0006](../docs/adr/0006-drop-litellm-purpose-built-proxy.md)）。
LiteLLM を撤廃し、Codex（OpenAI Responses）と Claude Code（Anthropic Messages）専用の
薄い透過プロキシを構築する。**masking core は温存・再利用**。

作成日: 2026-07-25

## スパイクで確定した事実（実測）

- **透過 OAuth パススルー成立**: `requires_openai_auth=true` ＋ カスタム base_url で、Codex は
  ChatGPT OAuth JWT（Bearer）＋ `chatgpt-account-id` を我々へ送る。→ Authorization を素通し転送するだけ。
- Codex は **`/v1/models`（モデル一覧）と `/v1/responses`** を叩く。両方の対応が必要。
- 転送ヘッダ例: `authorization` / `chatgpt-account-id` / `originator` / `session-id` / `thread-id` /
  `x-codex-*` / `x-openai-internal-codex-responses-lite`。
- マスクは実バックエンドで 0 漏えい実証済み。

## アーキテクチャ

```
Codex ──/v1/models, /v1/responses (SSE, Bearer=ChatGPT OAuth)──┐
Claude Code ──/v1/messages (SSE, x-api-key/anthropic-*)────────┤
                                                               ▼
                                   SecurityMasker Proxy (Starlette + httpx)
                                     1. セッション特定（既存 runtime.resolve_session_id）
                                     2. リクエスト body をマスク（既存 protocols/*）
                                     3. 認証・未知ヘッダは素通しで上流へ転送
                                     4. 応答 SSE を復元（既存 streaming/*）— 双方向を完全制御
                                               │
              ┌────────────────────────────────┼─────────────────────────────┐
              ▼                                 ▼                             ▼
   chatgpt.com/backend-api/codex      api.openai.com/v1 (API キー時)   api.anthropic.com/v1
```

- フレームワーク: **Starlette + httpx**（既に依存済み・軽量）。litellm extra は撤去。
- `/v1/models` はマスク不要 → そのまま透過転送（＋必要なら models 一覧を素通し）。
- 上流 URL は設定可能（既定: Codex=ChatGPT backend / Anthropic=api.anthropic.com）。API キー運用へも
  base_url 差し替えで対応。

## モジュール構成（新設 `src/securitymasker/gateway/`）

```
gateway/
├── app.py            # Starlette ASGI app（ルート定義）
├── forwarder.py      # httpx で上流へ透過転送（認証/未知ヘッダ素通し・§25 で Authorization 非ログ）
├── openai_responses_endpoint.py   # /v1/responses・/v1/models（mask req / restore resp SSE）
├── anthropic_endpoint.py          # /v1/messages（mask req / restore resp SSE bytes）
├── session.py        # セッション特定（ヘッダ優先、既存 runtime を流用/移設）
└── sse.py            # 応答 SSE の逐次復元パイプライン（既存 streaming を配線）
```

再利用（無改造）: `engine` / `detectors/*` / `aliases/*` / `sessions/*` / `policy` / `normalization` /
`protocols/openai_responses.py` / `protocols/anthropic_messages.py` / `streaming/*`
（text_replacer・tool_arguments・anthropic_stream）/ `config` / `models` / `crypto`。

撤去/deprecate: `integrations/litellm.py`・`integrations/runtime.py`（gateway/session へ移設）・
`litellm` extra・litellm 統合テスト・config の callback shim 前提。

## 応答 SSE 復元（Responses）

Codex の Responses SSE を**自前でパース→復元→再シリアライズ**（Anthropic で作った `anthropic_stream.py`
と同型の Responses 版 `responses_stream.py` を新設）。イベント: `response.output_text.delta`（carry buffer）
/ `output_text.done`・`content_part.*`・`output_item.*`・`response.completed`（全文・plain 復元）/
`function_call_arguments.delta`（tool 引数バッファ）。**双方向を自プロセスで所有するため復元が確実に届く**
（LiteLLM 制約が消滅）。

## セッション特定

`X-SecurityMasker-Session-ID` ヘッダ優先（`securitymasker run codex/claude` が付与）。
Codex は `session-id`/`thread-id` も送るので、それらを二次キーに使える（§7 優先順位）。

## 認証（透過パススルー）

- 受信した `Authorization`（ChatGPT OAuth）/ `x-api-key`（Anthropic）/ `chatgpt-account-id` 等を
  **そのまま上流へ転送**。プロキシは値を保存・復号・ログしない（§25）。
- フォールバック: 必要なら実 API キー運用（base_url を実プロバイダへ）。

## テスト

- 既存 unit/eval（138+）はそのまま。
- 新規統合: mock upstream（既存流用）＋ gateway を起動し、mask/restore/漏えい 0 を stream/非stream で検証。
- 実 Codex E2E: 透過パススルー構成（`requires_openai_auth=true`）で mask→復元→表示まで（optional）。

## 段階

1. gateway スケルトン（forwarder＋透過転送）で Codex `/v1/models`・`/v1/responses` が素通し疎通。
2. リクエストマスク配線（既存 protocols）。
3. 応答 SSE 復元（responses_stream / anthropic_stream 配線）— ここで Responses stream 復元を達成。
4. Anthropic 経路。
5. litellm 撤去・docs 更新・CLI（`run`）調整。
