# 開発・リリース状況

最終更新: 2026-07-30

この文書を、現行構成の`done`／`partial`／`blocked`と公開範囲の正とします。`done`は実装、
製品配線、回帰test、利用・運用手順が揃った項目だけです。

## 現在の製品

| 項目 | 状態 | 残件 |
|---|---|---|
| strict config v1、単一辞書、safe init | done | なし |
| `chatgpt`／`claude`の1 process・1 route | done | なし |
| OpenAI Responses／Anthropic Messages | done | buffered、SSE、Codex WebSocket、tool argumentを含め検証済み |
| 暗号化SQLiteとmode別DB/key | done | なし |
| 標準日本語NER | done（source） | 固定revision/digest、既定ON |
| preview、client snippet、read-only doctor | done | なし |
| CLI・設定reference | done | schemaとparserの網羅性testあり |
| README、導入、カスタマイズ、運用導線 | done | 目的別配置、link/anchor testあり |
| 通常setupとtest setupの分離 | done | なし |
| source release candidate | partial | Linux arm64でWebSocket対応後の隔離実CLI gate再実行が必要 |
| application `1.0.0`判断 | partial | 上記gateとsource版だけを公開するrelease noteが必要 |
| one-file binary公開 | blocked | 再配布判断、署名、対象OS別clean-machine gateが未完 |
| Windows | 対応外 | native security gate一式が未実装・未検証 |

## Source版

現在のapplication versionは`0.1.0`です。macOS arm64のPython 3.11／3.12とLinux arm64の
Python 3.12でsource setupを検証しています。

2026-07-30にmacOS arm64でruff、mypy strict 71 source files、unit 650件、evaluation 3件が
成功しました。mock upstreamを使う実process Gateway E2E 4件と、Codex CLI 0.145.0から
SecurityMaskerのWebSocketを通してOpenAI実サーバへ接続する合成data smoke 1件も成功済みです。
Codex 0.145.0がWebSocket frameへ付ける`stream: true`は、実導通で確認してadapterで
上流送信前に除去しています。

Linux arm64で外向きnetworkを遮断した実Codex CLI／Claude Code CLI gateをWebSocket対応後に
再実行し、source版だけが公開対象であることをrelease noteへ明記できれば、最初の公開版を
`1.0.0`とする判断は妥当です。判断理由は
[ADR-0016](../adr/0016-reset-config-schema-version.md)に記録しています。

Windowsはsource版を含めて公開対応範囲外です。部分的に動くことを根拠としたbest-effort対応は
[ADR-0013](../adr/0013-reject-best-effort-windows-support.md)で却下しました。

## Binary版

macOS arm64のone-file buildとE2Eはtechnical spikeとして成功していますが、公開artifactでは
ありません。公開には次が必要です。

1. 対象OS／architectureごとのnative buildとclean-machine binary gate。
2. macOSのDeveloper ID署名とnotarization。Windowsを出す場合はcode signing。
3. model weightとtransitive componentの再配布条件の確認。
4. version、checksum、release note、source tagとの対応。

再配布確認が終わるまで、model weight同梱binaryの外部公開をblockします。

## Ownerに必要な操作

- Linux arm64でWebSocket対応後のsource release gateを再実行する
- `1.0.0`の公開範囲とrelease noteを確定する
- repository公開、tag、GitHub Release、source archive/checksum uploadを行う
- binaryも公開する場合だけ、対象OS、再配布、署名、artifact uploadを別途判断する

過去のrelease candidate実測は
[0.1.0 RC evidence](release-evidence/0.1.0-rc-2026-07-26.md)、合格条件は
[Release gate](release.md)を参照してください。
