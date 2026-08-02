# Windows x64 source gate検証記録

実行日: 2026-08-02

この文書はWindows native source targetの途中証跡です。Windows対応完了または実データ利用許可を
示すものではありません。

## 対象

- tested commit: `42602f6703957a63b9bdef4f28a9c48662c8697d`（実CLI隔離E2E）
- OS: Windows 11 x64 build 26200.8875
- filesystem: local fixed NTFS
- shell: cmd.exe
- Python: CPython 3.12.10 x64
- Torch: 2.13.0
- Codex CLI: 0.146.0
- Claude Code: 2.1.220
- compiler: Visual Studioなし

operatorとは別のlocal standard user `SecurityMaskerGate`を作り、そのuser profileへsource archive、
Python、dependency、固定NER model、両CLIを配置しました。operatorのCLI設定や認証情報は共有して
いません。

## Source pre-release結果

専用userで`scripts\test-setup.cmd`と`scripts\release-check.cmd`を実行しました。

| gate | 結果 |
|---|---|
| Windows wheel-only setup | 成功 |
| Visual Studio非依存 | 成功 |
| 固定NER model 6 artifactのsize／SHA-256検証とlocal load | 成功 |
| ruff | 成功 |
| mypy strict | 73 source files成功 |
| unit／evaluation | 721件成功、5件skip、warning 1件 |
| mock upstream実process Gateway E2E | 4件成功 |
| Windows native process test | 3件成功 |

その後の実CLI harness修正を含むoperator側最新treeでは、unit／evaluation 725件成功、5件skip、
mock upstream E2E 4件成功、Windows native process test 3件成功を再確認しました。warningは既知の
Starlette TestClient deprecation 1件です。

## 外向き通信遮断

UAC昇格したoperatorのcmdから、専用user SIDにだけ適用されるPersistentStore outbound block ruleを
IPv4／IPv6各1件作成しました。専用user自身が次をActiveStoreから検査し、全条件が一致した後だけ
実CLIを起動しました。

- current userがadministrator groupに属さない
- ruleのLocalUser SIDがcurrent user SIDと一致する
- IPv4はloopback `127.0.0.0/8`以外、IPv6は`::1`以外をblockする
- protocol、profile、interface typeがすべて対象でactionがBlockである
- numeric IPv4／IPv6外部canaryへの接続が拒否される
- loopbackのGatewayとmock upstreamへ接続できる

検査結果は次のとおりです。

```json
{"isolated":true,"current_user_sid":"<redacted-dedicated-user-sid>","rule_names":["SecurityMaskerCliEgressGate-v4","SecurityMaskerCliEgressGate-v6"]}
```

## 実CLI E2E結果

`scripts\windows-cli-e2e.cmd`から両CLIを必須として実行しました。

| gate | 結果 |
|---|---|
| 実Codex CLI／Responses WebSocket | 成功 |
| 実Claude Code CLI／Anthropic Messages | 成功 |
| local mockの最終payloadから合成原文を排除 | 成功 |
| CLI出力で合成原文を復元 | 成功 |
| CLI出力へのalias残存なし | 成功 |
| test件数／wall time | 2件成功／42.11秒 |

Claude Codeは空の作業directoryで起動し、settings source、slash command、built-in tool、session永続化を
無効にしました。これによりrepositoryやuser profileの周辺contextをrequestへ混ぜず、固定system promptと
合成test promptだけをprotocol surrogateとして検証しています。

## 残件

Windows native sourceを対応済みと判断する前に、statusに記載した追加のnative negative matrix、利用・
運用手順、standard userのclean-machine source archive gateと新しい対応判断が必要です。それまでは
Windowsで実際の機密情報を扱いません。
