# 対応環境

この文書を、利用できるplatform、client、protocol、配布形態の正とします。初回導入手順は
[導入ガイド](../getting-started.md)を参照してください。

## Source版の必要条件

- 対応platform／versionのPython
- macOS／LinuxではPOSIX shell、Windowsでは標準cmd.exeと`venv`を作成できる環境
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
| macOS arm64 | Python 3.11 / 3.12で検証済み | Lite／Full技術spike済み | source版の対応環境 |
| Linux arm64 | Python 3.12で検証済み | Lite／Full技術spike済み（Debian 12） | source版の対応環境 |
| Windows 11 x64 build 26100以降 | CPython 3.12 x64で検証済み | Lite／Full技術spike済み | [ADR-0023](../adr/0023-support-windows-native-source.md)。local fixed NTFSのsource版限定 |
| その他のOS・architecture | 未検証 | 未検証 | 対応を表明しない |

Windows native source版はmode別`%LOCALAPPDATA%\SecurityMasker\<mode>`を既定data directoryとし、
current user、SYSTEM、AdministratorsだけへFull Controlを与えるprotected DACLを作成・検査します。
setup、利用、client設定は[Windows native source版の導入手順](../guides/windows-native-source.md)に
従います。Windows 10、ARM64、Python 3.11／3.13以降、ReFS、FAT、removable／network／subst drive、
UNC pathは未対応であり、部分的に動いても対応範囲へ含めません。

WSL2直接実行とDocker Desktop上のDocker Composeは、Windows nativeとは別のLinux-hosted候補として
[ADR-0015](../adr/0015-evaluate-windows-linux-hosted-deployments.md) で同一gateによる比較を
決定しました。[Windows上のLinux環境で評価する](../unsupported/windows-evaluation.md)と
mode別Compose artifactは
technical spikeとして用意していますが、どちらも対応環境ではありません。

## 検証済み範囲

- OpenAI Responses: buffered/SSE/WebSocket text、tool argument、response binding、
  `previous_response_id`、一つのCodex turn内でのWebSocket接続再利用、
  `responsesapi.websocket_timing`
- Anthropic Messages: buffered/streaming text、tool use input、count_tokens、
  feature header
- protocol-native file/image/audio添付とprovider file search: 未検査転送せずlocal block
- SQLite: process restart後のalias復元、wrong key/mode、tamper、二重writer
- source最小要件: macOS／LinuxはPython 3.11、Windows targetはCPython 3.12 x64
- clean setup実測: Python 3.11（macOS arm64）、Python 3.12（macOS arm64／Linux arm64／Windows 11 x64）
- Linux NER runtime: 公式CPU版Torch 2.13.0+cpu
- one-file spike: macOS arm64、Linux arm64 Debian 12 container、Windows 11 x64のLite／Full、
  PyInstaller 6.21.0

実CLIの検証baselineはmacOS／LinuxがCodex CLI 0.145.0とClaude Code 2.1.212、WindowsがCodex CLI
0.146.0とClaude Code 2.1.220です。protocolは変化し得るため、release時に最新対象versionでE2Eを
再実行します。2026-07-31にはLinux arm64の外部networkなし
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
- Windows 10、Windows ARM64、Windows one-file版、Windowsの未検証Python／filesystem
- macOS／Linux arm64以外の未検証architecture

## Python最小versionの根拠

SecurityMaskerが使用する最も新しい標準library機能は、`enum.StrEnum`、`tomllib`、
`datetime.UTC`で、いずれもPython 3.11から利用できます。固定runtime依存もPython 3.11を
許容し、3.12固有のsyntaxやAPIは使っていません。このため3.12に限定する製品上の理由はなく、
3.11を共通実装の最小要件とします。ただしWindowsはdependency wheelとnative gateをCPython 3.12
x64でだけ固定・検証しているため、対応targetを3.12へ限定します。

Python 3.10以下へ広げるには、これらのbackportまたは互換実装、追加dependency、対応versionごとの
clean setup・NER・Gateway回帰testが必要です。保護境界のtest matrixを未検証のまま広げないため、
現時点では3.10以下を対象外とします。one-file版の利用者にはPython runtimeの導入は不要です。

## binary

one-fileはOS/architecture別artifactで、model配置だけが異なる二つのprofileを持ちます。

- Lite版（モデル非同梱版）：初回に`securitymasker model-load`で固定modelをlocal cacheへ取得する。
  取得後の通常処理はofflineで、model不足・破損時はfail-closedになる。
- Full版：同じ固定modelをone-fileへ同梱し、初回からofflineで使用できる。

2026-08-01のmacOS arm64 spikeではLite版188,764,560 bytes（約180 MiB）、Full版961,502,400 bytes
（約917 MiB）でした。同日のLinux arm64 spikeではLite版248,563,712 bytes（約237 MiB）、Full版
1,019,794,840 bytes（約972.6 MiB）でした。LinuxはDebian 12 slimのPythonなしruntimeで、外部network
なし、read-only root filesystemによる両profileのinit、config validation、標準NER previewを検証済み
です。2026-08-02のWindows 11 x64 spikeではLite版202,654,668 bytes（約193.3 MiB）、Full版
973,887,958 bytes（約928.8 MiB）をnative buildし、Pythonを解決できない子process環境で両profileの
binary integrationを完了しました。実測条件とchecksumは
[macOS arm64 evidence](../development/release-evidence/macos-arm64-lite-full-one-file-2026-08-01.md)と
[Linux arm64 evidence](../development/release-evidence/linux-arm64-lite-full-one-file-2026-08-01.md)、
[Windows x64 evidence](../development/release-evidence/windows-x64-lite-full-one-file-2026-08-02.md)に記録
しています。

これらは技術検証であり、物理clean machineや公開artifactの互換性を表明しません。両版とも署名／
notarizationと同梱dependencyの再配布確認が未完で、Full版にはmodel weight再配布確認も残ります。
公開条件は[status](../development/status.md)を参照してください。
