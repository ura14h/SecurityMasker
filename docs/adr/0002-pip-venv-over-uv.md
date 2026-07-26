# ADR-0002: 依存管理は pip + venv を採用（uv ではなく）

- 状態：採用
- 日付：2026-07-24

## 背景

初期案の`uv`に対し、追加toolを要求しないpip + venvをownerが選択した。

## 決定

- Homebrew `python@3.12` を導入し、`.venv`（Python 3.12.13）を作成。
- 依存は `pyproject.toml` に宣言し、`pip install -e ".[dev]"` 等で導入。
- 再現性のため `pip freeze` を `requirements.lock` として固定・コミットする（§36 の lock 要件を満たす代替）。

## 検討した代替案

- **uv**: §36 の第一推奨だがユーザー環境未導入・明示的に非選択。
- **poetry / pdm**: 追加ツール導入が必要で pip+venv より重い。

## 影響

- lock は `requirements.lock`（`pip freeze`）で管理。extras 追加時は再 freeze が必要。
- local setup/release gateでも同じ手順（venv + pip install + lock検証）を再現する。
