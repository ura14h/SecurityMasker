# macOS arm64 実Codex／実OpenAI E2E検証記録

実行日: 2026-08-01

この文書は、実CodexからSecurityMaskerを通してOpenAI実サーバへ接続した専用E2Eの
evidenceです。source release gate全体または正式な`1.0.0` evidenceではありません。

## 対象

- tested commit: `eb68a27cc69b283e47370b4d74a1fe66b58cd108`
- host: macOS arm64
- Python: 3.12.13
- Codex CLI: 0.145.0
- transport: Responses WebSocket、HTTP/SSE
- authentication: 既存のChatGPT loginをCLI自身が使用。token値は表示、複製、保存していない

## 安全条件

- 外部へ送る検査値は固定合成PERSON `SYNTHETIC_PERSON_793421`だけ
- 一時product layout、mode別SQLite/key、固定辞書、日本語NER無効を使用
- shell tool、unified exec、agent、MCP、plugin、web searchを無効化
- test harnessが提供するdynamic toolは進捗と固定合成PERSONだけを返す
- 認証header、response本文、prompt、alias対応表をevidenceまたはlogへ保存しない

## 実OpenAI結果

Anthropic stream修正後の同じsnapshotで、OpenAI実サーバへの4 tool callをWebSocketとHTTP/SSEの
両方で再実行しました。

| 項目 | WebSocket | HTTP/SSE |
|---|---:|---:|
| Codex完了 | 成功 | 成功 |
| tool calls | 4 | 4 |
| wall time | 73,163.6 ms | 67,960.2 ms |
| WebSocket接続 | 1 | 0 |
| WebSocket完了response | 10 | 0 |
| 全tool resultのmask | 成功 | 成功 |
| 最終responseの復元・alias非残存 | 成功 | 成功 |

単回のwall time差は外部service負荷、prompt cache、生成時間を分離できないため合否条件または
性能保証値にはしません。

## 実導通で見つかった差分

最初のHTTP診断実行は、CodexがHTTP requestへ付ける`x-codex-turn-metadata`内のLuhn-validな
millisecond timestampをcredit cardとしてblockし、上流へ送信せずfail-closedで終了しました。
headerをJSONとして構造化し、形式検証済みのUUID／prefix付きID／既知timestampだけを一般PII
format検査から除外しました。header全体は免除せず、他のmetadata値と不正JSONは引き続き
leak guardを通します。上表は修正後の独立した成功実行です。

## ローカル回帰

| gate | 結果 |
|---|---|
| ruff | 成功 |
| mypy strict | 71 source files成功 |
| unit／evaluation | 699件成功、warning 1件 |
| mock upstream実process Gateway E2E | 4件成功 |
| 実Codex／実OpenAI WebSocket・HTTP比較E2E | 1件成功 |

warningはStarlette TestClientからhttpx2への将来移行を示すdeprecationで、test失敗またはsecurity
境界の縮小ではありません。
