# 導入ガイド

全commandとoptionは[CLIリファレンス](cli-reference.md)を参照してください。

## 1. 展開する

source版はPython 3.11以上を使います。検証済み環境はmacOS arm64とLinux arm64です。
Windowsはsetup、Windows ACLによる機密file保護、PowerShell向けclient設定、native E2Eが
未実装・未検証のため、現在は対応対象外です。Windows上の実データ利用について安全性を保証しません。
Windows 11上のWSL2またはDocker DesktopでLinux版を評価する未サポートの手順は、
[Windows番外編](getting-started-windows.md)へ分離しています。

公開repositoryの `Code` メニューからcloneするか、Releaseのsource archiveを展開します。
以降はrepository rootで実行します。

```console
./scripts/setup
. .venv/bin/activate
```

setupは固定lockからruntimeを導入し、固定revisionの日本語NER modelを取得してSHA-256を
検証します。既定の `python3` が古い環境ではPATH上の `python3.12`、次に `python3.11` を
自動選択します。別のinterpreterを使う場合だけ
`PYTHON_COMMAND=/path/to/python3.11` を指定します。
通常運用中にdownloadは行いません。

配布済みone-file版を使う場合、Pythonとsetupは不要です。以下の `python3 securitymasker.py` を
`./securitymasker` に読み替えます。

## 2. 初期化して辞書を調整する

Codex用:

```console
python3 securitymasker.py init --mode chatgpt --port 4000
```

Claude Code用:

```console
python3 securitymasker.py init --mode claude --port 4001
```

`securitymasker.dict` の合成例を削除または編集し、組織固有の人名、組織名、顧客名、
project名を登録します。辞書は1ファイルだけです。設定と辞書には機密が含まれ得るため、
SecurityMaskerはPOSIX環境でowner以外が読める権限を拒否します。

外部へ送らず確認できます。

```console
python3 securitymasker.py preview "株式会社極秘技研の山田太郎です"
python3 securitymasker.py preview < prompt.txt
python3 securitymasker.py config-check
```

`preview` は元の入力を再表示せず、mask後の文字列と検出件数だけを表示します。実際のpromptは
shell historyやprocess一覧へ残さないよう、ファイルのredirectまたはpipeで標準入力から渡します。

## 3. Gatewayを起動する

```console
python3 securitymasker.py gateway
```

引数を省略すると、CLI指定、`SECURITYMASKER_CONFIG`、実行ファイルに隣接する
`securitymasker.config` の順で探索します。current working directoryや親directoryは探索しません。

一時的な上書きもできます。

```console
python3 securitymasker.py gateway --config /path/to/chatgpt.config \
  --mode chatgpt --port 4000
```

hostは `127.0.0.1`、`::1`、`localhost` だけです。public bind、複数worker、
共有serverとしての利用には対応しません。

## 4. クライアントを設定する

別terminalで設定snippetを生成します。このcommandはファイルを変更しません。

```console
python3 securitymasker.py client-config
```

### Codex

出力を、Codex CLIまたはCodex appが読む `config.toml` へ手動で反映します。

```toml
model_provider = "securitymasker"

[model_providers.securitymasker]
name = "SecurityMasker Gateway"
base_url = "http://127.0.0.1:4000"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = false
```

modelはクライアント側で選択します。SecurityMaskerはrequestの `model` を変更しません。

`model_provider` はTOMLのtop-level fieldです。既存 `config.toml` の末尾が
`[shell_environment_policy.set]` などのtableである場合、その直後へ
`model_provider = "securitymasker"` だけを追記すると直前のtable所属になり、Codexは既定providerを
使い続けます。top-level fieldは最初の `[table]` より前へ置いてください。誤例と確認方法は
[clientがproxyを通らない](troubleshooting.md#clientがproxyを通らない)を参照してください。

### Claude Code

Claude Code CLIまたはClaude Code Desktopを起動する環境へ設定します。

```console
export ANTHROPIC_BASE_URL="http://127.0.0.1:4001"
```

環境変数の永続化方法はOSや起動方法に合わせてください。SecurityMaskerは認証情報を生成せず、
Claude自身の認証headerを上流へ透過します。

## 5. 起動前後を確認する

```console
python3 securitymasker.py doctor
python3 securitymasker.py doctor --require-ready
```

`doctor` はconfig、辞書、key、port、NER、client設定、Gateway readinessをread-onlyで検査し、
実providerへrequestを送りません。

### 実Codexでマスクと復元を目視確認する

最初の確認には実在人物や実際のsecretを使わず、辞書へ登録した合成値だけを使います。次の例では
test suiteでも合成fixtureとして使う `山田太郎` を利用します。先にlocal `preview` を実行し、
`PERSON: 1` と `SM_PERSON_...` が表示されることを確認してください。検出されない場合は実Codexへ
送らず、合成値を `securitymasker.dict` へ登録してGatewayを再起動します。

```console
python3 securitymasker.py preview "担当者: 山田太郎"
```

SecurityMaskerをproviderにした新しいCodex taskへ、次の診断promptを送ります。このpromptは、
modelへplaceholderを変形させずlocal shell toolの引数へ渡させ、決定論的なPythonで文字列を
分解します。標準設定の `tool_trust.trusted_local_tools: []` を前提とし、shell toolをtrusted
local toolへ追加している場合はこの確認方法を使えません。

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

マスク済みaliasをmodelが受け取っていれば、shell出力は次の形になります。文字間にJSONの区切りが
入るため、完全一致だけを対象とするresponse復元で元の値へ戻りません。

```json
{
  "route": "MASKED_ALIAS",
  "length": 22,
  "ascii": true,
  "characters": ["S", "M", "_", "P", "E", "R", "S", "O", "N", "_", "..."]
}
```

`route` が `UNMASKED_TEXT` になった場合は実データへ進まず、Codexが実際に読むuser-level
`config.toml` の `model_provider` とGatewayの `base_url`、起動時の `CODEX_HOME` を確認します。
続けて同じ合成値を「一字一句そのまま返す」よう依頼し、Codex画面で `山田太郎` に戻ることを
確認すればresponse復元も目視できます。

この手順は、実Codexの通常経路でmodelがaliasを受け取り、local表示で復元されることを利用者が
確認するsmoke testです。LLMとtoolの動作を利用するためwire-levelの証明ではありません。厳密な
egress検証は、外向きnetworkを遮断した環境で実CLIとlocal mock upstreamを使うrelease gateで
行います。

## 6. Gatewayを終了する

通常はクライアントでの操作を終えてから、Gatewayを起動したterminalへ戻り、`Ctrl+C`を1回
入力します。shutdown処理が終わってshellのpromptが戻るまで待ってください。ChatGPT用と
Claude用を別processで起動している場合は、それぞれのterminalで終了します。

バックグラウンドで起動した場合は、そのGateway processへ`SIGTERM`を送ります。通常の終了で
応答しない場合を除き、`SIGKILL`やterminalの強制終了は使用しないでください。Gatewayの終了後も
client設定は残るため、次に利用するときは同じconfigでGatewayを再起動します。

## 両方を同時に使う

同じ辞書を共有しても構いませんが、config、state directory、DB、key、portはmodeごとに分けます。

```text
SecurityMasker/
├── securitymasker.py
├── securitymasker.dict
├── chatgpt.config
├── chatgpt.state/
│   ├── securitymasker.db
│   └── securitymasker.key
├── claude.config
└── claude.state/
    ├── securitymasker.db
    └── securitymasker.key
```

それぞれ `--config` で指定して別プロセスを起動してください。同じDBを2プロセスで開くと、
writer leaseにより後から起動した側を拒否します。
