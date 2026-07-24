# Compatibility — 対応バージョンと確認済みフック

`doc/00-First-Order.md` §4・§37（Phase 0）に基づく互換性固定の記録。
ここに記載したバージョン・シグネチャは `tests/unit/test_litellm_hook_contract.py` で機械的に固定している。
アップグレード時はまず当該テストを実行し、失敗したら `src/securitymasker/integrations/litellm.py` を先に見直すこと。

最終確認日: 2026-07-24

## 固定バージョン

| コンポーネント | 固定バージョン | 備考 |
|---|---|---|
| Python | 3.12.13 | 要件 §36 は 3.12+。Homebrew `python@3.12` を使用 |
| 依存管理 | pip + venv | ユーザー合意。`requirements.lock` に `pip freeze` を固定（[ADR-0002](adr/0002-pip-venv-over-uv.md)） |
| LiteLLM | `litellm[proxy]==1.93.0` | 最新安定版（2026-07-19）。**供給網インシデント（2026-03）は 1.82.7/1.82.8 のみ**が対象で本版は安全 |
| Presidio | `presidio-analyzer>=2.2.355,<3` | Phase 4 で導入（in-process、[ADR-0004](adr/0004-presidio-in-process.md)） |
| OpenAI SDK | `openai==2.48.0` | litellm 1.93.0 が推移的に固定 |
| Anthropic SDK | `anthropic>=0.40,<1` | Phase 3 で確定 |
| cryptography | `48.0.1` | litellm 推移依存に合わせて上限を `<49` に調整 |
| CLI | 標準ライブラリ `argparse` | litellm の `click==8.4.2` ハードピンと衝突する `typer` を排除（[ADR-0003](adr/0003-argparse-cli.md)） |

`.venv` の完全な固定は [`../requirements.lock`](../requirements.lock)（115 パッケージ）を参照。

## 確認済み LiteLLM フック（1.93.0 実ソース）

`from litellm.integrations.custom_guardrail import CustomGuardrail` を継承する。
`CustomGuardrail` と `CustomLogger` は同一シグネチャで下記フックを提供する。

```python
async def async_pre_call_hook(
    self, user_api_key_dict, cache: DualCache, data: dict, call_type: Literal[...]
) -> Exception | str | dict | None

async def async_post_call_success_hook(
    self, data: dict, user_api_key_dict, response
) -> Any            # response は ModelResponse / ResponsesAPIResponse /
                     # AnthropicMessagesResponse などの Union

async def async_post_call_streaming_iterator_hook(
    self, user_api_key_dict, response: Any, request_data: dict
) -> AsyncGenerator[ModelResponseStream, None]   # ストリームのリアルタイム変換に使用

async def async_post_call_failure_hook(
    self, request_data: dict, original_exception: Exception,
    user_api_key_dict, traceback_str: str | None = None
) -> HTTPException | None
```

### 重要な発見

- `call_type` の Literal に **`responses` / `aresponses`（OpenAI Responses API）** と
  **`anthropic_messages`（Anthropic Messages API）** が含まれる。
  → Codex（Responses）と Claude Code（Messages）の双方が同じフック群を通過する。
- リアルタイムのストリーム変換は `async_post_call_streaming_iterator_hook` を使う。
  `async_post_call_streaming_hook(self, user_api_key_dict, response: str)` は
  組み立て済み文字列を受け取る監査向けで、逐次変換には不向き。
- `async_post_call_failure_hook` で失敗時にも機密が漏れない処理を担保する。

## 未確認・Phase 0 残タスク（ライブ Proxy が必要）

以下は実際に `litellm --config` で Proxy を起動して確認する統合テスト項目（`tests/integration/`）。
本コミット時点では**未実施**（正直な状態記録）。

- [ ] no-op `SecurityMaskerCallback` を config 登録して Proxy 起動 → `/v1/responses`・`/v1/messages` が疎通
- [ ] `/v1/responses` の実 SSE イベント列を記録し `tests/fixtures/` へ
- [ ] `/v1/messages` の実 SSE イベント列を記録し `tests/fixtures/` へ
- [ ] **LiteLLM logging が pre-call hook より前に raw request を保存しないか**検証（§25）。
      保証できない場合は当該ログ連携をデプロイ検証で失敗させる方針。
- [ ] `litellm.set_verbose` / detailed debug が本番で無効であることの検証

暫定の安全既定（§25）: 本番では詳細デバッグログを無効化し、外部ログ連携（Langfuse 等）は
raw request 非送信を保証できるまで既定で無効にする。
