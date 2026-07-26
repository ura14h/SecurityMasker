# ADR-0001: LiteLLM への統合は CustomGuardrail で行う

- 状態：採用（ADR-0006により撤回）
- 日付：2026-07-24
- 関連: [ADR-0006](0006-drop-litellm-purpose-built-proxy.md)（本ADRを撤回）

## 背景

SecurityMasker は LiteLLM 本体を fork せず、リクエスト直前のマスキング・非ストリーム／
ストリーム応答の復元・エラー時の安全処理に介入する必要がある（§4）。LiteLLM は
`CustomLogger` と `CustomGuardrail` の 2 系統で拡張フックを提供する。

## 決定

`litellm.integrations.custom_guardrail.CustomGuardrail` を継承した
`SecurityMaskerCallback` を実装し、proxy config の `guardrails` セクションで登録する。
LiteLLM 依存は `src/securitymasker/integrations/litellm.py` の 1 ファイルに閉じ込め、
masking core からは LiteLLM を import しない。

採用フック（1.93.0 で確認）:

- `async_pre_call_hook` — 送信前マスキング（fail-closed）
- `async_post_call_success_hook` — 非ストリーム復元
- `async_post_call_streaming_iterator_hook` — ストリームのリアルタイム復元
- `async_post_call_failure_hook` — 失敗時の漏えい防止

## 検討した代替案

- **CustomLogger**: 同じフック群を持つが、ガードレールとしての mode（pre_call /
  during_call / post_call）管理や拒否セマンティクスが `CustomGuardrail` の方が明確。
- **LiteLLM を fork**: §4・§40-5 で明確に禁止。却下。

## 影響

- LiteLLM のフック改名・シグネチャ変更は `test_litellm_hook_contract.py` が検知する。
- masking core は LiteLLM 非依存を保ち、単体テスト・再利用が容易。
- SecurityMasker を config から外せば素の LiteLLM として動作（§38-17）。
