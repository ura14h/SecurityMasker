# Compatibility — 対応バージョンと検証済み経路

> **重要（2026-07-25, ADR-0006）**: LiteLLM 依存は**撤廃**した。以下「§Phase 0〜3」「§Codex 実 E2E」の
> LiteLLM 関連記述は、撤廃の**理由**を残す歴史的記録。現行アーキテクチャは自作透過プロキシ
> （`securitymasker.gateway`）。

## 現行構成の検証（自作プロキシ・実機、2026-07-25）

- **透過 ChatGPT OAuth パススルー成立**: Codex を `requires_openai_auth=true` ＋ カスタム base_url に設定
  すると、Codex は自分の ChatGPT OAuth JWT（Bearer）＋ `chatgpt-account-id` をプロキシへ送る
  （`/models`・`/responses`）。プロキシは Authorization を素通し転送（保存/復号/ログなし §25）。
- **実 Codex 0.145 → 新プロキシ → 本物の ChatGPT バックエンド E2E 成功**:
  - chatgpt.com への実送信ボディに登録機密 **0 件**（alias のみ）。gateway ログにも 0。
  - **Codex 画面に原本が復元表示**（山田太郎 / 株式会社極秘技研）— LiteLLM で不可能だった
    Responses ストリーミング復元がクライアントまで到達。
- Anthropic Messages（stream/非stream）も同プロキシで mask+restore・0 漏えいを確認。
- 依存: Python 3.12 / Starlette / uvicorn / httpx / pydantic / cryptography（`requirements.lock`、36 pkg）。
  Presidio 日本語 NER は任意（`presidio-analyzer==2.2.364` / `spacy==3.8.14` / `ja_core_news_md==3.8.0`）。

---

## （歴史的記録）LiteLLM 撤廃の理由と Phase 0 互換性固定

以下は撤廃前の LiteLLM 1.93.0 に対する検証記録。撤廃の判断根拠（特に §13・Codex 実 E2E の
「Responses HTTP ストリーミング応答をどのコールバックでも書き換え不能」）を保存する。

最終確認日: 2026-07-24（LiteLLM 撤廃: 2026-07-25）

## 固定バージョン（撤廃前）

| コンポーネント | 固定バージョン | 備考 |
|---|---|---|
| Python | 3.12.13 | 要件 §36 は 3.12+。Homebrew `python@3.12` を使用 |
| 依存管理 | pip + venv | ユーザー合意。`requirements.lock` に `pip freeze` を固定（[ADR-0002](adr/0002-pip-venv-over-uv.md)） |
| LiteLLM | `litellm[proxy]==1.93.0` | 最新安定版（2026-07-19）。**供給網インシデント（2026-03）は 1.82.7/1.82.8 のみ**が対象で本版は安全 |
| Presidio | `presidio-analyzer==2.2.364` / `spacy==3.8.14` / `ja_core_news_md==3.8.0` | in-process 日本語 NER（任意、[ADR-0004](adr/0004-presidio-in-process.md)、[requirements-presidio.lock](../requirements-presidio.lock)） |
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

## ライブ Proxy 検証結果（実施済み 2026-07-24）

`tests/integration/test_live_proxy.py`（`SM_RUN_LIVE=1` で実行、mock upstream + 実 LiteLLM Proxy を
subprocess 起動）で確認。**6 passed**。

- [x] no-op `SecurityMaskerCallback` を config 登録 → Proxy 正常起動（`/health/liveliness` = "I'm alive!"）
- [x] `/v1/chat/completions`（stream / non-stream）疎通・SSE fixture 化
- [x] `/v1/responses`（stream / non-stream）疎通・SSE fixture 化（Codex 経路）
- [x] `/v1/messages`（Anthropic, stream）疎通・SSE fixture 化（Claude Code 経路）
- [x] **§25**: `set_verbose: false` で proxy ログに元の秘密・API キーが **0 件**（漏えいなし）

fixture: `tests/integration/fixture_openai_chat_stream.sse` /
`fixture_openai_responses_stream.sse` / `fixture_anthropic_messages_stream.sse`。

### 統合で判明した重要事項

1. **guardrail のロード方式**: LiteLLM の config ローダー（`litellm/proxy/types_utils/utils.py`
   の `get_instance_fn`）は、`config_file_path` があると dotted path を
   **config ディレクトリ相対のファイルパス**として解決し、インストール済みモジュールへは
   フォールバックしない。→ SecurityMasker は config に隣接する 1 行 shim
   （`securitymasker_guardrail.py`）を同梱し、`securitymasker_guardrail.SecurityMaskerCallback`
   として参照する。実体はインストール済み `securitymasker` パッケージ。
2. **Responses の `id` 変換**: LiteLLM は Responses の `id` を
   `resp_<base64("litellm:custom_llm_provider:...;model_id:...;response_id:<orig>")>`
   に書き換える。`previous_response_id`→セッションキー導出（§7）で復号が必要。
3. **Responses SSE 形式**: LiteLLM は `event:` 行を付けず `data: {..., "type": "..."}` の
   SDK 形式で返す（`type` が JSON 内）。Anthropic 経路は `event:` 名とブロック構造を透過保持。
4. **chat/completions stream**: LiteLLM が独自の `ModelResponseStream` チャンク形式へ再直列化
   （`async_post_call_streaming_iterator_hook` が見るのはこの形式）。

### Phase 2 で判明した重要事項（2026-07-24、実測）

5. **登録方式で発火するフックが変わる**:
   - `guardrails:` に登録すると **mode（pre_call 等）で限定**され、pre_call しか発火しない。
   - マスク（pre_call）＋復元（post_call/streaming）の**全ライフサイクル**を回すには
     `litellm_settings.callbacks:` に登録する。
   - ただし callbacks 経路は文字列を **インスタンス化しない**（クラスをそのまま使い、
     フックが unbound になり `self` 不足で失敗する）。→ shim で**インスタンス**を公開し
     `securitymasker_guardrail.securitymasker_callback` を参照する（`config/securitymasker_guardrail.py`）。
6. **Responses ストリーミングのチャンク型**（iterator hook で観測）:
   - `ResponseCreatedEvent`（`.response` に全体オブジェクト）
   - `OutputTextDeltaEvent`（`.delta` にテキスト片、`item_id`/`content_index`）
   - `ResponseCompletedEvent`（`.response` に全体オブジェクト）
   → delta は carry buffer で逐次復元、created/completed の埋め込み `response` は直接復元。
   chat は `choices[].delta.content`。
7. **post_call のレスポンスは pydantic オブジェクト**（dict ではない）。属性で in-place 復元する
   （`ResponsesAPIResponse.output[].content[].text` / `ModelResponse.choices[].message`）。

### Phase 3 で判明した重要事項（2026-07-24、実測）

8. **Anthropic `/v1/messages` ストリーミングは iterator hook で生 SSE の `bytes` を流す**
   （chat/Responses が型付きオブジェクトを流すのと異なる）。→ `streaming/anthropic_stream.py`
   で UTF-8 逐次デコード → SSE パース → `text_delta` はブロック index 毎 carry buffer で復元、
   `input_json_delta` はブロック完了まで蓄積し 1 イベントに再構成、その他は透過。
9. **ルーティングは `call_type`**（`anthropic_messages` を含む）で判定。復元は chat/Responses
   （`choices`/`output`）と Anthropic（`content`）が排他フィールドのため両復元器を無条件適用
   しても二重復元にならない。
10. Anthropic 非ストリームのレスポンスは pydantic オブジェクト（`.content[].text` / `tool_use .input`）。

### Codex 実バージョン E2E で判明した重要事項（2026-07-25、実測）

実 Codex CLI 0.145.0 → Gateway → 本物の ChatGPT バックエンド（`litellm chatgpt/` プロバイダ、
OAuth 再利用・API キー不要）を送信内容タップ付きで検証。ハーネス: `scripts/codex_e2e_setup.py`
（隔離 `CODEX_HOME`/`CHATGPT_TOKEN_DIR`・`~/.codex` 無変更）/ `config/litellm.codex-e2e.yaml` /
（この検証に使った使い捨ての透過フォワーダ spike は、production path と混ざらないよう削除済み。再検証が必要なら `devtools/` に置くこと。）

11. **マスクは実環境で機能（§38-1 を実バックエンドで実証）**: chatgpt.com へ実送信されたボディに
    登録機密が **0 件**、alias のみ。最重要要件を本物の外部 LLM への実呼び出しで達成。
12. **`litellm chatgpt/` プロバイダは API キー不要**: `Authenticator` が `auth.openai.com` の
    デバイスコード OAuth（`~/.config/litellm/chatgpt/auth.json`、`CHATGPT_TOKEN_DIR` で差し替え可）で
    自己認証し `chatgpt.com/backend-api/codex` へ送る。**パススルーではなく Gateway が終端・再送信**
    （マスクのため不可避）。Codex の `~/.codex/auth.json` は `tokens.*` ネスト構造で、トップレベル
    キーを期待する litellm とは非互換 → 整形コピーで再利用可。
13. **Responses API の HTTP ストリーミング復元は、どのコールバック hook でも不可能**（1.93.0・
    最新 main とも）。3 hook すべてを実測:
    - `async_post_call_streaming_iterator_hook`: parsed イベントを変更しても破棄。
    - `async_post_call_streaming_hook`（per-chunk）: `isinstance(response, (ModelResponse,
      ModelResponseStream))` ガードで **Responses イベントを除外**（main でも同一・未修正）。
    - `async_post_call_streaming_deployment_hook`: イテレータ `__anext__` 内で毎チャンク発火・
      `request_data` あり・戻り値で差し替え可 → **実測で 18 回発火・session 解決・chunk 復元・返却
      しても Codex 出力は不変**。
    → LiteLLM は Responses HTTP ストリーミングで**上流の生 SSE をそのままクライアントへ流し、
    parsed イベントはログ/ガードレール検査専用**。マスク（安全性）は全経路で担保され、復元されない
    のは表示のみ（ユーザーには不透明 alias が見えるだけで、機密漏えい・誤データではない＝fail-safe）。
14. **LiteLLM は Responses API にネイティブ可逆マスキングを実装中**（main の WebSocket モード
    `ResponsesWebSocketStreaming`: `_mask_response_create`/`_unmask_response_event`/
    `_mask_response_completed`・`apply_to_output`・`output_guardrail_callbacks`）。HTTP へ展開されれば
    コールバック経由の復元が可能になる見込み。
15. `async_post_call_streaming_deployment_hook` ＋ `_restore_chunk_plain` は意味的に正しい最上流の
    復元点として実装済み（他プロバイダ/将来の litellm 挙動では有効）。現状 Responses HTTP ストリームでは
    litellm 側の制約で無効、と docstring に明記。

### 方針の選択肢（Responses ストリーミング完全復元）

- (a) LiteLLM 本体の HTTP 対応を待つ / 上流貢献。
- (b) litellm のモデルルーティングを使わず、**専用 in-process Responses パススルー proxy**（mask/unmask を
  1 プロセスで完結＝§25 遵守）を自作。
- (c) 非ストリーム運用 / Anthropic 経路（stream 含め完全動作）を推奨。

### 残（後続フェーズ）

- thinking blocks / citations の詳細処理（現状は透過、text は復元対象）
- Claude Code 実バージョンでの E2E（optional integration）
- 外部ログ連携（Langfuse 等）は raw request 非送信を保証できるまで既定無効（デプロイ検証で担保）

暫定の安全既定（§25）: 本番では詳細デバッグログを無効化し、外部ログ連携は raw request
非送信を保証できるまで既定で無効にする。
