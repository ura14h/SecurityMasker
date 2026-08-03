# 開発・リリース状況

最終更新: 2026-08-03

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
| source release candidate | done | macOS／Linux／Windowsで対応gate完了 |
| application `1.0.0`判断 | done | source版だけを公開し、binary版は公開対象外 |
| one-file Lite版公開 | blocked | dependency再配布、署名、対象OS別clean-machine gateが未完 |
| one-file Full版公開 | blocked | Lite版の残件に加え、model weight再配布判断が未完 |
| Windows native source | done | launcher隣接layoutのDACL／mock Gateway／native process検証済み |

## Source版

現在のapplication versionは`1.0.0`です。macOS arm64のPython 3.11／3.12、Linux arm64の
Python 3.12、Windows 11 x64のPython 3.12 x64でsource setupを検証しています。

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

最初の安定releaseを`1.0.0`とし、source版だけを公開対象とすることをrelease noteで
確定しました。binary版はこのreleaseの公開対象外です。判断理由は
[ADR-0016](../adr/0016-reset-config-schema-version.md)に記録しています。

Windows native source版は[ADR-0023](../adr/0023-support-windows-native-source.md)により、Windows 11
x64 build 26100以降、CPython 3.12 x64、local fixed NTFSへ限定して対応範囲へ加えました。その後、
[ADR-0024](../adr/0024-unify-source-adjacent-layout.md)でsource版の既定data配置を全OS共通のlauncher隣接へ
変更し、2026-08-03にWindows native再検証を完了しました。結果は
[Windows x64 launcher隣接source evidence](release-evidence/windows-x64-launcher-adjacent-source-2026-08-03.md)に
記録しています。
範囲外を部分的な動作だけでbest-effort対応とする方針は、引き続き
[ADR-0013](../adr/0013-reject-best-effort-windows-support.md)どおり却下します。

2026-08-02に[ADR-0021](../adr/0021-add-windows-native-source-target.md)を採用し、Windows APIによる
protected DACLの作成・検査、当時のmode別data directory、SQLite artifact、`init --force`の安全な切替を
実装しました。owner不一致、予期しないACE、UNC path、removable／network／subst drive、model改竄は
fail-closedで拒否します。

Windows 11 x64 build 26200.8875、Python 3.12.10、Visual Studioなしのfreshなstandard user環境で、
wheel-only setup、固定NER modelの取得・digest検証・offline推論、両modeの初期化とlocal gateを完走
しました。そのsnapshotではruff、mypy strict 73 source files、unit／evaluation 754件（5 skip）、mock
upstream E2E 4件、Windows native process test 3件が成功しています。

実CLI E2Eは、専用standard userのloopback以外をWindows Firewallで遮断し、試験user自身が有効なruleを
検査した後に実行しました。Codex CLI 0.146.0とClaude Code 2.1.220の両方で、local mockへの合成原文の
非送信、CLI出力での復元、alias非残存を確認しています。試験後はFirewall rule、user、profileを削除
しました。詳細な条件とnegative matrixは
[Windows x64 source evidence](release-evidence/windows-x64-source-2026-08-02.md)に記録しています。この
evidenceは従来のmode別`%LOCALAPPDATA%` layoutに対するもので、現行のlauncher隣接layoutには流用しません。

現行のlauncher隣接layoutはmacOS arm64でruff、mypy strict 73 source files、unit／evaluation 740件
（24 skip）、mock upstream E2E 4件が成功しています。Windows 11 x64でもruff、mypy strict
73 source files、unit／evaluation 759件（5 skip）、mock upstream E2E 4件、Windows native process
test 3件が成功しました。launcher隣接の既定init、source file／root DACL非変更、
managed artifactのprotected DACL、不正DACL、wrong owner、reparse point、非local fixed NTFSの拒否を
含みます。

backup／restore作業は利用者の運用範囲とし、製品CLIでは扱いません。setup、隣接data layout、Gateway、
Codex／Claude Codeの設定・解除は全OS共通の導入手順へまとめています。

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

2026-08-02にWindows 11 x64 build 26200.8875、Python 3.12.13、PyInstaller 6.21.0でLite／Fullを
native buildしました。Liteは202,654,668 bytes、Fullは973,887,958 bytesでした。Pythonを解決できない
隔離子process環境でprofile識別、model経路、標準NER、両modeのmock Gateway、SQLite、mask／復元、
一時展開cleanupを検証し、Lite 6件、Full 5件（Lite専用1件skip）が成功しています。詳細は
[Windows x64 Lite／Full evidence](release-evidence/windows-x64-lite-full-one-file-2026-08-02.md)に記録
しています。

公開には次が必要です。

1. 対象OS／architectureごとのnative buildとclean-machine binary gate。
2. macOSのDeveloper ID署名とnotarization。Windowsを出す場合はcode signing。
3. Lite／Fullに同梱するtransitive componentの再配布条件とNOTICEの確認。
4. version、checksum、release note、source tagとの対応。

Lite版はmodel weightをSecurityMasker artifactへ含めないため、model weight再配布のblockerを持ちません。
ただし上記のdependency再配布、署名、native gateが終わるまで公開をblockします。Full版はこれらに加え、
model作者・dataset権利者への確認または適切な法務確認が終わるまで外部公開をblockします。

## Ownerに必要な操作

- repository公開、tag、GitHub Release、source archive/checksum uploadを行う
- binaryも公開する場合だけ、Lite／Full、対象OS、再配布、署名、artifact uploadを別途判断する

過去のrelease candidate実測は
[0.1.0 RC evidence](release-evidence/0.1.0-rc-2026-07-26.md)、合格条件は
[Release gate](release.md)を参照してください。
