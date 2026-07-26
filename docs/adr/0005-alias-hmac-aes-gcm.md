# ADR-0005: alias は per-session HMAC で決定し、原本は AES-GCM で暗号化

- 状態：採用
- 日付：2026-07-24
- 関連: [現行architecture](../design/architecture.md)

## 背景

同一セッション内で一つの機密値に一つの安定 alias を割り当て、別セッションでは別 alias に
する必要がある。平文の機密をstore keyやログに使ってはならない。

## 決定

- セッション鍵はセッション作成時に暗号学的乱数（`secrets.token_bytes`）で生成する。
  セッション ID から鍵を導出しない（§7）。
- 指紋: `secret_index = HMAC(session_index_key, normalized_secret + entity_type + profile)`。
  素の SHA-256 単独で alias を決めない（§7）。
- マッピング: `alias -> AES-GCM(original)`（認証付き暗号、§8）。改ざんは復号時に検知。
- alias から原本を推測不可能にし、短縮 alias の衝突は検出して長さを延長する（§7）。

## 検討した代替案

- **決定論的 SHA-256 alias**: セッション横断で同一 alias となり、プロバイダーによる
  名寄せを許す。§6・§33 に反する。却下。
- **可逆暗号を alias 本体に埋め込む**: alias が長大化し構造保持プロファイル（§9）と両立し難い。却下。

## 影響

- 鍵は Redis に保存しない（§8）。プロセスメモリ／Secret Manager で管理。
- Python では完全なメモリゼロ化を保証しにくい旨を SECURITY.md に明記する（§33）。
- alias 生成は並列でも決定論的・冪等（§7、§30.4 で検証）。
