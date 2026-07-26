# CLAUDE.md

Claude Code 用の入口ファイル。**運用ルールと現行アーキテクチャは [`AGENTS.md`](AGENTS.md)** を参照。
ここでは Claude Code 固有の注意点だけを補足する。

## まず読むもの
1. [`AGENTS.md`](AGENTS.md) — 不変ルール・現行アーキテクチャ・作法。**これが現行の指示。**
2. [`doc/07-Remediation-Status.md`](doc/07-Remediation-Status.md) — 何が `done` で何が `partial` か。
   着手前に必ず読む。
3. [`docs/adr/`](docs/adr/) — 手段についての最新の決定。
4. [`doc/00-First-Order.md`](doc/00-First-Order.md) — **初期命令（当時のブリーフ）**。
   要件の背景を知るために読む。設計が変わった箇所（特に LiteLLM 撤廃 = ADR-0006）については
   doc/00 より新しい ADR が優先する。

矛盾時の優先順位は AGENTS.md と同じ: **不変条件 ＞ 最新 ADR ＞ doc/00 の手段記述**。

## 絶対ルール（詳細は AGENTS.md §2）
- 元の機密情報を外部 LLM へ送らない / セッション・テナント・ユーザーを混ぜない / 構造を壊さない。
- 不明・障害時は **fail-closed**。**一部だけ検査して成功を返すことは fail-closed ではない。**
- **ログ・スクラッチ・コミットに秘密値、復号鍵、平文対応表を残さない。**
- テストに実在人物・実際の秘密を使わない（合成データのみ）。実 provider へ送信しない。

## この環境の状況（2026-07-26 時点）
- git は `main` で運用中（コミット履歴あり）。`.gitignore` 設定済み。
- Python は `.venv`（3.12）。依存管理は **pip + venv**（`uv` は不採用 — ADR-0002）。
  用途別 lock: `requirements.lock` / `-dev` / `-presidio` / `-ner`。
- Docker 利用可。`docker compose` の Redis 構成は overlay 指定が必要
  （`-f docker-compose.yml -f docker-compose.redis.yml`）。
- 任意依存の Presidio / 日本語 NER はインストール済みで、既定は OFF。

## 作業原則
- 着手前に `doc/07-Remediation-Status.md` で現状を確認する。作業単位ごとに、
  動くコードとテスト結果を残して終える。
- 置いた前提は README か `docs/adr/` に明記する。**コードから ADR を参照するなら実際に書く。**
- 破壊的操作・外部送信・依存追加・大きな設計変更は事前にユーザー確認。コミット/push は依頼時のみ。
- ユーザーとのやり取りは日本語。
