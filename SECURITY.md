# セキュリティ

SecurityMasker は、元の機密情報を信頼領域から外部 LLM へ流出させないための
**セキュリティ境界**です。本書では、保証範囲、制約、堅牢化指針を示します
（`doc/00-First-Order.md` §33）。

## 保証と実現方法

- **元の機密情報を外部 LLM へ送りません。** Gateway は転送前にリクエストを
  マスクし、送信直前の最終ペイロードとヘッダーを再検査します。登録済みまたは
  高確度の機密情報が残っていれば fail-closed で拒否します
  （`engine._verify_no_leak`、`engine.assert_no_leak_in_payload`、§18）。
  `tests/integration/test_live_gateway.py` は、ローカルの mock upstream に対して
  実際にプロセス外へ出た内容を検査する E2E テストです。
- **セッションとテナントを分離します。** alias はセッションごとの CSPRNG 鍵を
  用いた HMAC で生成し、復元対象もそのセッションが発行した alias に限定します
  （§7）。Redis キーはテナント名前空間に分離し、プロセスの master key で
  暗号化します（§8）。
- **構造を保持します。** 変換対象は文字列値だけです。ID、type、tool 名、
  JSON Schema のキーは変更しません（§16）。
- **既定で fail-closed です。** 検出器、暗号、セッション、ストリームの障害時は、
  元データを転送せずリクエストを拒否します（§26）。

## 秘密情報と鍵

- API key と private key の既定は `env_reference` です。
  `${SECURITYMASKER_SECRET_...}` に置換し、応答で実値へは**復元しません**
  （§10、§27）。これにより shell history や process list への残存を防ぎます。
- セッション鍵は `secrets.token_bytes` で生成し、session ID からは導出しません。
  Redis store は全データを `SECURITYMASKER_MASTER_KEY`（base64 の32 bytes）で
  暗号化します。この鍵は環境変数または Secret Manager から注入し、Redis には
  保存しないでください（§8）。

## ログ（§25）

- 元の機密値、復号済み対応表、鍵、認証ヘッダー、prompt／response の全文、
  復元済み tool arguments は**一切ログへ出しません**。
- ログと監査の対象は、安全なフィールド（entity type、件数、処理時間、不可逆な
  fingerprint）だけです（`securitymasker.metrics`、`securitymasker.logging`）。
- 経路上のどこでも request／response body のログを有効にしないでください。
  Gateway 自身の debug log、前段の reverse proxy、APM agent も対象です。
  Gateway はマスク前の原文と復元後の原文を扱うため、body log は本製品が外部に
  出さないための情報そのものを複製します。
- 外部ログ／telemetry 連携は、request body を転送しないと確認できるまで
  無効のままにします。

## 既知の制約（§34）

画像・音声内のテキスト、binary／圧縮／暗号化ファイル、再帰的な Base64、あらゆる
言語の完全な AST 解析、WebSocket 版 Responses API、hosted tool への実値受け渡し、
未登録の日本語氏名・住所の100%検出、モデルが変形した alias からの復元は対象外です。
検出できた場合は黙って転送せず拒否します。

Python ではメモリの完全消去を保証できません。Gateway はローカルまたは信頼済み
network で動かし、swap を制限し、core dump を無効化し、container filesystem を
read-only にし、管理 port を public interface に公開しないでください（§33）。

## 脆弱性の報告

本プロジェクトは reference implementation です。脆弱性は公開前に保守担当へ
非公開で報告してください。報告には実在する秘密情報を含めないでください。
