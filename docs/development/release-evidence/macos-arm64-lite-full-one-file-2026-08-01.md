# macOS arm64 Lite／Full one-file technical spike検証記録

## 位置付け

この文書は、モデル非同梱のLite版と固定モデル同梱のFull版を同じPyInstaller build基盤から生成し、
profile固有のmodel準備と共通Gateway機能を検証したtechnical spikeのevidenceです。公開releaseの
binary gate完了、署名済みartifact、macOS one-fileの対応表明を意味しません。

## 対象

- date: 2026-08-01
- tested commit: `e686cfe`
- OS／architecture: macOS 26.5.2 arm64
- Python: 3.12.13
- PyInstaller: 6.21.0
- model: `tsmatz/xlm-roberta-ner-japanese`
- revision: `aba094e118d5ffc622e9b25e07edc49f9dd85feb`
- binary profile: `lite`、`full`

実在人物、実際のsecret、実provider bodyを使用していません。binary Gateway E2Eはlocal mock upstreamと
固定合成PERSONだけを使用しました。buildとtestで生成したartifactはlocal `dist/`だけに置き、Gitへ
commitしていません。

## Build結果

| profile | model配置 | size | SHA-256 |
|---|---|---:|---|
| Lite | binary外の検証済みlocal cache | 188,764,560 bytes（約180 MiB） | `eb7eee6e78589409117d157b0c6c7b342debbcd78b57c064167406c6ce07d564` |
| Full | one-fileへ固定6 artifactを同梱 | 961,502,400 bytes（約917 MiB） | `dcd7d9ef2a5676646ab8b23117a36b430607503079d2c4c27266e2940e3933d1` |

`securitymasker-lite --version`は`securitymasker 0.1.0 (binary lite)`、
`securitymasker-full --version`は`securitymasker 0.1.0 (binary full)`を表示しました。成果物名、指定profile、
埋め込みmetadataは一致しています。

## Lite版gate

Lite版の初回`model-load`で、Hugging Face clientが起動するmultiprocessing resource trackerを
PyInstaller executableが通常CLIとして誤処理する問題を検出しました。binary entry pointでCLI import
より先に`multiprocessing.freeze_support()`を実行するよう修正し、同じbinaryから再実行しました。

修正後は次を確認しました。

1. 空の`HF_HOME`では標準NERを黙って無効化せず、`model-load`を案内してpreviewを非zeroで拒否する。
2. 失敗出力へ固定合成PERSONを表示しない。
3. binary自身の`model-load`が6 artifactを取得し、sizeとSHA-256を検証する。
4. 取得後は同じcacheを使って標準NER previewを完了する。
5. ChatGPT／Claude両modeのmock Gatewayでrequest mask、response restore、SQLite永続化、上流原文ゼロ、
   SIGTERM終了、一時展開directory cleanupを完了する。

最終binary integration結果は`6 passed in 116.59s`でした。

## Full版gate

Full版はbuild時とruntime load時に同梱6 artifactをmanifestへ照合しました。隔離HOMEにはmodel cacheを
用意せず、同梱modelだけで標準NER previewとChatGPT／Claude両modeのmock Gateway E2Eを完了しました。
Lite専用のmodel不足testだけをskipし、結果は`5 passed, 1 skipped in 135.34s`でした。

## Source回帰

同じcommitで次を実行しました。

| gate | 結果 |
|---|---|
| ruff | 成功 |
| mypy strict | 72 source files成功 |
| unit／evaluation | 706件成功 |
| mock Gateway E2E | 4件成功 |

mock Gateway E2Eの最初の全件実行ではClaude streaming 1件がlocal leak guardで安全側にblockされ、上流の
recordへ当該requestが到達しませんでした。単独再現では成功し、続く全4件の再実行も成功しました。
原文が上流へ到達した観測はありません。公開release evidenceでは、clean snapshot上の一回の正式gateを
別途実行して記録します。

## 未完了

- macOS Developer ID署名とnotarization
- 物理または同等のclean macOS machine gate
- Lite／Fullへ同梱する全transitive componentのlicense inventoryとNOTICE検証
- Full版model weightの再配布判断
- 公開version、source tag、release note、公開artifact checksumとの対応

したがってLite／Fullの設計、macOS native build、profile別model経路、共通機能のtechnical spikeは成功です。
Lite版からmodel weight再配布blockerは分離できましたが、どちらも公開可能とは判断しません。
Linux側の同日検証は
[Linux arm64 Lite／Full evidence](linux-arm64-lite-full-one-file-2026-08-01.md)に記録しています。
