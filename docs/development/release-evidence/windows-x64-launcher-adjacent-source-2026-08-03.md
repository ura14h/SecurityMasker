# Windows x64 launcher隣接source gate検証記録

実行日: 2026-08-03

この文書は、[ADR-0024](../../adr/0024-unify-source-adjacent-layout.md)で採用した
launcher隣接layoutのWindows native source gateに対するevidenceです。

## 対象

- base commit: `ce08ae1d0dbd00103cd36d309b636b934e651325`に1.0.0確定変更を加えたtree
- OS: Windows 11 Pro x64 build 26200
- Python: CPython 3.12.10 x64
- filesystem: local fixed NTFS
- source path: drive-letter path、ancestorにreparse pointなし
- provider通信: なし。mock upstreamだけを使用

## 実行結果

Windows preflightでOS、architecture、Python、filesystem、source pathをread-only確認した後、
`scripts\release-check.cmd`を1回起動しました。ruffとmypy strict 73 source filesは成功し、
unit／evaluationは758件成功、5件skipの後、Windows DACL test 1件が失敗して停止しました。

失敗原因は製品実装ではなく、test fixtureがsource file作成後に非継承のprotected DACLを
source rootへ設定し、source fileを初期化前から読取不能にしていたことでした。fixtureを
継承可能なsource root DACLから作るよう修正し、rootとsource fileのSDDLがinit前後で
完全一致するassertionを追加しました。製品実装は変更していません。

指定された再実行方針に従い、source gate全体は再起動せず、修正の影響を受けるDACL testだけを
1回再実行して成功しました。最初のgateが到達しなかった後続stageも、それぞれ1回実行しました。

| gate | 結果 |
|---|---|
| ruff | 成功 |
| mypy strict | 73 source files成功 |
| unit／evaluation | 759件成功、5件skip、warning 1件 |
| mock upstream実process Gateway E2E | 4件成功／10.65秒 |
| Windows native process test | 3件成功／1.01秒 |

## Windows固有の確認

- launcher隣接directoryへの既定init
- source fileの内容とSDDL非変更、source rootのSDDL非変更
- config、辞書、state directory、master key、SQLite DB／lockのprotected DACL
- Everyone、deny、NULL、未知SID、未対応ACE、継承済みpermissive DACLの拒否
- wrong owner、junction、UNC、subst drive、removable／remote drive typeの拒否
- ChatGPT／Claudeのmode、config、stateの分離
- 両modeのmock Gatewayにおける合成原文の上流非送信とresponse復元
- 別process writerの拒否、graceful close／forced termination後の復旧、DB／key backup pairの復旧

wrong ownerの異principal実fileによる追加gateは、旧layout検証時の
[Windows x64 source evidence](windows-x64-source-2026-08-02.md)で実測済みです。今回の
launcher隣接変更はowner判定実装を変更していないため、同じ管理者gateは再実行していません。

## 結論

Windows 11 x64、CPython 3.12 x64、local fixed NTFSで、launcher隣接layoutのnative source gateは
成功です。このevidence後の変更は文書に限定し、Windows gateは再実行しません。
