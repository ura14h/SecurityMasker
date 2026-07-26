# ADR-0003: CLI は標準ライブラリ argparse で実装

- 状態：採用
- 日付：2026-07-24

## 背景

`securitymasker` CLI（§7, §12）を実装するにあたり typer を検討したが、
`litellm[proxy]==1.93.0` が `click==8.4.2` を推移的に固定する一方、typer 0.15.4 は
`click<8.2` を要求し、両者を同一環境へ入れると依存衝突が発生した。

## 決定

CLI は Python 標準ライブラリ `argparse` で実装する。追加依存ゼロで litellm のピンと
衝突せず、コア package を依存軽量に保てる（§40-12 保守性優先）。

## 検討した代替案

- **typer / click 直接利用**: litellm の click ピンに追従する必要があり、litellm を
  入れない構成（コアのみ）との整合が崩れる。却下。

## 影響

- CLI サブコマンド（`run` / `sessions` / `entities` / `config` / `doctor`）を argparse で構築。
- リッチな補完・色付けは自前で最小限に留める。
