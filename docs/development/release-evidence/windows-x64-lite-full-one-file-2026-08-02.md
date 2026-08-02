# Windows x64 Lite／Full one-file technical spike検証記録

## 位置付け

この文書は、Windows nativeでLite版とFull版を同じPyInstaller specから生成し、profile固有のmodel
経路と共通Gateway機能を検証したtechnical spikeのevidenceです。clean machineでの検証、署名済み
artifact、Windows one-file版の対応表明、公開releaseのbinary gate完了を意味しません。

## 対象

- date: 2026-08-02
- tested code commit: `d287168eb7b39e4f598775d87d7bbace2b94b27d`
- OS／architecture: Windows 11 25H2 build 26200.8875 x64
- Python: 3.12.13 x64
- PyInstaller: 6.21.0
- Torch: 2.13.0+cpu
- model: `tsmatz/xlm-roberta-ner-japanese`
- revision: `aba094e118d5ffc622e9b25e07edc49f9dd85feb`
- binary profile: `lite`、`full`

実在人物、実際のsecret、実provider bodyを使用していません。Gateway E2Eはlocal mock upstreamと固定
合成PERSONだけを使用しました。生成artifactとbuild directoryはGitへcommitしていません。

## Build結果

Windows専用runtime lockとbuild lockから、Visual Studioやsource buildへfallbackしないclean build
venvをprofileごとに作成しました。両artifactはPE signature `0x00004550`、x64 machine `0x8664`です。

| profile | model配置 | size | SHA-256 |
|---|---|---:|---|
| Lite | binary外の検証済みlocal cache | 202,654,668 bytes（約193.3 MiB） | `65570bb1b80f764383ad4af80a886a3248d30a8c8c084ac04a1c3e0a299b247d` |
| Full | one-fileへ固定6 artifactを同梱 | 973,887,958 bytes（約928.8 MiB） | `3d9501d755f121d76be3c636ebaf7d55b3fc760619747b2715ee08f73d5f2d7d` |

`--version`はそれぞれ`securitymasker 0.1.0 (binary lite)`と
`securitymasker 0.1.0 (binary full)`を終了code 0で表示しました。両artifactとも未署名です。

## Profile別binary integration

Windows用の隔離`USERPROFILE`、`LOCALAPPDATA`、`APPDATA`、短い専用`TEMP`を各testへ渡しました。
binary processの`PATH`はWindows System32だけに制限し、`python.exe`が存在しないことを検査しました。
runtimeには`HF_HUB_OFFLINE=1`と`TRANSFORMERS_OFFLINE=1`を設定しています。

共通gateはhelp、profile付きversion、init、config validation、標準NER preview、ChatGPT／Claude両modeの
mock Gateway、SQLite永続化、上流原文ゼロ、response復元、Windows console control eventによる終了、
一時展開directory cleanupを検査しました。

- Lite: 空cacheでのfail-closedと原文非表示を確認後、binary自身の`model-load`で固定6 artifactを
  size／SHA-256検証し、`6 passed in 124.05s`
- Full: local model cacheを渡さず同梱modelだけを使用し、Lite専用のmodel不足testだけをskipして
  `5 passed, 1 skipped in 149.62s`

両gate終了後、専用TEMPに`_MEI*`は残りませんでした。

## Spikeで検出したWindows固有差分

1. Torch wheel内の深いlicense pathと長いbuild directoryの組合せが従来のWindows path上限へ達した。
   Windows runnerは製品dataとは別の短い内部build directoryを使うようにした。
2. pytestの長い一時pathではone-file展開先が同じ上限へ達した。Windows binary gateは短い専用TEMPを
   明示し、終了後の一時展開削除assertionを維持した。
3. `config-check`のUnicode em dashは、stdoutをpipeへ接続したWindows既定cp932でencodeできなかった。
   ASCIIの区切りへ変更し、cp932 encodeの回帰testを追加した。

## Source回帰

同じtreeで次を再実行しました。

| gate | 結果 |
|---|---|
| ruff | 成功 |
| mypy strict | 73 source files成功 |
| unit／evaluation | 754件成功、5件skip、既知warning 1件 |
| mock Gateway E2E | 4件成功 |

## 結論と未完了

Windows 11 x64でのLite／Full native build、profile別model経路、Pythonを解決できない子process環境、
共通binary機能のtechnical spikeは成功です。ただし公開には次が残ります。

- 新しいstandard userまたは同等のclean Windows環境でのbinary gate
- Authenticode署名と署名後artifactの再検証
- Lite／Fullへ同梱する全transitive componentのlicense inventoryとNOTICE検証
- Full版model weightの再配布判断
- 公開version、source tag、release note、公開artifact checksumとの対応

このためWindows one-file版は引き続き対応範囲外であり、生成artifactを第三者へ配布しません。
