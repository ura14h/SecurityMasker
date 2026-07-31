# 通信経路を詳しく検証する

この手順は、実CodexがSecurityMaskerを通り、外部modelが合成値そのものではなくaliasを受け取った
ことを目視確認するための高度な診断です。通常の導入確認は
[導入ガイド](../getting-started.md#6-接続状態を確認する)までで構いません。

実在人物や実際のsecretは使わず、辞書へ登録した合成値だけを使います。

## 1. Local previewを確認する

```console
python3 securitymasker.py preview "担当者: 山田太郎"
```

`PERSON: 1`と`SM_PERSON_...`が表示されることを確認します。検出されない場合は外部clientへ
進まず、辞書を修正してGatewayを再起動します。

## 2. Codexへ診断promptを送る

SecurityMaskerをproviderにした新しいCodex taskへ、次を送ります。標準設定の
`tool_trust.trusted_local_tools: []`を前提とします。shell toolをtrusted toolへ追加している場合は
この方法を使えません。

````text
これは通信経路の診断です。

「診断対象」の値について、あなたが実際に受け取った表記を一切変更せず、
下記コマンドの最後の引数 VALUE として渡してください。

自分で文字列を分解・変換・推測してはいけません。
必ずlocal shell toolを1回だけ使用してください。
VALUEを、あなたが受け取った値で完全に置き換えてください。

```console
python3 -c 'import json,sys; s=sys.argv[1]; print(json.dumps({"route":"MASKED_ALIAS" if s.startswith("SM_PERSON_") else "UNMASKED_TEXT","length":len(s),"ascii":s.isascii(),"characters":list(s)},ensure_ascii=False))' 'VALUE'
```

最後の回答は、shellの標準出力を変更せずJSON code blockで返してください。
説明は不要です。

診断対象: 山田太郎
````

外部modelがaliasを受け取っていれば、`route`は`MASKED_ALIAS`になります。文字をJSON配列へ
分解しているため、この診断結果自体はresponse復元の対象になりません。

`UNMASKED_TEXT`になった場合は実データへ進まず、Codexが読む`config.toml`の`model_provider`、
Gatewayの`base_url`、起動時の`CODEX_HOME`を確認します。

`securitymasker.config`の`logging.level`を一時的に`DEBUG`へ変更してGatewayを再起動し、同じ
操作時刻に`sm_websocket_connected`があればResponses WebSocketが成立しています。このeventは
原文を含まず、不可逆なsession fingerprintだけを表示します。確認後は`INFO`へ戻します。

## 3. Response復元を確認する

同じ合成値を「一字一句そのまま返す」よう依頼し、Codexの表示上で`山田太郎`へ戻ることを
確認します。

この手順はLLMとtoolの動作を利用する目視確認であり、wire-levelの証明ではありません。厳密な
egress検証は、外向きnetworkを遮断し、実CLIとlocal mock upstreamを使うrelease gateで行います。

開発・release担当者が実OpenAIサーバとの互換性も検証する場合は、外部送信とモデル利用を理解した
うえで、[Testing](../development/testing.md#実cliと実サーバ)の明示opt-in E2Eを実行します。
通常利用者の導入確認には不要です。
