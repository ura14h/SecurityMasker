# 01-Plan — SecurityMasker 実装計画メモ

正典は [`00-First-Order.md`](00-First-Order.md)。本メモは着手前の合意事項と作業計画を記録する。
運用ルールの要約は [`../AGENTS.md`](../AGENTS.md)、Claude Code 入口は [`../CLAUDE.md`](../CLAUDE.md)。

作成日: 2026-07-24

---

## 0. 合意済みの前提（ユーザー確認済み）

- 開発環境は **pip + venv** で構成する（`uv` は使わない）。要件 §36 は uv 推奨だが変更可のため pip+venv を採用。→ ADR に記録する。
- LiteLLM のバージョンは **Web で最新安定版を確認して 1 点に固定**し、`docs/compatibility.md` に記録する。
- 実装は本メモを commit した後に **Phase 0 から順に**進める（ユーザー承認済み）。
- 各 Phase 終了時に、動作する状態とテスト結果を残す。

## 1. 不変ルール（詳細は AGENTS.md §2 / doc §40）

1. 元の機密情報を外部（非信頼領域）へ送らない
2. セッション / テナントを混ぜない
3. JSON・コード・ツール呼び出し・patch を壊さない
4. 不明・障害時は fail-closed
5. LiteLLM を fork しない（薄いアダプターに隔離）
6. protocol adapter と masking core を分離
7. 未知フィールド/イベント/ヘッダーは透過（認証除く）
8. ログ・監査・例外・テレメトリに秘密/鍵/平文対応表を残さない
9. Presidio/NER だけに依存せず、ユーザー辞書を最優先
10. API キー・秘密鍵は env_reference 優先
11. テスト可能性・保守性を優先

## 2. 着手前 TODO（Phase 0 の準備）

- [ ] `.gitignore` 作成（秘密・.env・キャッシュ除外）✅ 本コミットに含む
- [ ] `AGENTS.md` / `CLAUDE.md` 整備 ✅ 本コミットに含む
- [ ] `doc/01-Plan.md`（本ファイル）作成・commit
- [ ] Python 3.12+ の venv 用意（システムは 3.9.6）
- [ ] `pyproject.toml` 骨格 + `requirements`/lock 方針決定

## 3. Phase 0: 調査と互換性固定（進行中）

- [x] LiteLLM の最新安定版を Web 確認 → **`litellm[proxy]==1.93.0` に固定**（供給網インシデントは 1.82.7/1.82.8 のみ、本版は安全）
- [x] OpenAI SDK（`openai==2.48.0`、litellm 推移固定）等のバージョン固定。Presidio は Phase 4、Anthropic SDK は Phase 3 で確定
- [x] Python 3.12 venv 構築（3.12.13）、install、`requirements.lock` 生成（115 パッケージ）
- [x] LiteLLM の guardrail hook の**正確なシグネチャ**をソースで確認（`CustomGuardrail`）→ [compatibility.md](../docs/compatibility.md)
- [x] hook シグネチャを固定するテスト追加（`tests/unit/test_litellm_hook_contract.py`、12 passed）
- [x] `docs/compatibility.md` 作成（対応バージョン・確認済み hook）
- [x] ADR 0001〜0005 作成（guardrail 統合 / pip+venv / argparse / Presidio in-process / alias HMAC+AES-GCM）
- [x] no-op `SecurityMaskerCallback`（`CustomGuardrail` 継承）実装＋ config 例
- [x] （ライブ Proxy）`litellm --config` 起動で `/v1/chat/completions`・`/v1/responses`・`/v1/messages` 疎通確認
- [x] （ライブ Proxy）3 プロトコルの実 SSE 構造を fixture 化（`tests/integration/*.sse`）
- [x] （ライブ Proxy）`set_verbose:false` で proxy ログに秘密・API キーが残らないことを検証（§25）
- [x] guardrail ロード方式の判明（config 相対ファイル解決）→ shim 同梱で解決

**完了条件**: 固定バージョンで LiteLLM Proxy が起動し、no-op の `SecurityMaskerCallback` が hook に載ることを確認 → **達成**。
契約テスト（12 passed）＋ ライブ統合テスト（`SM_RUN_LIVE=1`、6 passed）。**Phase 0 完了。**

## 4. Phase 1: コア MVP ✅ 完了

辞書 / Regex / Secret detector、インメモリセッション、HMAC alias、AES-GCM mapping、
replacement profiles、非ストリーム mask/unmask、fail-closed、CLI、unit test。

- [x] models / errors / normalization(NFKC+offset) / crypto(HMAC+AES-GCM)
- [x] aliases: profiles（prose/host/email/ipv4/ipv6/uuid/numeric/env_ref）+ factory（衝突延長・冪等）
- [x] sessions: Protocol + InMemory（TTL・per-session lock）
- [x] detectors: existing_alias / dictionary / regex / secret_patterns
- [x] policy: 重複解決（長一致・優先度）+ existing alias 保護
- [x] engine: mask（正規化→検出→統合→alias→置換→漏えい再スキャン）/ unmask（当該セッションのみ）
- [x] config: YAML 辞書ローダー（value_from_env・起動時検証）+ build_engine
- [x] CLI（argparse）: config validate / entities list・test / doctor / run / sessions
- [x] unit tests **75 passed**、ruff + mypy --strict クリーン

**受け入れ達成**: 同一=同 alias / 別=別 alias / 復元 / 衝突検出 / HMAC 型分離 / 復元ポリシー /
NFKC・全半角・日本語スペース / 長一致 / 二重マスクなし / JSON エスケープ安全 / TTL / AES-GCM 改ざん検知 /
Secret→env_reference / block→fail-closed。

**Phase 1 で未対応（Phase 2+）**: プロトコル walker・SSE ストリーミング復元・ツール引数バッファ、
LiteLLM callback への実配線（現状 callback は no-op、engine は単体で検証済み）。

## 5. Phase 2: Codex 対応 ✅ 完了

OpenAI Responses adapter、SSE parser、streaming text 復元（carry buffer / 全分割位置）、
tool 引数バッファ（複数 delta → parse → 復元 → 再 serialize）、mock upstream、Codex E2E fixture。

- [x] streaming/text_replacer（carry buffer）+ hypothesis で全分割位置検証
- [x] protocols/sse（event/data 複数行/comment/retry/[DONE]/unknown 透過）
- [x] protocols/structured_walker（値のみ変換・キー不変）
- [x] protocols/openai_responses（input/messages/instructions/tool description マスク、
      output/choices/tool 引数 復元、構造キー不変）
- [x] streaming/tool_arguments（複数 delta→parse→復元→再 serialize、不完全 JSON は fail-closed）
- [x] integrations/litellm 配線: pre_call マスク / post_call 復元（chat・Responses オブジェクト）/
      streaming iterator 復元（chat delta・Responses OutputTextDelta・created/completed）
- [x] runtime: SECURITYMASKER_CONFIG から engine/store 構築、セッション特定（ヘッダー→prev_id→一時）
- [x] **ライブ統合テスト 7 passed**（chat/Responses × stream/非stream で 0 漏えい＋復元、
      別セッション別 alias、proxy ログに秘密なし）
- [x] unit **含め 117 passed**、ruff + mypy --strict クリーン

**受け入れ達成（§38）**: 1（最終送信に機密なし）2（alias 復元）3-5（セッション分離）
6（SSE 分割復元）7（tool 引数 JSON 分割→有効 JSON）8（tool 名/id/schema/type 不変）。

**Phase 2 で未対応（後続）**: WebSocket 版 Responses（§22）、Hosted tool 実値、
tool call/function call の実 delta イベント経路（fixture は合成、Codex 実バージョンは optional）、Anthropic（Phase 3）。

## 6. Phase 3: Claude Code 対応 ✅ 完了

Anthropic Messages adapter、content block 処理、tool use/result、input JSON delta、
beta header 透過、unknown block 透過、Claude Code E2E fixture。

- [x] protocols/anthropic_messages（system/messages/content blocks/tool_use input/
      tool_result/tools description マスク、content text / tool_use input 復元、構造キー不変、unknown block 透過）
- [x] callback ルーティング（call_type で Anthropic/OpenAI 判定、復元は排他フィールドで両適用）
- [x] streaming/anthropic_stream（**iterator hook は生 SSE bytes** → UTF-8 逐次デコード＋SSE パース、
      text_delta をブロック毎 carry buffer で復元、input_json_delta を蓄積→1 イベント再構成、fail-closed、透過）
- [x] beta header 等は LiteLLM 転送に委ね、SecurityMasker は認証以外を削らない
- [x] unit（adapter 7 + stream 7）+ **ライブ Anthropic 非stream/stream 復元・0 漏えい**
- [x] **合計 132 passed**、ruff + mypy --strict クリーン

**受け入れ**: §38 の Claude Code 相当（content blocks / tool use / input JSON delta / unknown 透過）も充足。

## 7. Phase 4: 日本語 PII ✅ 完了

Presidio adapter、JP phone / postal / My Number（チェックディジット）、DOB 文脈、
Japanese NER adapter（モデル差し替え可）、composite address detector、評価コーパス（precision/recall/F1）。

- [x] japanese_my_number（公式チェックディジット検証・文脈語・チェック不一致は非検知）
- [x] japanese_phone（各形式・区切りなしは文脈必須）/ japanese_postal_code（〒/県名/文脈）
- [x] date_of_birth（生年月日文脈で昇格）/ japanese_address（複合・1スパン統合）
- [x] formats（email / IPv4 レンジ検証 / credit card Luhn→block）
- [x] presidio adapter（in-process・import ガード・未導入で no-op、ADR-0004）
- [x] japanese_ner adapter（HF モデル差し替え可・未設定で無効・§14.1）
- [x] config 配線（japanese_pii / presidio / ner / enable_format_detectors）
- [x] 評価コーパス（正例・負例、prose/code）+ P/R/F1 ハーネス（`tests/evaluation/`、**現状 P/R/F1=1.00**）
- [x] docs/japanese-pii.md、example config 更新
- [x] unit **129 passed**、ruff + mypy --strict クリーン（合成データのみ、§30）

**受け入れ（§38-13）**: 氏名・電話・メール・住所・郵便番号・マイナンバーのテストあり。

## 8. Phase 5: 運用強化 ✅ 完了

Redis store、multi-tenant 分離、暗号化永続、metrics、audit log、Docker hardening、
compatibility CI、performance benchmark。

- [x] RedisSessionStore（master key で全体を AES-GCM 封緘・鍵は Redis に置かない・
      テナント名前空間分離・TTL・SET NX ロック）、fake Redis で単体テスト
- [x] metrics（カウンタ/タイマ）+ audit（安全フィールドのみ・session id は fingerprint、§25）
- [x] Dockerfile（非 root・最小・healthcheck・read-only 前提）+ docker-compose（mock/gateway/redis）
      + .dockerignore + config/litellm.docker.yaml（`docker compose up` デモ、§38-20）
- [x] GitHub Actions CI（ruff/mypy/tests + **hook 契約テスト**で LiteLLM 互換監視、§5/§36）
- [x] performance benchmark（10KB/100KB/1MB・秘密 100/1k/10k）+ 回帰テスト。
      **ベンチで二次計算量を 2 箇所発見・修正**（policy をクラスタ分割で近似線形化、
      leak 再スキャンを一意化）→ 1MB マスキング 14.3s→**0.38s**
- [x] 運用ドキュメント: SECURITY.md / architecture / threat-model / operations / configuration
- [x] Codex / Claude Code 設定ヘルパー（integrations/codex.py・claude_code.py）+ LICENSE(Apache-2.0)
- [x] **合計 153 passed**（unit+eval+live）、ruff + mypy --strict クリーン

## 8.5 Phase 6: LiteLLM 撤廃・自作透過プロキシ ✅ 進行中（コア完了）

[ADR-0006](../docs/adr/0006-drop-litellm-purpose-built-proxy.md) / [doc/05-Phase6-Design.md](05-Phase6-Design.md)。
LiteLLM は Responses HTTP ストリーミング応答を書き換え不能（3 hook 実測）＋ ChatGPT 認証を扱いにくい
ため撤廃。Codex/Claude Code 専用の薄い透過プロキシ（Starlette+httpx）を新設。masking core は温存。

- [x] **透過 ChatGPT OAuth パススルーを実測検証**（`requires_openai_auth=true` で Codex が自分の
      OAuth JWT をカスタム base_url へ送る）
- [x] `gateway/`（session/forwarder/responses_stream/runtime/app）実装。認証は素通し・保存/ログしない（§25）
- [x] **Responses ストリーミング復元がクライアントまで届く**（LiteLLM で不可能だった点を解消）
- [x] Anthropic 経路（stream/非stream）も同プロキシで動作
- [x] LiteLLM 統合・extra・関連テスト/config を撤去。starlette/uvicorn を直接依存化。lock 再生成（36 行）
- [x] CLI `gateway` サブコマンド / codex・claude_code 設定 helper 更新 / Dockerfile・compose・CI 更新
- [x] unit+eval **137 passed** ＋ live gateway（Responses/Anthropic × stream/非stream・0 漏えい）、
      ruff + mypy --strict クリーン

## 9. 受け入れ基準（doc §38 の 20 項目）— 達成状況

最重要「Codex→LiteLLM→モック送信の最終リクエストに機密情報が一切含まれない」をライブ統合
テストで確認。主要項目を全 Phase で充足: §38-1（最終送信に機密なし）・2（alias 復元）・
6（SSE 分割復元）・7（tool 引数 JSON）・8（構造キー不変）・11（ログに秘密なし）・
17（無効化で素の LiteLLM）・18（薄いアダプター）・19（互換性文書）・20（docker compose）。

## 10. MVP 非対応（doc §34）

画像/音声/バイナリ/圧縮内の機密、Base64 再帰解析、全言語 AST、WebSocket 版 Responses、
Hosted tool への実値受け渡し、未登録氏名/住所の完全検出。
※検知できた未対応データは黙って送らず block する。
