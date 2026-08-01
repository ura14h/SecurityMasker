# macOS arm64 実Claude Code／実Anthropic E2E検証記録

実行日: 2026-08-01

この文書は、実Claude CodeからSecurityMaskerを通してAnthropic実サーバへ接続した専用E2Eの
evidenceです。source release gate全体または正式な`1.0.0` evidenceではありません。

## 対象

- base commit: `f895b686f6c2ee87b159e1c8d80ca7641e8d7a3e`
- tested snapshot: 上記commitに実導通で見つかったstream互換性修正と本evidenceを加えたtree
- host: macOS arm64
- Python: 3.12.13
- Claude Code: 2.1.212
- model: `haiku`
- upstream: `https://api.anthropic.com`
- authentication: 既存のClaude.ai loginをCLI自身が使用。token値は表示、複製、保存していない

## 安全条件

- 外部へ送る検査値は固定合成PERSON `SYNTHETIC_PERSON_684209`だけ
- 一時product layout、mode別SQLite/key、固定辞書、日本語NER無効を使用
- Claude Codeの作業directoryは空で、user／project／local settings sourceを読まない
- built-in tool、他のMCP、slash command、Chrome連携、session永続化を無効化
- test専用stdio MCPは進捗と固定合成PERSONだけを返す
- 認証header、response本文、prompt、alias対応表をevidenceまたはlogへ保存しない

## 実Anthropic結果

| 項目 | 結果 |
|---|---|
| Claude Code完了 | 成功 |
| MCP tool calls | 4回、直列 |
| Claude Code turns | 5 |
| Anthropic成功stream | 5本 |
| wall time | 8,705.9 ms |
| 初回promptのmask | 成功 |
| 全4 tool resultのmask | 成功 |
| 最終responseの合成PERSON復元 | 成功 |
| client出力のalias非残存 | 成功 |
| Gateway logの合成原文非残存 | 成功 |

wall timeは外部service負荷、model生成、prompt cacheを分離できない観測値であり、性能保証値または
合否閾値ではありません。

## 実導通で見つかった差分

1. Anthropicの一時的な429は`application/json`で返る。従来は成功SSE用processorへ渡してJSONを
   壊し、Claude Codeにはsocket切断として見えていた。非2xx／非SSE bodyはprocessorを迂回する。
2. 実Anthropic SSEはHTTP圧縮され得る。従来の`aiter_raw()`は圧縮byteをUTF-8 decoderへ渡して
   `UnicodeDecodeError`になった。Content-Encodingを除去するresponse契約に合わせ、httpxで展開
   済みの`aiter_bytes()`を処理・転送する。
3. timeout時にClaude Codeだけを終了するとstdio MCP子processが残り得る。E2Eは専用process group
   を作り、期限超過時にgroup全体を終了する。
4. deny-by-defaultの子process環境でもmacOS Keychain認証に必要な`USER`／`LOGNAME`は保持する。
   認証値、任意custom header、proxy設定は継承しない。

429の診断中も、Gatewayは全11回の試行で固定合成PERSONを送信前にmaskしました。429は外部容量の
一時状態であり成功扱いにはしていません。最終evidenceは上表の独立した4 tool call成功実行です。

## ローカル回帰

| gate | 結果 |
|---|---|
| ruff | 成功 |
| mypy strict | 71 source files成功 |
| unit／evaluation | 699件成功、warning 1件 |
| mock upstream実process Gateway E2E | 4件成功 |
| 実Claude Code／実Anthropic E2E | 1件成功 |

warningはStarlette TestClientからhttpx2への将来移行を示すdeprecationで、test失敗またはsecurity
境界の縮小ではありません。
