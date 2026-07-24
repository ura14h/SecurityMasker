# ADR-0002: 依存管理は pip + venv を採用（uv ではなく）

- Status: Accepted
- Date: 2026-07-24

## Context

`doc/00-First-Order.md` §36 は `uv` を推奨するが「合理的な理由があれば変更可」と明記。
本環境には `uv` 未導入・システム Python 3.9.6 のみ。ユーザーは pip + venv を選択。

## Decision

- Homebrew `python@3.12` を導入し、`.venv`（Python 3.12.13）を作成。
- 依存は `pyproject.toml` に宣言し、`pip install -e ".[dev]"` 等で導入。
- 再現性のため `pip freeze` を `requirements.lock` として固定・コミットする（§36 の lock 要件を満たす代替）。

## Alternatives

- **uv**: §36 の第一推奨だがユーザー環境未導入・明示的に非選択。
- **poetry / pdm**: 追加ツール導入が必要で pip+venv より重い。

## Consequences

- lock は `requirements.lock`（`pip freeze`）で管理。extras 追加時は再 freeze が必要。
- CI でも同じ手順（venv + pip install + lock 検証）を再現する。
