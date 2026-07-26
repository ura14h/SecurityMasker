# ADR-0004: Presidio は in-process Python ライブラリとして利用

- 状態：撤回（[ADR-0012](0012-renew-package-design.md)）
- 日付：2026-07-24（2026-07-25更新）
- 関連: `doc/00-First-Order.md` §13, §14、ADR-0012

> この ADR は従来構成での判断記録として残す。ADR-0012 により Presidio は標準製品から
> 撤去し、日本語 NER を標準搭載・既定 ON とする。移行完了までは実装が repository に
> 残るが、新規機能を Presidio 前提で設計しない。

## 背景

§13 は Presidio Analyzer を「LiteLLM と同一 Python 環境で利用」するか「公式
Analyzer コンテナへ HTTP 接続」するかを選び、理由を ADR 化するよう求める。
Presidio は主に**検出器**として使い、alias 生成・可逆マッピング・復元・構造保持は
SecurityMasker 独自実装で行う。

## 決定

MVP〜Phase 4 では **in-process（`presidio-analyzer` ライブラリ）** を採用する。

理由:

- LiteLLM と同一プロセスのため、検出のたびの HTTP 往復レイテンシがない（§32）。
- ネットワーク境界を増やさず、機密テキストを別サービスへ渡さない（§5 信頼境界）。
- 運用対象コンテナが 1 つ減り保守が軽い（§13 の MVP 方針）。

`SensitiveDataDetector` インターフェース（§15）の背後に閉じ込め、後から HTTP Analyzer
アダプターへ差し替え可能にする。Presidio Anonymizer コンテナは独自置換のため不要。

## 検討した代替案

- **HTTP Analyzer コンテナ**: 言語ランタイム分離やスケール独立が利点だが、機密テキストの
  越境・レイテンシ・運用コスト増。将来のスケール要件が出た時点でアダプター追加で対応。

## 影響

- spaCy 等の重い依存が LiteLLM プロセスへ入る（`presidio` extra として分離）。
- 日本語 NER モデルは設定可能（§14.1、ハードコードしない）。
- Detector ごとにタイムアウト／サーキットブレーカーを設ける（§32）。
