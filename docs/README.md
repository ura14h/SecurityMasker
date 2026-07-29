# 文書案内

初めて使う場合は[導入ガイド](getting-started.md)から始めてください。このページは文書の
ファイル種別ではなく、「何をしたいか」から入口を選べるように案内します。

## 初めて使う

1. [導入ガイド](getting-started.md) — setup、初期化、合成値での確認、client接続、終了
2. [辞書のカスタマイズ](guides/customize-dictionary.md) — 会社名、人名、顧客名、project名の登録
3. [日常的な使い方](operations/daily-use.md) — 起動、readiness確認、終了

実際の機密情報を扱う前に[安全な使い方](security/safe-use.md)のチェックを完了してください。

## カスタマイズする

- [辞書のカスタマイズ](guides/customize-dictionary.md) — 通常の固有名詞と表記揺れ
- [Credentialを環境変数から登録する](guides/credentials.md) — API key、password、秘密鍵
- [独自patternを追加する](guides/custom-patterns.md) — ticket番号や顧客ID
- [Trusted local toolを設定する](guides/trusted-tools.md) — tool argumentの復元
- [CodexとClaude Codeを同時に使う](guides/use-both-clients.md) — mode別の2 process運用
- [通信経路を詳しく検証する](guides/verify-routing.md) — alias受信とresponse復元の高度な診断

## 運用する

- [日常的な使い方](operations/daily-use.md) — 起動、変更反映、終了
- [Backupとrestore](operations/backup-restore.md) — config、辞書、DB、key
- [Source版を更新する](operations/update.md) — backup、setup、確認、rollback
- [アンインストールと設定の復旧](operations/uninstall.md) — client設定とlocal data
- [トラブルシューティング](operations/troubleshooting.md) — config、権限、model、port、routing

## 仕組みを理解する

- [SecurityMaskerの仕組み](concepts/how-it-works.md) — mask、restore、session、認証
- [検出層と日本語PII](security/detection.md) — 辞書、決定論的detector、日本語NER

## 安全性を確認する

- [安全な使い方](security/safe-use.md) — 利用者向けの保護範囲、制限、事前確認
- [Security policy](../SECURITY.md) — supported scopeと脆弱性報告
- [Threat model](security/threat-model.md) — reviewer向けの信頼境界、脅威、残存risk
- [日本語NER modelの出典](reference/model-licenses.md) — 固定modelと再配布条件
- [Third-party notices](../THIRD_PARTY_NOTICES.md) — project licenseと第三者component

## 仕様を調べる

- [CLIリファレンス](reference/cli.md) — 全command、option、終了code
- [設定リファレンス](reference/configuration.md) — config、辞書、detector、policy、tool trust
- [対応環境](reference/compatibility.md) — platform、client、protocol、binary
- [Changelog](../CHANGELOG.md) — 公開版ごとの差分

## 開発する

- [Architecture](development/architecture.md) — 製品境界と内部component
- [コード読解ガイド](development/codebase-guide.md) — sourceの入口、module、testの辿り方
- [Testing](development/testing.md) — local testとtest data
- [Release gate](development/release.md) — source／binaryの公開合格条件
- [開発・リリース状況](development/status.md) — 現在のdone、partial、blocked

## 対応外の評価手順

[Windows上のLinux環境で評価する](unsupported/windows-evaluation.md)はtechnical spikeです。
Windows nativeもWSL2／Docker Desktop経路も対応環境ではありません。実データを扱わないでください。

## ADR

ADRは採用時点の比較、却下理由、後続判断による変更を残す履歴です。現在の操作や仕様は、上記の
利用者文書、reference、architectureを正とし、ADRを操作手順として使わないでください。

現行製品に直接関係する判断は[ADR-0012](adr/0012-renew-package-design.md)以降、個別の暗号、
alias、protocol、model供給網の判断は[`docs/adr/`](adr/)にあります。
