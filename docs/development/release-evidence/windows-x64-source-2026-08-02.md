# Windows x64 source gate検証記録

> このevidenceはmode別`%LOCALAPPDATA%`を使った当時のlayoutに対する記録である。
> [ADR-0024](../../adr/0024-unify-source-adjacent-layout.md)で採用したlauncher隣接layoutのWindows native
> 検証を示すものではない。

実行日: 2026-08-02

この文書はWindows native source targetの検証証跡です。対応範囲は別の最新ADRと
[compatibility](../../reference/compatibility.md)で判断します。

## 対象

- tested commit: `42602f6703957a63b9bdef4f28a9c48662c8697d`（初回実CLI隔離E2E）
- fresh source archive commit: `f13a3922f16233ea602e6b6170cf9edd88a63587`
- RDP orchestration commit: `84f085e05a8f0815e5b7ea057c6dc2076ebe1d80`
- OS: Windows 11 x64 build 26200.8875
- filesystem: local fixed NTFS
- shell: cmd.exe
- Python: CPython 3.12.10 x64
- Torch: 2.13.0
- Codex CLI: 0.146.0
- Claude Code: 2.1.220
- compiler: Visual Studioなし

operatorとは別の専用local standard userを作り、そのuser profileへsource archive、Python、
dependency、固定NER model、両CLIを配置しました。operatorのCLI設定や認証情報は共有して
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

## Fresh source archive結果

固定名`SecurityMaskerTester`を新規作成し、checksum
`577440d5c3198f64ae759e5bec2710312eed267b82a4a3ac7cab365940df48cc`のsource archiveを
local fixed NTFSへ展開しました。RDP sessionのoperator接続を維持するため、Windows標準の
`runas /profile`で固定userのprofileとtokenを使う別cmdを起動しました。

preflightで固定standard user、`.git`／`.venv`と既存製品dataがないfresh directory、local fixed NTFS、
reparse point非使用を確認しました。その後、Python 3.12 wheel-only setup、固定NER model load、両modeの
init／doctor／preview／client configとrelease gateを一巡しました。

| gate | 結果 |
|---|---|
| ruff | 成功 |
| mypy strict | 73 source files成功 |
| unit／evaluation | 742件成功、5件skip、warning 1件／71.02秒 |
| mock upstream実process Gateway E2E | 4件成功／10.54秒 |
| Windows native process test | 3件成功／1.15秒 |

最初のfresh実行では、LF-only cmd wrapper testが日本語cmd出力をUTF-8固定でdecodeし、製品外のtest
harnessで失敗しました。localeに依存するerror textの読取りをやめ、偽PowerShell gateへの`Remove`
dispatchをmarker fileで直接確認するtestへ修正してから、上記fresh gateを再実行しました。

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
| test件数／wall time | 初回2件成功／42.11秒、fresh archive再確認2件成功／41.34秒 |

Claude Codeは空の作業directoryで起動し、settings source、slash command、built-in tool、session永続化を
無効にしました。これによりrepositoryやuser profileの周辺contextをrequestへ混ぜず、固定system promptと
合成test promptだけをprotocol surrogateとして検証しています。

## 後片付け

fresh archiveの実CLI E2E完了後、固定userの別cmdを閉じ、UAC昇格したoperatorのcmdから
`scripts\windows-test-user.cmd remove`を実行しました。この操作で専用IPv4／IPv6 rule、local user、
Windows profileを完全削除しました。削除後にlocal user、`C:\Users\SecurityMaskerTester`、ActiveStore
ruleが存在しないことを確認しました。合成dataと公開実行ファイルだけを含むPublic bootstrap資材は、
今後の再検証用に残しています。

## 追加native negative gate

fresh archive gate後の開発treeで、次のWindows境界を追加確認しました。

- `7941ad098254e706794d9527b7c0b9255c07c266`: UNC pathをvolume access前に拒否し、
  `GetDriveTypeW`のremovable／remote値をlocal fixed driveではないとして拒否
- `68165d0e6183cc4c8e41a6585595adf9e293255f`: 存在しない合成SIDのallow ACEを設定した
  実DACLをunexpected principalとして拒否
- `ba945378b0d164e45e208d0d82e2220b7899d2c2`: UAC昇格したoperatorがProgramDataへ
  owner Administratorsの合成fileを作成し、製品のowner検査が拒否した後にfixtureを削除

wrong-owner gateの実測出力は次のとおりです。

```text
created synthetic wrong-owner fixture
{"wrong_owner_rejected":true}
removed synthetic wrong-owner fixture
```

終了後、固定fixture directoryが存在しないことを読み取り確認しました。

`77c45476a9611f93df836a8033790b30db8e7c09`でread-onlyなremovable drive gateを追加し、実removable
mediaを接続したstandard userのcmd.exeから実行しました。Windows APIがreadyな媒体をremovableと
分類したことを確認したうえで、製品のvolume境界がlocal fixed driveではないとして拒否しました。
媒体上にfixtureやfileは作成していません。実測出力は次のとおりです。

```json
{"removable_drive_rejected":true}
```

## 最終gateと対応判断

最新treeでruff、mypy strict 73 source files、unit／evaluation 753件（5 skip、既知warning 1件）、
mock Gateway E2E 4件、Windows native process 3件を再実行し、すべて成功しました。ADR-0013と
ADR-0021の再検討条件は[ADR-0023](../../adr/0023-support-windows-native-source.md)で監査し、限定した
Windows native source targetを対応範囲へ加えました。
