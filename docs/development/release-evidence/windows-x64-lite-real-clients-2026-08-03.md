# Windows x64 Lite実client／実provider E2E検証記録

## 位置付け

1.0.0 Lite one-fileを、実Codex CLI／Claude Code CLIと実providerの間のGatewayとして動かした
opt-in E2E evidenceです。固定合成値だけを使用し、認証値とprovider payloadは表示・保存していません。
これはtechnical artifactの互換性確認であり、binaryの署名、再配布、clean-machine gate、公開承認を
意味しません。

## 対象

- date: 2026-08-03
- tested product commit: `8393547091e8180a729ad0956bcab2b48fe4127f`
- OS／architecture: Windows 11 25H2 build 26200.8875 x64
- Python: 3.12.10 x64（test harness）
- Codex CLI: 0.146.0
- Claude Code: 2.1.220
- binary: `securitymasker 1.0.0 (binary lite)`
- size: 202,472,004 bytes
- SHA-256: `e16ae611679ebe65b528acbb1d371c508f90181426a25f3aa82845c9c5415bd8`

実在人物、実secret、repository内容はpromptへ含めていません。各test専用の空directory、一時product
layout、固定辞書を使い、日本語NERは無効にしました。clientの既存認証はprocess環境から利用しますが、
値をtest出力やfileへ複製しません。

## Codex／OpenAI

実Codex app-serverからLite binaryのChatGPT modeを経由し、OpenAI Responses WebSocketで固定合成値を
返すdynamic toolを4回直列実行しました。

- tool calls: 4
- WebSocket connections: 1
- completed responses: 6
- measured wall time: 15,515.0 ms
- pytest: `1 passed in 29.58s`

初回inputと全tool resultはGateway送信前にmaskされ、最終responseでは固定合成PERSONへ復元され、
aliasが残らないことを確認しました。

## Claude Code／Anthropic

実Claude CodeからLite binaryのClaude modeを経由し、固定合成PERSONを一度だけ正確に返すよう
Anthropic Messagesへ要求しました。

- completed streams: 1
- turns: 1
- measured wall time: 3,110.0 ms
- pytest: `1 passed in 14.77s`

送信直前の`request_masked`が1件以上であること、実streamがHTTP 200で完了したこと、Claude Codeの
最終表示が固定合成PERSONと完全一致すること、aliasが残らないことを確認しました。

## Claude Code MCP拡張gate

同じClaude Code 2.1.220で、test専用stdio MCPの`repeat_probe`を4回呼ぶ拡張gateも試行しました。
上流Messages streamはHTTP 200で完了しましたがMCP tool callは0件で、`--mcp-config=<path>`、
`--strict-mcp-config`、対象toolだけの`--allowedTools`、`alwaysLoad: true`を指定してもtool chainは開始
されませんでした。この挙動はLite binaryを経由しないMCP初期化／tool公開の層にあり、単一turnで確認
できたmask／restore互換性とは分離します。拡張gateは`SM_RUN_ANTHROPIC_MCP_E2E=1`を必要とする独立
opt-inとして残し、成功evidenceには数えません。

## Windows process分離

test harnessはprocess名でCodex／Claude関連processを列挙・終了しません。自ら`Popen`で起動した
Gatewayとclient processのハンドルだけを保持し、Windows process groupへ終了通知後、必要な場合だけ
同じhandleをterminate／killします。これによりCodex Desktopのapp-serverを終了対象へ含めません。

## 結論

Windows x64の1.0.0 Lite technical artifactについて、実Codex／OpenAIのtool chainと、実Claude
Code／Anthropicの単一turnで実provider互換性、送信前mask、最終表示の復元を確認しました。
binaryは引き続き1.0.0の公開対象外です。
