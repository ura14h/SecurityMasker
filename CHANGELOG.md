# Changelog

## Unreleased

- `init -f` / `init --force`を追加し、明示したdirectoryの標準config、辞書、SQLite、master keyを
  Gateway停止中に一組として完全初期化できるように変更
- config schema v1へ`logging.level`を追加し、Gatewayのconsole logをINFO、WARNING、ERROR、DEBUGの
  製品影響別に分類
- SecurityMasker自身のlicenseをApache-2.0からMITへ変更
- project license、setup時に取得する第三者component、binary再配布条件の境界を明文化
- READMEを検索訪問者向けの入口へ改め、導入、カスタマイズ、運用、reference、security、
  development、対応外手順を目的別に再編
- PyInstaller one-file buildを、modelをlocal cacheへ分離するLite版と、固定modelを同梱するFull版へ
  分け、成果物名と`--version`でprofileを識別可能に変更
- Lite版binaryの`model-load`、model未準備時のfail-closed、取得後のoffline NER、両mode Gatewayを
  profile別gateで検証

## 0.1.0 — 2026-07-26

SecurityMaskerの最初のsource release候補です。

### 主な機能

- CodexのOpenAI ResponsesとClaude CodeのAnthropic Messagesに対応する、
  mode別のlocal可逆マスキングGateway
- buffered/streaming responseとtool argumentの復元
- user dictionary、secret/format/日本固有detector、標準日本語NERの多層検出
- session固有aliasと、AES-256-GCMで封緘したSQLite永続store
- strict v1 `securitymasker.config`、単一 `securitymasker.dict`、安全な `init`
- 外部送信しない `preview`、手動client設定生成、read-only `doctor`
- source setup、local release gate、PyInstaller one-file build/test script

### 安全側の変更

- 1 processを `chatgpt` または `claude` の一方だけへ限定
- loopback bindとsingle workerだけを製品範囲に限定
- model欠落・破損、DB/key不一致、未知protocol、検出上限、leak guard失敗をfail-closed
- provider認証を保存・logせず、対応する上流へだけ透過
- Linuxでは公式CPU版Torchを固定し、CUDA runtimeを要求しない
- model検出が同一text内の反復を一部だけ返しても、検出済み原文の完全一致を同じaliasへ揃える
- 引数なしではhelpを表示し、常駐Gatewayの起動には明示的な`gateway` commandを要求

### 対象外・既知の制限

- Web版ChatGPTやremote sessionなど、localhost Gatewayを通らない通信
- public bind、multi-user、multi-tenant、multi-worker
- 未登録の組織固有語をmodelだけで100%検出すること
- one-file binaryの一般公開。macOS arm64 spikeは成功したが、code signing、他OS、
  model weight再配布条件の確認が未完了

通常の公開候補は、model weightをrepositoryへ含めず、setup時に固定revisionを取得・検証する
source archiveです。
