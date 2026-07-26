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
- SQLite: process restart後のalias復元、wrong key/mode、tamper、二重writer
- source: Python 3.12
- one-file spike: macOS arm64、PyInstaller 6.21.0

実CLIの検証baselineは Codex CLI 0.145.0 と Claude Code 2.1.212です。protocolは変化し得るため、
release時に最新対象versionでE2Eを再実行します。

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
- Python 3.11以下

## binary

one-fileはOS/architecture別artifactです。macOS arm64 spikeでは約917 MiBでした。他OS、
署名/notarization、Python未導入の物理clean machineは未検証です。公開条件は
[status](status.md) を参照してください。
