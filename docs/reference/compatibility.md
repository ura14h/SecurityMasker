# 対応環境

この文書を、利用できるplatform、client、protocol、配布形態の正とします。初回導入手順は
[導入ガイド](../getting-started.md)を参照してください。

## Source版の必要条件

- 対応platform上のPython 3.11以上
- POSIX shellと`venv`を作成できる環境
- 初回setup時のnetwork接続
- Python runtime、PyTorch、固定日本語NER modelのための数GBの空き容量
- clientからloopback portへ接続できること

GPUやCUDA runtimeは必要ありません。通常のrequest処理中にpackageやmodelをdownloadしません。

## 製品mode

| mode | local route | upstream | 認証 | 自動検証 |
|---|---|---|---|---|
| `chatgpt` | `/responses`, `/v1/responses`（HTTP/SSE・WebSocket）、models | `chatgpt.com/backend-api/codex` | CodexのChatGPT bearer passthrough | Codex CLI + mock／実OpenAI upstream |
| `claude` | `/v1/messages`, count_tokens, models, `HEAD /` | Anthropic API | bearer/API key passthrough | Claude Code CLI + mock upstream |

各processは一方のrouteだけを公開します。未知field/eventは、構造を壊さずleak guardを通過できる場合に
限り透過します。Responses WebSocketは`chatgpt` modeだけで有効です。

## Platform対応

| platform | source版 | one-file版 | 状態 |
|---|---|---|---|
| macOS arm64 | Python 3.11 / 3.12で検証済み | 技術spikeのみ | source版の対応環境 |
| Linux arm64 | Python 3.12で検証済み | 技術spike済み（Debian 12） | source版の対応環境 |
| Windows | 非対応 | 非対応 | [ADR-0013](../adr/0013-reject-best-effort-windows-support.md)。setup、ACL検査、PowerShell設定、native E2E、build・署名が未実装 |
| その他のOS・architecture | 未検証 | 未検証 | 対応を表明しない |

Windows向けコード分岐が存在することは、製品対応を意味しません。機密fileのWindows ACLを
検査しておらず、POSIX用setup scriptとClaude向け`export`形式の設定案内もそのままでは使えません。
部分的に動く状態をbest-effort対応として公開する方針は
[ADR-0013](../adr/0013-reject-best-effort-windows-support.md) で却下しています。安全性を
Windows実機で確認するまでは、実データ利用の安全性を保証しません。利用者が評価する場合のriskと
免責は[Windows上のLinux環境で評価する](../unsupported/windows-evaluation.md)を参照してください。

WSL2直接実行とDocker Desktop上のDocker Composeは、Windows nativeとは別のLinux-hosted候補として
[ADR-0015](../adr/0015-evaluate-windows-linux-hosted-deployments.md) で同一gateによる比較を
決定しました。[Windows上のLinux環境で評価する](../unsupported/windows-evaluation.md)と
mode別Compose artifactは
technical spikeとして用意していますが、Windows実機gateは未完であり、どちらも対応環境では
ありません。

## 検証済み範囲

- OpenAI Responses: buffered/SSE/WebSocket text、tool argument、response binding、
  `previous_response_id`、一つのCodex turn内でのWebSocket接続再利用、
  `responsesapi.websocket_timing`
- Anthropic Messages: buffered/streaming text、tool use input、count_tokens、
  feature header
- protocol-native file/image/audio添付とprovider file search: 未検査転送せずlocal block
- SQLite: process restart後のalias復元、wrong key/mode、tamper、二重writer
- source最小要件: Python 3.11
- clean setup実測: Python 3.11（macOS arm64）、Python 3.12（macOS arm64 / Linux arm64）
- Linux NER runtime: 公式CPU版Torch 2.13.0+cpu
- one-file spike: macOS arm64、およびLinux arm64のDebian 12 container、PyInstaller 6.21.0

実CLIの検証baselineは Codex CLI 0.145.0 と Claude Code 2.1.212です。protocolは変化し得るため、
release時に最新対象versionでE2Eを再実行します。2026-07-31にはLinux arm64の外部networkなし
環境で、WebSocket対応後の両CLIのmask、local mockへの到達、response復元を確認しました。
2026-07-30にはmacOS arm64で
Codex CLI 0.145.0からSecurityMaskerのWebSocketを通してOpenAI実サーバへ接続し、合成値の
maskとresponse復元を確認しました。実Codex app-serverの一つのturnでdynamic toolを8回連続
実行して接続数1・完了response数18を確認し、同じ4回のtool chainではWebSocket
42,391.4 ms、HTTP 91,033.0 ms（53.4%短縮）を観測しました。外部serviceの負荷で絶対時間と
短縮率は変動するため、製品の性能保証値ではありません。

## Desktopについて

Codex appとCodex CLI、Claude Code DesktopとClaude Code CLIは設定・protocolを
共有するため、CLIを自動testの代用にしています。Desktop UIの手動確認が未実施のreleaseでは、
「CLIと共有設定で検証済み」とだけ表現し、Desktop実証済みとは表現しません。

## 対象外

- 通常のWeb版ChatGPT会話
- localhost Gatewayを通らないremote session
- 外部MCP/hosted toolへの原文復元
- OpenAI Chat Completions
- public bind、multi-user/multi-tenant、multi-worker
- Python 3.10以下
- Windows
- macOS / Linux arm64以外の未検証architecture

## Python最小versionの根拠

SecurityMaskerが使用する最も新しい標準library機能は、`enum.StrEnum`、`tomllib`、
`datetime.UTC`で、いずれもPython 3.11から利用できます。固定runtime依存もPython 3.11を
許容し、3.12固有のsyntaxやAPIは使っていません。このため3.12に限定する製品上の理由はなく、
3.11をsource版の最小要件とします。

Python 3.10以下へ広げるには、これらのbackportまたは互換実装、追加dependency、対応versionごとの
clean setup・NER・Gateway回帰testが必要です。保護境界のtest matrixを未検証のまま広げないため、
現時点では3.10以下を対象外とします。one-file版の利用者にはPython runtimeの導入は不要です。

## binary

one-fileはOS/architecture別artifactです。macOS arm64 spikeは約917 MiB、Linux arm64 spikeは
約972.5 MiBでした。Linux arm64はDebian 12 slimのPythonなしruntimeで、外部networkなし・
read-only root filesystemの起動、init、config validation、標準NER previewを検証済みです。
ただしこれはDocker container上の技術検証であり、複数distribution、物理clean machine、公開artifactの
互換性を表明するものではありません。署名/notarization、同梱componentとmodel weightの再配布確認も
未完です。公開条件は[status](../development/status.md)を参照してください。
