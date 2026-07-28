# 文書案内

SecurityMaskerの文書は、利用、設計、実装の読解、release判断、過去の意思決定に分けています。
現行の仕様を確認するときはREADME、architecture、development statusを優先し、ADRは判断理由と
変更履歴として参照してください。

## 利用する

1. [導入ガイド](user/getting-started.md) — source setup、初期化、client接続、終了
2. [CLIリファレンス](user/cli-reference.md) — 全command、option、終了code
3. [設定リファレンス](user/configuration.md) — config、辞書、detector、tool trust
4. [トラブルシューティング](user/troubleshooting.md) — 起動、routing、復元、権限
5. [Windows番外編](user/getting-started-windows.md) — 非対応環境でのLinux-hosted評価

実際の機密情報を扱う前に、repository rootの[Security policy](../SECURITY.md)と
[脅威モデル](design/threat-model.md)を確認してください。

## 仕組みを理解する

- [Architecture](design/architecture.md) — 製品境界、request処理、session、認証
- [コード読解ガイド](development/codebase-guide.md) — sourceの入口、module対応、testの辿り方
- [日本固有PII](design/japanese-pii.md) — 日本向け決定論的detector
- [日本語NER modelの出典](model-licenses.md) — 固定modelと再配布判断

## 現在の状態とrelease

- [開発・リリース状況](development/status.md) — done、partial、blockedの正
- [Testing](development/testing.md) — local testとrelease gate
- [Compatibility](development/compatibility.md) — client/protocolの確認範囲
- [Changelog](../CHANGELOG.md) — 公開版ごとの差分

## ADR

ADRは採用時点の比較、却下理由、後続判断による変更を残す履歴文書です。古い構成を説明するADRを
現在の操作手順として使わないでください。

現行製品に直接関係する入口は次です。

- [ADR-0012](adr/0012-renew-package-design.md) — 現行package設計
- [ADR-0013](adr/0013-reject-best-effort-windows-support.md) — Windowsをbest-effort対応しない
- [ADR-0014](adr/0014-reject-non-python-port-for-current-product.md) — Python実装の維持
- [ADR-0015](adr/0015-evaluate-windows-linux-hosted-deployments.md) — Windows上のLinux-hosted評価
- [ADR-0016](adr/0016-reset-config-schema-version.md) — 現行configをschema v1とする

個別の暗号、alias、protocol、model供給網の判断は[`docs/adr/`](adr/)にあります。各ADR冒頭の
状態と置換先を確認してください。
