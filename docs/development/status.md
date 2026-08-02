# 開発・リリース状況

最終更新: 2026-08-01

この文書を、現行構成の`done`／`partial`／`blocked`と公開範囲の正とします。`done`は実装、
製品配線、回帰test、利用・運用手順が揃った項目だけです。

## 現在の製品

| 項目 | 状態 | 残件 |
|---|---|---|
| strict config v1、単一辞書、safe init、明示的な完全初期化 | done | なし |
| `chatgpt`／`claude`の1 process・1 route | done | なし |
| OpenAI Responses／Anthropic Messages | done | buffered、SSE、Codex WebSocket、tool argumentを含め検証済み |
| 暗号化SQLiteとmode別DB/key | done | なし |
| 標準日本語NER | done（source） | 固定revision/digest、既定ON |
| preview、client snippet、read-only doctor | done | なし |
| CLI・設定reference | done | schemaとparserの網羅性testあり |
| console log level・設定閾値 | done | schema v1の`logging.level`、影響別4 level、起動・終了・異常系testあり |
| README、導入、カスタマイズ、運用導線 | done | 目的別配置、link/anchor testあり |
| 通常setupとtest setupの分離 | done | なし |
| source release candidate | done | macOS arm64とLinux arm64のsource gateに合格 |
| application `1.0.0`判断 | partial | source版だけを公開するrelease noteが必要 |
| one-file Lite版公開 | blocked | dependency再配布、署名、対象OS別clean-machine gateが未完 |
| one-file Full版公開 | blocked | Lite版の残件に加え、model weight再配布判断が未完 |
| Windows | 対応外（native source gate検証中） | 未完のnative negative matrix、clean standard userのsource archive gate、利用・運用手順、対応判断 |

## Source版

現在のapplication versionは`0.1.0`です。macOS arm64のPython 3.11／3.12とLinux arm64の
Python 3.12でsource setupを検証しています。

2026-08-01にmacOS arm64、Python 3.12、Claude Code 2.1.212からSecurityMaskerを通して
Anthropic実サーバへ接続する明示opt-in E2Eを追加しました。`haiku`を使う一つのsessionで
test専用MCP toolを4回直列実行し、5 turn、成功stream 5本、wall time 8,705.9 msを観測しました。
初回promptと全tool resultの合成PERSONが送信前にmaskされ、最終responseでは原文へ復元され、
aliasが残らないことを確認しました。実測を通じ、圧縮されたAnthropic SSEをraw UTF-8として
処理する不具合と、非2xx JSON errorをSSE processorへ渡す不具合を修正しました。

同snapshotでruff、mypy strict 71 source files、unit／evaluation 699件、mock upstreamを使う
実process Gateway E2E 4件が成功しました。実provider E2Eは通常suiteでは実行せず、固定合成値、
空の作業directory、built-in tool無効、一時product layout、既存認証の非表示利用を条件にします。
詳細は[macOS arm64 real Claude evidence](release-evidence/macos-arm64-real-claude-2026-08-01.md)に
記録しています。

同じ最終snapshotで実Codex／実OpenAIの4 tool callをWebSocketとHTTP/SSEの両方へ再実行し、
WebSocket 73,163.6 ms、HTTP 67,960.2 ms、WebSocket接続1、完了response 10を観測しました。
両transportで全tool resultのmask、最終responseの復元、alias非残存が成功しています。HTTPで
付与される`x-codex-turn-metadata`はJSONとして構造検査し、既知transport ID／timestampだけを
一般PII形式の偶発一致から除外し、他のmetadata値は引き続き全scannerへ通します。詳細は
[macOS arm64 real Codex evidence](release-evidence/macos-arm64-real-codex-2026-08-01.md)に記録しています。

2026-07-31にmacOS arm64でruff、mypy strict 71 source files、unit 684件、evaluation 3件が
成功しました。mock upstreamを使う実process Gateway E2E 4件も成功済みです。config schema v1へ
後方互換な`logging.level`を追加し、INFO、WARNING、ERROR、DEBUGの表示閾値と製品影響別のevent
対応、bind前socket確保、起動・終了logを検証しました。明示的な`init --force`による完全初期化、
稼働中state・symlink・管理外entryの拒否、切替失敗時のrollbackも検証しました。

2026-07-30にはCodex CLI 0.145.0のapp-serverから
SecurityMaskerのWebSocketを通してOpenAI実サーバへ接続し、一つのturnでdynamic toolを8回
直列実行してWebSocket接続数1、完了response数18、全tool resultのmaskと最終responseの復元を
確認しました。最終コードでのwall timeは132,139.9 msでした。

同じ実Codex・実OpenAIで4回のtool chainを両transportへ通した比較では、WebSocket
42,391.4 ms、HTTP 91,033.0 msとなり、同一実行で53.4%の短縮を観測しました。外部serviceの
負荷で変動する観測値であり、性能保証値ではありません。実導通ではCodexがWebSocket frameへ
付ける`stream: true`、response完了後の`responsesapi.websocket_timing`、HTTP成功responseで
欠落する場合があるSSE Content-Typeも確認し、adapterで互換処理しています。

2026-07-31にDocker Desktopのnative Linux arm64 container、Python 3.12.13で現行source gateを
再実行しました。ruff、mypy strict 71 source files、unit／evaluation 688件、mock Gateway E2E
4件、実OpenAI E2E 1件が成功しました。同じimageを`--network none`で別起動し、外向きinterfaceと
default routeがないことを構造検査した上で、実Codex CLI／Claude Code CLI E2E 2件も成功しました。
実OpenAIの最終観測はWebSocket 84,033.9 ms、HTTP 115,698.8 ms、接続数1、完了response数14でした。
単回のwall timeは外部service負荷、prompt cache、生成時間を分離できないため、大小関係ではなく
生値と差をevidenceへ残します。詳細は
[Linux arm64 gate evidence](release-evidence/linux-arm64-2026-07-31.md)に記録しています。

source版だけが公開対象であることをrelease noteへ明記できれば、最初の公開版を`1.0.0`とする
判断は妥当です。判断理由は
[ADR-0016](../adr/0016-reset-config-schema-version.md)に記録しています。

Windowsはsource版を含めて公開対応範囲外です。部分的に動くことを根拠としたbest-effort対応は
[ADR-0013](../adr/0013-reject-best-effort-windows-support.md)で却下しました。

2026-08-02に[ADR-0021](../adr/0021-add-windows-native-source-target.md)を採用し、Windows 11 x64、
CPython 3.12 x64、local fixed NTFS、cmd.exeを最初のnative source targetとして実装を開始しました。
current user、SYSTEM、AdministratorsだけへFull Controlを与えるprotected DACLをWindows APIで作成・
検査し、mode別`%LOCALAPPDATA%\SecurityMasker\<mode>`、config、辞書、key、SQLite DB／WAL／SHM／
lock、`init --force`のstaging／rollbackへ配線しました。Windowsではmaster key自身をlockしたまま
renameできないため、置換対象外の`securitymasker.state.lock`をGatewayと`init`が共有します。

同日のWindows 11 x64 build 26200.8875、Python 3.12.10で`scripts\test-setup.cmd`を実行し、
Windows専用lockからVisual Studioなし・wheel-onlyでTorch 2.13.0を含む環境を構築しました。固定NER
modelの6 artifactをdownloadし、size／SHA-256検証とlocal loadに成功しました。最新treeではruff、mypy strict
73 source files、unit／evaluation 742件（5 skip）、mock upstreamを使う実process Gateway E2E 4件が
成功しています。Windows native重点testはowner／protected DACL、Everyone／継承ACL拒否、mode別
既定directory、config load、SQLite artifact、restart／暗号化／wrong key／mode／tamper／二重writer、
`init --force`とrollbackを含みます。

同じ環境でaccess-denied ACEとNULL DACL、junction、subst driveを実OS上で拒否し、別processのwriter
競合、graceful close、forced termination後のprivate leaseと再openを検証しました。暗号化SQLiteと
sidecar keyのbackup pairをprivate DACLで保護して別layoutへ復元し、合成session／response bindingを
再読込できることも確認しました。これら3件のWindows native process testを`release-check.cmd`へ接続
しています。

Hyper-V VMを必須にせずoperatorのCodex Desktop接続を維持するため、ADR-0021へ専用local standard
userのWindows Firewall境界を追加しました。administratorが試験user SIDへ、loopback以外の全IPv4／
IPv6と全IP protocolをblockするPersistentStore ruleを作成し、試験user自身がActiveStoreのSID、
address range、profile、actionを検査した後だけ実CLI E2Eを開始します。ruleのinstall／verify／remove、
Codex CLI 0.146.0とClaude Code 2.1.220の既定path解決、外部canary、local mock E2Eをcmd runnerへ実装し、
rule未設定時のfail-closedとunit testを確認しました。UAC昇格したcmdから専用standard user SIDへruleを
作成し、そのuser自身によるActiveStore検査と外部canary拒否を確認した上で実CLI E2E 2件を実行しました。
CodexはResponses WebSocket、Claude CodeはAnthropic Messages routeを使用し、local mockが受信した最終
payloadに合成原文がないこと、CLI出力で合成原文が復元されaliasが残らないことを確認しました。結果は
2件成功、42.11秒でした。詳細は
[Windows x64 source evidence](release-evidence/windows-x64-source-2026-08-02.md)に記録しています。

追加で実security descriptorのownerとprocess SIDが一致しない場合の拒否を固定し、有効なSDDLから
object ACEとconditional callback ACEを実fileのDACLへ設定して、どちらもunsupportedとして拒否する
ことを確認しました。

固定NER modelは、Windowsでmodel loadから実CPU推論までsocket接続を禁止した状態で合成PERSONを検出
しました。実snapshotの全artifactをNTFS hard linkで複製し、`config.json`だけを1 byte変更したshadowは
runtime load前のdigest再検証で拒否しました。元のmodel cacheは変更していません。

freshなsource archiveを固定名`SecurityMaskerTester`のstandard userでだけ実行するcmd gateを追加しました。
`.git`／`.venv`と既存製品dataがないこと、local fixed NTFS、reparse point非使用、環境分離をpreflightし、
setup、両mode init、doctor、preview、client config、local release gateを一巡します。実装とparser／拒否
testは完了していますが、新規作成した試験userでの完走証跡はまだ取得していません。

これはWindows対応完了の証拠ではありません。別principalが所有する実file、未知ACE、network／
removable driveのnative negative matrix、利用者向けbackup／restore操作、Codex／Claude Code CLIと
Desktop設定手順、standard userのclean-machine source archive gateが残っています。これらをすべて完了して
新しい対応判断を記録するまで、Windowsでは実際の機密情報を扱いません。

## Binary版

2026-08-01にmacOS arm64で、one-fileをLite版（モデル非同梱）とFull版（固定モデル同梱）へ分離
しました。Lite版は188,764,560 bytes（約180 MiB）、Full版は961,502,400 bytes（約917 MiB）でした。
Lite版binary自身の`model-load`で固定6 artifactを取得・SHA-256検証し、model cacheが空なら原文を
表示せずfail-closedになること、取得後は両modeのGateway E2Eを完走することを確認しました。Full版も
local cacheへ依存せず従来の同梱NERと両mode E2Eを完走しました。`--version`は各artifactを
`binary lite`／`binary full`として識別します。変更後のlocal回帰ではruff、mypy strict 72 source
files、unit／evaluation 706件、mock Gateway E2E 4件が成功しました。詳細は
[macOS arm64 Lite／Full evidence](release-evidence/macos-arm64-lite-full-one-file-2026-08-01.md)に
記録しています。

同日にLinux arm64のLite／FullもDocker Desktopのnative arm64 builderでbuildし、Lite 6件、Full 5件
（Lite専用1件skip）のbinary integrationを完了しました。Lite版は248,563,712 bytes（約237 MiB）、
Full版は1,019,794,840 bytes（約972.6 MiB）でした。Pythonを含まないDebian 12 slim runtimeを
外部networkなし・read-only root filesystemで起動し、両profileのinit、config validation、標準NER
previewも確認しました。詳細は
[Linux arm64 Lite／Full evidence](release-evidence/linux-arm64-lite-full-one-file-2026-08-01.md)に
記録しています。

公開には次が必要です。

1. 対象OS／architectureごとのnative buildとclean-machine binary gate。
2. macOSのDeveloper ID署名とnotarization。Windowsを出す場合はcode signing。
3. Lite／Fullに同梱するtransitive componentの再配布条件とNOTICEの確認。
4. version、checksum、release note、source tagとの対応。

Lite版はmodel weightをSecurityMasker artifactへ含めないため、model weight再配布のblockerを持ちません。
ただし上記のdependency再配布、署名、native gateが終わるまで公開をblockします。Full版はこれらに加え、
model作者・dataset権利者への確認または適切な法務確認が終わるまで外部公開をblockします。

## Ownerに必要な操作

- `1.0.0`の公開範囲とrelease noteを確定する
- repository公開、tag、GitHub Release、source archive/checksum uploadを行う
- binaryも公開する場合だけ、Lite／Full、対象OS、再配布、署名、artifact uploadを別途判断する

過去のrelease candidate実測は
[0.1.0 RC evidence](release-evidence/0.1.0-rc-2026-07-26.md)、合格条件は
[Release gate](release.md)を参照してください。
