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

## 3. Phase 0: 調査と互換性固定（次に着手）

- [ ] LiteLLM の最新安定版を Web 確認 → バージョン 1 点固定
- [ ] Presidio (`presidio-analyzer`) / OpenAI SDK / Anthropic SDK のバージョン固定
- [ ] Python 3.12 venv 構築、上記を pin して install、lock 相当を生成
- [ ] LiteLLM の custom callback / guardrail hook の**正確なシグネチャ**をソースで確認
      （`async_pre_call_hook` / `async_post_call_success_hook` /
       `async_post_call_streaming_iterator_hook` / `async_post_call_failure_hook` を想定）
- [ ] hook シグネチャを固定するテストを追加（アップデート検知用）
- [ ] `/v1/responses`（OpenAI Responses）と `/v1/messages`（Anthropic）の実 SSE 構造を fixture 化
- [ ] LiteLLM logging が pre-call hook より前に raw request を保存しないか検証
- [ ] `docs/compatibility.md` 作成（対応バージョン・確認済み hook）
- [ ] ADR 作成: (a) Presidio インプロセス採用 (b) pip+venv 採用 (c) alias=HMAC+AES-GCM (d) セッションストア抽象化

**完了条件**: 固定バージョンで LiteLLM Proxy が起動し、no-op の `SecurityMaskerCallback` が hook に載ることをテストで確認できる。

## 4. Phase 1: コア MVP

辞書 / Regex / Secret detector、インメモリセッション、HMAC alias、AES-GCM mapping、
replacement profiles、非ストリーム mask/unmask、fail-closed、CLI、unit test。
**受け入れ**: 同一セッション=同 alias / 別セッション=別 alias / 復元可 / 衝突検出 / leakage test。

## 5. Phase 2: Codex 対応

OpenAI Responses adapter、SSE parser、streaming text 復元（carry buffer / 全分割位置）、
tool 引数バッファ（複数 delta → parse → 復元 → 再 serialize）、mock upstream、Codex E2E fixture。

## 6. Phase 3: Claude Code 対応

Anthropic Messages adapter、content block 処理、tool use/result、input JSON delta、
beta header 透過、unknown block 透過、Claude Code E2E fixture。

## 7. Phase 4: 日本語 PII

Presidio adapter、JP phone / postal / My Number（チェックディジット）、DOB 文脈、
Japanese NER adapter（モデル差し替え可）、composite address detector、評価コーパス（precision/recall/F1）。

## 8. Phase 5: 運用強化

Redis store、multi-tenant 分離、暗号化永続、metrics、audit log、Docker hardening、
compatibility CI、performance benchmark。

## 9. 受け入れ基準（doc §38 の 20 項目）

最終的に doc §38 の 20 項目をすべて満たす。特に:
「Codex→LiteLLM→モック送信の最終リクエストに機密情報が一切含まれない」を最重要とする。

## 10. MVP 非対応（doc §34）

画像/音声/バイナリ/圧縮内の機密、Base64 再帰解析、全言語 AST、WebSocket 版 Responses、
Hosted tool への実値受け渡し、未登録氏名/住所の完全検出。
※検知できた未対応データは黙って送らず block する。
