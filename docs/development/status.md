# 開発・リリース状況

最終更新: 2026-07-28

この文書を、現行構成の `done` / `partial` / `blocked` の正とします。`done` は実装、製品配線、
回帰test、利用/運用手順が揃った項目だけです。

## 現在の製品

| 項目 | 状態 | 根拠・残件 |
|---|---|---|
| v1 config、単一辞書、隣接探索、safe init | done | 旧フラット形式撤去、unknown field/権限/上書き拒否test |
| `chatgpt` / `claude` 1 process・1 route | done | wrong-protocol拒否、別port同時起動test |
| OpenAI Responses / Anthropic Messages | done | buffered/stream、tool argument、count_tokens、header test |
| 暗号化SQLite | done | keyed lookup、AES-GCM、wrong key/mode/tamper、restart、writer lease test |
| 標準日本語NER | done（source） | setupへ固定model/digest検証、既定ON、silent downgrade禁止 |
| preview / client snippet / read-only doctor | done | Gateway同一pipeline、設定非変更test |
| 利用者向けCLIリファレンス | done | parserの全leaf command・optionとの網羅性test |
| 通常setupとtest setupの分離 | done | scripts/setup、test-setup、release-check |
| one-file技術spike | partial | macOS arm64 build/E2E成功。他OS・署名・物理clean machine未完 |
| 旧Redis/Docker標準経路/CI/multi-tenant/run撤去 | done | 旧製品code/artifact削除、文書再編、回帰test |
| Windows Linux-hosted spike | partial | WSL2番外編とmode別Compose artifactを追加。Windows実機gate未完 |
| source release candidate | done（macOS/Linux arm64） | 0.1.0、clean setup、全gate、展開artifact、再現可能checksumを検証 |
| application 1.0.0判断 | partial | schema v1変更後のmacOS unit/mock gate成功。Linux隔離実CLI gate再実行と公開範囲の明記が必要 |
| binary公開 | blocked | model再配布判断、署名、対象OS別clean-machine gateが未完 |

## source版の判断

source checkout/archiveは、検証済みのmacOS arm64 / Linux arm64で、固定dependencyと固定NER
modelを利用者環境へ取得する形なら技術的に公開可能です。PyPI登録、Docker、GitHub Actionsは
必要ありません。残っているのはrepository公開、tag、GitHub Releaseなどownerの公開操作だけです。

application versionは現在`0.1.0`のままです。schema v1への変更後にsource release gateを
再実行し、source版だけが公開対応範囲であることをrelease noteへ明記できれば、最初の公開版を
`1.0.0`とすることは妥当です。判断基準は
[ADR-0016](../adr/0016-reset-config-schema-version.md)に記録しています。

2026-07-28のschema v1変更後、macOS arm64で`ruff`、`mypy`、固定NER必須unit/evaluation
615件、mock Gateway 3件が成功しました。`scripts/release-check`は設計どおりLinux network
namespaceがないmacOS上で隔離実CLI E2Eを成功扱いせず停止しました。Linux arm64での隔離実CLI
E2E再実行は未完です。

Windowsはsource版を含めて公開対応範囲外です。部分的に動くことを根拠としたbest-effort対応は
[ADR-0013](../adr/0013-reject-best-effort-windows-support.md) で却下しました。Windows用setup、
ACLによる機密file保護、PowerShell向けclient設定、native dependency・SQLite・CLI E2Eが
すべて揃うまでは、Windows上で実際の機密情報を扱えるとは表明しません。

Windows上のLinux-hosted deploymentについては、
[ADR-0015](../adr/0015-evaluate-windows-linux-hosted-deployments.md) に従い、WSL2直接実行と
Docker Composeを同一gateで比較します。WSL2番外編とCodex / Claude別Compose artifactは
technical spikeとして追加しましたが、Windows実機の共通gateは未完であり、どちらも
公開対応範囲外です。

2026-07-26に次をrelease candidate `0.1.0` で確認しました。

- macOS arm64のPython 3.11ではcleanな一時展開から
  `PYTHON_COMMAND=/opt/homebrew/bin/python3.11 scripts/test-setup`、Python 3.12では
  環境変数なしの `scripts/setup` が成功。Linux arm64のPython 3.12でもclean setup成功
- Linuxでは公式CPU版Torch `2.13.0+cpu` を選択し、CUDA runtimeへ依存しないこと
- `ruff`、`mypy`、固定NER必須unit/evaluation 586件、mock Gateway 3件が成功
- 外部networkなしのLinuxでCodex CLI 0.145.0 / Claude Code 2.1.212実E2E 2件が成功
- source archiveを展開した状態からsetup、init、validate、NER preview、client config生成が成功
- 別clean worktreeから生成したsource archiveがbyte-for-byte一致
- DB/keyのpair backup/restore、不一致拒否、再起動をまたぐidle/absolute TTLを検証

## one-file実測

macOS arm64、Python 3.12.13、PyInstaller 6.21.0:

| 項目 | 結果 |
|---|---|
| artifact | arm64 thin Mach-O、961,117,984 bytes（約917 MiB） |
| clean build | 243.6秒 |
| cold `--help` | 約25.5秒 |
| warm config-check | 約11.5秒 |
| NER preview | 約46.8秒 |
| 外部runtime link | macOS標準libSystem/libzのみ |
| 署名 | ad-hoc |
| 一時展開 | `TMPDIR/_MEI*`、通常終了/SIGTERM後に残存なし |
| binary E2E | 3件成功（init、validate、NER、両mode、SQLite、mask/restore、漏えいゼロ） |

## binary公開ブロッカー

binaryを公開artifactとして扱うには次が必要です。

1. 対象OS/architectureごとのnative buildとclean-machine binary gate。
2. macOSではDeveloper ID署名/notarization、Windowsを出すならcode signing方針。
3. model weightをone-fileへ同梱して再配布する条件の確認。model/baseはMIT、学習datasetは
   CC BY-SA 3.0であり、現時点では法的結論を置かない。
4. version、checksum、release note、source tagとの対応。

再配布確認が終わるまで、model weight同梱binaryの外部公開はblockします。source setupは
weightをrepository/release artifactへ含めないため、このbinary固有blockerの対象外です。

## ownerに必要な操作

- source版: GitHub repositoryの公開設定、tag、GitHub Release、source archive/checksum upload
- binary版も公開する場合: 公開対象OSの選択、model weight再配布判断、必要なcode
  signing/notarization、対象OSごとのartifact upload
- 可能なら合成promptだけを使うCodex app / Claude Code Desktop手動smoke test

Desktop smoke testを行わなくてもCLIによるprotocol検証は可能ですが、その場合の公開表現は
「CLIと共有設定で検証済み」に限定します。
