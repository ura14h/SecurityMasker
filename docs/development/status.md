# 開発・リリース状況

最終更新: 2026-07-31

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
| one-file binary公開 | blocked | 再配布判断、署名、対象OS別clean-machine gateが未完 |
| Windows | 対応外 | native security gate一式が未実装・未検証 |

## Source版

現在のapplication versionは`0.1.0`です。macOS arm64のPython 3.11／3.12とLinux arm64の
Python 3.12でsource setupを検証しています。

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

## Binary版

macOS arm64に加え、Linux arm64のone-file buildとE2Eもtechnical spikeとして成功していますが、
どちらも公開artifactではありません。Linux arm64ではDocker Desktopのnative arm64 builderで
PyInstaller 6.21.0を実行し、Pythonを含まないDebian 12 slim runtimeを外部networkなし・read-only
root filesystemで起動して、init、config validation、標準NER previewまで確認しました。変更後の
local回帰ではruff、mypy strict 71 source files、unit／evaluation 689件も成功しました。詳細は
[Linux arm64 one-file evidence](release-evidence/linux-arm64-one-file-2026-07-31.md)に記録しています。

公開には次が必要です。

1. 対象OS／architectureごとのnative buildとclean-machine binary gate。
2. macOSのDeveloper ID署名とnotarization。Windowsを出す場合はcode signing。
3. model weightとtransitive componentの再配布条件の確認。
4. version、checksum、release note、source tagとの対応。

再配布確認が終わるまで、model weight同梱binaryの外部公開をblockします。

## Ownerに必要な操作

- `1.0.0`の公開範囲とrelease noteを確定する
- repository公開、tag、GitHub Release、source archive/checksum uploadを行う
- binaryも公開する場合だけ、対象OS、再配布、署名、artifact uploadを別途判断する

過去のrelease candidate実測は
[0.1.0 RC evidence](release-evidence/0.1.0-rc-2026-07-26.md)、合格条件は
[Release gate](release.md)を参照してください。
