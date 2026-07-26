# 開発・リリース状況

最終更新: 2026-07-26

この文書を、現行構成の `done` / `partial` / `blocked` の正とします。`done` は実装、製品配線、
回帰test、利用/運用手順が揃った項目だけです。

## 現在の製品

| 項目 | 状態 | 根拠・残件 |
|---|---|---|
| v2 config、単一辞書、隣接探索、safe init | done | unknown field/権限/上書き拒否test |
| `chatgpt` / `claude` 1 process・1 route | done | wrong-protocol拒否、別port同時起動test |
| OpenAI Responses / Anthropic Messages | done | buffered/stream、tool argument、count_tokens、header test |
| 暗号化SQLite | done | keyed lookup、AES-GCM、wrong key/mode/tamper、restart、writer lease test |
| 標準日本語NER | done（source） | setupへ固定model/digest検証、既定ON、silent downgrade禁止 |
| preview / client snippet / read-only doctor | done | Gateway同一pipeline、設定非変更test |
| 通常setupとtest setupの分離 | done | scripts/setup、test-setup、release-check |
| one-file技術spike | partial | macOS arm64 build/E2E成功。他OS・署名・物理clean machine未完 |
| 旧Redis/Docker/CI/multi-tenant/run撤去 | done | 製品code/artifact削除、文書再編、回帰test |
| release candidate | partial | 0.1.0、release note、checksum生成、state scenarioを整備。owner操作は下記 |

## source版の判断

source checkoutは、固定dependencyと固定NER modelを利用者環境へ取得する形で技術的に配布可能です。
PyPI登録、Docker、GitHub Actionsは必要ありません。公開前にはPhase 10のclean checkout gateと
version/release note確定が残っています。

## one-file実測

macOS arm64、Python 3.12.13、PyInstaller 6.21.0:

| 項目 | 結果 |
|---|---|
| artifact | arm64 thin Mach-O、961,152,432 bytes（約917 MiB） |
| clean build | 約244秒 |
| cold `--help` | 約25.5秒 |
| warm config validate | 約11.5秒 |
| NER preview | 約46.8秒 |
| 外部runtime link | macOS標準libSystem/libzのみ |
| 署名 | ad-hoc |
| 一時展開 | `TMPDIR/_MEI*`、通常終了/SIGTERM後に残存なし |
| binary E2E | init、validate、NER、両mode、SQLite、mask/restore、漏えいゼロ |

## 公開ブロッカー

binaryを公開artifactとして扱うには次が必要です。

1. 対象OS/architectureごとのnative buildとclean-machine binary gate。
2. macOSではDeveloper ID署名/notarization、Windowsを出すならcode signing方針。
3. model weightをone-fileへ同梱して再配布する条件の確認。model/baseはMIT、学習datasetは
   CC BY-SA 3.0であり、現時点では法的結論を置かない。
4. version、checksum、release note、source tagとの対応。

再配布確認が終わるまで、model weight同梱binaryの外部公開はblockします。source setupは
weightをrepository/release artifactへ含めないため、このbinary固有blockerの対象外です。

## ownerに必要な操作

- GitHub repositoryの公開設定、tag、GitHub Release、artifact upload
- 必要なcode signing/notarization
- 公開対象OSの選択
- model weight再配布判断
- 可能なら合成promptだけを使うChatGPT Desktop / Claude Code Desktop手動smoke test

Desktop smoke testを行わなくてもCLIによるprotocol検証は可能ですが、その場合の公開表現は
「CLIと共有設定で検証済み」に限定します。
