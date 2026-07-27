# Compatibility

## 製品mode

| mode | local route | upstream | 認証 | 自動検証 |
|---|---|---|---|---|
| `chatgpt` | `/responses`, `/v1/responses`, models | ChatGPT Codex backend | ChatGPT/Codex bearer passthrough | Codex CLI + mock upstream |
| `claude` | `/v1/messages`, count_tokens, models, `HEAD /` | Anthropic API | bearer/API key passthrough | Claude Code CLI + mock upstream |

各processは一方のrouteだけを公開します。未知field/eventは、構造を壊さずleak guardを通過できる場合に
限り透過します。WebSocket Responsesは無効です。

## 検証済み範囲

- OpenAI Responses: buffered/streaming text、tool argument、response binding、
  `previous_response_id`
- Anthropic Messages: buffered/streaming text、tool use input、count_tokens、
  feature header
- protocol-native file/image/audio添付とprovider file search: 未検査転送せずlocal block
- SQLite: process restart後のalias復元、wrong key/mode、tamper、二重writer
- source最小要件: Python 3.11
- clean setup実測: Python 3.11（macOS arm64）、Python 3.12（macOS arm64 / Linux arm64）
- Linux NER runtime: 公式CPU版Torch 2.13.0+cpu
- one-file spike: macOS arm64、PyInstaller 6.21.0

実CLIの検証baselineは Codex CLI 0.145.0 と Claude Code 2.1.212です。protocolは変化し得るため、
release時に最新対象versionでE2Eを再実行します。0.1.0ではLinux arm64の外部networkなし環境で、
両CLIのmask、local mockへの到達、response復元を確認しました。

## Desktopについて

ChatGPT Desktop/Codex surfaceとCodex CLI、Claude Code DesktopとClaude Code CLIは設定・protocolを
共有するため、CLIを自動testの代用にしています。Desktop UIの手動確認が未実施のreleaseでは、
「CLIと共有設定で検証済み」とだけ表現し、Desktop実証済みとは表現しません。

## 対象外

- 通常のWeb版ChatGPT会話
- localhost Gatewayを通らないremote session
- 外部MCP/hosted toolへの原文復元
- OpenAI Chat Completions
- public bind、multi-user/multi-tenant、multi-worker
- Python 3.10以下

## Python最小versionの根拠

SecurityMaskerが使用する最も新しい標準library機能は、`enum.StrEnum`、`tomllib`、
`datetime.UTC`で、いずれもPython 3.11から利用できます。固定runtime依存もPython 3.11を
許容し、3.12固有のsyntaxやAPIは使っていません。このため3.12に限定する製品上の理由はなく、
3.11をsource版の最小要件とします。

Python 3.10以下へ広げるには、これらのbackportまたは互換実装、追加dependency、対応versionごとの
clean setup・NER・Gateway回帰testが必要です。保護境界のtest matrixを未検証のまま広げないため、
現時点では3.10以下を対象外とします。one-file版の利用者にはPython runtimeの導入は不要です。

## binary

one-fileはOS/architecture別artifactです。macOS arm64 spikeでは約917 MiBでした。他OS、
署名/notarization、Python未導入の物理clean machineは未検証です。公開条件は
[status](status.md) を参照してください。
