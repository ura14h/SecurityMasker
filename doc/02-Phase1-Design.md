# 02-Phase1-Design — コア MVP 設計メモ

正典 [`00-First-Order.md`](00-First-Order.md) の Phase 1（§37）。非ストリームの mask/unmask を
壊さず・漏らさず・冪等に行うコアを作る。ストリーム／プロトコル対応は Phase 2 以降。

作成日: 2026-07-24

## 決定事項（ユーザー確認済み）

- **複数表記の扱い**: 既定は「**表記ごとに別 alias**」。
  → alias fingerprint の入力は「元の表記文字列」をキーにする（正規化前の surface form）。
  設定 `merge_surface_forms`（既定 false）で「正規化後にまとめる」へ切替可能（§12）。
- **正規化**: 既定 **NFKC**（検出用途）。復元時は元の表記を保持（§12・§14.4）。設定 `normalization: nfkc|nfc`。

## モジュール実装順（依存の浅い順）

1. `models.py` — `DetectionResult` / `AliasMapping` / `MaskingSession` /
   `MaskingPolicyDecision`、および `ContextKind` / `ReplacementProfile` / `RestorePolicy` / `EntityType`。
2. `errors.py` — 例外階層。既定 fail-closed（§26）。`SecurityMaskerError` 基底。
3. `normalization.py` — NFKC/NFC 正規化＋**オフセットマッピング**（正規化位置→原文位置）。
4. `sessions/crypto.py` — `SessionCrypto`: セッション鍵（`secrets.token_bytes`）、
   `HMAC(session_index_key, normalized+type+profile or surface+type+profile)`、AES-GCM 暗復号。
5. `aliases/profiles.py` — プロファイル別の alias 整形（prose_identifier / hostname / email /
   ipv4 / ipv6 / uuid / numeric / file_path / url / environment_reference）。
6. `aliases/factory.py` — fingerprint→短縮 alias 生成、衝突検出→長さ延長、冪等・並列安全。
7. `sessions/models.py` `sessions/store.py`(Protocol) `sessions/memory.py`(InMemory, TTL, ロック)。
8. `detectors/base.py` — `SensitiveDataDetector` Protocol、`DetectionContext`。
9. `detectors/dictionary.py`（Aho–Corasick 風、長一致優先）/ `regex.py` / `secret_patterns.py`。
10. `policy.py` — 重複範囲統合（長一致・優先度・信頼度・型）、既存 alias 保護（二重マスク防止）、
    `MaskingPolicyDecision`。
11. `engine.py` — mask: 正規化→検出→統合→ポリシー→alias→構造保持置換→送信前再スキャン。
    unmask: セッションで生成した alias のみ復元。
12. `config.py` — pydantic-settings ＋ YAML 辞書ローダー（`value_from_env`、平文非許可、起動時検証）。
13. `cli.py` — argparse（`config validate` / `entities test` / `sessions ...` / `doctor`）。

## Phase 1 の受け入れ（§30.1・§38 の該当項目）

- 同一セッション=同 alias / 別セッション=別 alias / alias→原本復元 / 衝突検出
- HMAC 入力がエンティティ型で分離 / 復元ポリシー適用（literal・env_reference・redacted・block）
- NFKC・全角半角・日本語スペース / 長一致優先 / 重複範囲解決 / 既存 alias 二重マスクなし
- Secret（API キー・JWT・PEM）は既定 env_reference / JSON 文字列エスケープ安全
- TTL / セッション削除 / AES-GCM 改ざん検知 / 送信前再スキャンで漏えい時 fail-closed
- **leakage: 置換後テキストに元の機密値が存在しない**

## Phase 1 で扱わない（後続）

- SSE ストリーミング復元（Phase 2）／ツール引数バッファ（Phase 2）
- OpenAI/Anthropic プロトコル walker の本格実装（Phase 2/3、MVP は素テキスト＋簡易 JSON walker）
- Presidio / 日本語 NER / 住所・電話・マイナンバー Recognizer（Phase 4）
- Redis / マルチテナント（Phase 5）
