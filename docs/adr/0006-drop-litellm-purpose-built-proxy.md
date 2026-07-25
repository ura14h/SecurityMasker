# ADR-0006: LiteLLM 依存を撤廃し、Codex/Claude Code 専用の自作透過プロキシへ

- Status: Accepted
- Date: 2026-07-25
- 関連: [ADR-0001](0001-litellm-guardrail-integration.md)（撤回）/ `doc/00-First-Order.md` §3-4, §40 /
  [compatibility.md](../compatibility.md)（Codex 実 E2E 知見）

## Context

`doc/00-First-Order.md` は**初期命令（ブリーフ・方針）**であり、変更不能な制約宣言ではない（冒頭が
「安全側の前提を置き ADR に明記せよ」と工学判断を招いている）。その二層を区別する:

- **不変（製品の目的・ツール非依存）**: 機密を外部へ出さない / セッション・テナントを混ぜない /
  構造を壊さない / fail-closed（§40-1..4）/ ログに秘密を残さない（§25）/ 合成データのみ（§30）/
  protocol adapter と masking core の分離（§40-6）。
- **手段（工学判断で見直し可）**: LiteLLM を土台にする（§4, §38-16/17/18）等。

実測で判明した LiteLLM（1.93.0・最新 main とも）の構造的限界:

1. **OpenAI Responses の HTTP ストリーミング応答を、どのコールバック hook でも書き換え不能**
   （iterator / per-chunk / deployment の 3 hook を実測。deployment hook は 18 回発火・session 解決・
   chunk 復元・返却しても**クライアント出力は不変**）。Codex の主 UX がこれ。litellm は生 SSE を
   そのまま流し、parsed イベントはログ/ガードレール検査専用。
2. Codex の ChatGPT 認証を素直に扱えない（プロバイダは env_key＝API キー前提）。
3. 設定が複雑（callback shim・プロバイダルーティング）。バグが多く、2026-03 に供給網インシデント。

**セキュリティ製品にとって、挙動を完全制御できない巨大依存はむしろ負債**である。

## Decision

**LiteLLM 依存を撤廃**し、Codex（OpenAI Responses）と Claude Code（Anthropic Messages）だけを
対象とする**自作の薄い透過マスキングプロキシ**を構築する。**LiteLLM 非依存の masking core は温存**
（設計上 `integrations/litellm.py` にのみ閉じ込めてあり、engine/detectors/sessions/crypto/aliases/
policy/normalization/protocols/streaming はそのまま再利用可）。

### 検証済み（本 ADR の前提を実測で確認）
- **透過 OAuth パススルー成立**: Codex を `requires_openai_auth=true` ＋ カスタム base_url に設定すると、
  Codex は自分の **ChatGPT OAuth JWT（Bearer, len≈1812, `eyJ...`）＋ `chatgpt-account-id`** を
  カスタムエンドポイントの `/v1/models`・`/v1/responses` へ送る。プロキシは Authorization を素通し
  するだけでよい（保存・復号・ログなし＝§25 準拠）。
- **マスクは実バックエンドで実証済み**: chatgpt.com への実送信ボディに登録機密 0 件（[compatibility.md]）。

## Alternatives

- **B. LiteLLM 上流対応を待つ/貢献**: ネイティブ可逆マスキングは WS のみ実装中で HTTP は未対応・時期不明。
  設計思想（Presidio ベース）が SecurityMasker（per-session HMAC alias・テナント分離・env_reference）と
  異なる。却下。
- **C. サポート範囲を Responses 非stream 等に限定**: 主用途 Codex のストリーム表示を諦める。却下。
- **D. in-process タップ相関**: litellm のヘッダ転送挙動依存で脆い。却下。

## Consequences

- 受け入れ基準のうち **§38-16/17/18（LiteLLM 固有）は適用対象外**になる。**不変条件（§40-1..4, §25, §30）は
  引き続き拘束**する。§38 のうち「最終送信に機密なし」「alias 復元」「SSE 分割復元」「tool 引数 JSON」
  「構造キー不変」等は自作プロキシでも達成する（むしろ完全化する）。
- 上流プロトコル互換（OpenAI Responses / Anthropic Messages の 2 本）を自分で保守する。ただし**透過**
  （未知フィールドは素通し・text 値だけ触る）なのでプロバイダ変更への耐性は高い。
- 設定が単純化（base_url を向けてセッションヘッダを付けるだけ）。
- **応答方向を完全制御**できるため、Responses ストリーミング復元が可能になる。
- `integrations/litellm.py` と litellm extra は撤去（または deprecated 化）。既存 unit/eval テスト
  （138+）は core 対象でそのまま生存。統合テストのみ差し替え。
- ADR-0001（LiteLLM guardrail 統合）は本 ADR により撤回。
