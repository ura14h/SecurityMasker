# 導入ガイド

全commandとoptionは[CLIリファレンス](cli-reference.md)を参照してください。

## 1. 展開する

source版は Python 3.11 以上を使います。

```console
git clone <repository-url> SecurityMasker
cd SecurityMasker
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

ChatGPT/Codex用:

```console
python3 securitymasker.py init --mode chatgpt --port 4000
```

Claude Code/Desktop用:

```console
python3 securitymasker.py init --mode claude --port 4001
```

`securitymasker.dict` の合成例を削除または編集し、組織固有の人名、組織名、顧客名、
project名を登録します。辞書は1ファイルだけです。設定と辞書には機密が含まれ得るため、
SecurityMaskerはPOSIX環境でowner以外が読める権限を拒否します。

外部へ送らず確認できます。

```console
python3 securitymasker.py preview "株式会社極秘技研の山田太郎です"
python3 securitymasker.py config-check
```

`preview` は元の入力を再表示せず、mask後の文字列と検出件数だけを表示します。

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

### ChatGPT/Codex

出力を、使用中のChatGPT/Codexが読む `config.toml` へ手動で反映します。

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

### Claude Code/Desktop

Claudeを起動する環境へ設定します。

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
