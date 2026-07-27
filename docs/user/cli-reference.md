# CLIリファレンス

この文書はSecurityMaskerの利用者向けcommandとoptionを網羅します。source版では
`securitymasker`を`python3 securitymasker.py`に、one-file版では`./securitymasker`に
読み替えられます。

## 共通仕様

- `-h`, `--help`: 対象commandのhelpを表示して終了します。
- `--version`: SecurityMaskerのversionを表示します。top-levelだけで指定できます。
- `--config PATH`: 使用する`securitymasker.config`を明示します。指定がなければ
  `SECURITYMASKER_CONFIG`、実行ファイルに隣接する`securitymasker.config`の順で探索します。
  current working directoryや親directoryは探索しません。
- commandを省略した場合は`gateway`を実行します。先頭が`--config`、`--host`、`--mode`、
  `--port`の場合も`gateway`のoptionとして扱います。
- option値にpromptや機密値を直接指定しないでください。shell historyやprocess一覧へ残る
  可能性があります。

終了codeは、成功が`0`、設定・安全検査・実行時診断の失敗が`1`、argparseによる不正な
command/optionや一部の必須準備不足が`2`です。

## `securitymasker init`

隣接するconfig、単一辞書、state directory、256-bit master keyを新規作成します。
既存fileは上書きせず、SQLite DBは最初のGateway起動時に作成します。

```console
securitymasker init [--directory DIRECTORY] [--mode chatgpt|claude] [--port PORT]
```

- `--directory DIRECTORY`: 作成先directory。既定値は実行ファイルまたはroot scriptと同じ
  directoryです。
- `--mode {chatgpt,claude}`: configへ書く製品mode。既定値は`chatgpt`です。
- `--port PORT`: configへ書くloopback port。既定値は`4000`です。Claude用に別processを
  起動する例では`4001`を推奨します。

## `securitymasker config-check`

configと参照辞書を読み、schema、値、権限、環境変数参照を検証します。Gatewayは起動せず、
providerにも接続しません。

```console
securitymasker config-check [--config PATH]
```

- `--config PATH`: 検証するconfigを明示します。

## `securitymasker entities`

設定済みentityとpatternのID、type、replacement profile、restore policy、variant件数を
表示します。辞書の実値は表示しません。

```console
securitymasker entities [--config PATH]
```

- `--config PATH`: 読み込むconfigを明示します。

## `securitymasker preview`

Gatewayと同じdetector・policyでtextをlocal maskし、mask後の文字列とentity type別の検出件数を
表示します。外部送信は行わず、元の入力を別途列挙しません。

```console
securitymasker preview [TEXT] [--config PATH]
```

- `TEXT`: 確認対象のtext。省略時は標準入力を最後まで読みます。引数へ書くとshell historyや
  process一覧へ残り得るため、実際のpromptは標準入力から渡してください。
- `--config PATH`: 使用するconfigを明示します。

```console
securitymasker preview < prompt.txt
generate-prompt | securitymasker preview
```

対話端末で`TEXT`を省略した場合と、標準入力が空の場合は、待機せず終了code`2`になります。

## `securitymasker client-config`

現在のmodeとportに対応するCodexまたはClaude Codeの設定snippetをstdoutへ
表示します。利用者の設定fileは変更しません。

```console
securitymasker client-config [--config PATH]
```

- `--config PATH`: snippetの生成元configを明示します。

## `securitymasker doctor`

Python、dependency、config、辞書、key、detector、NER、port、Gateway readiness、client設定を
read-onlyで診断します。providerへpromptを送りません。

```console
securitymasker doctor [--json] [--gateway URL] [--require-ready] [--config PATH]
```

- `--json`: 機械処理用JSONを表示します。機密値は含めません。
- `--gateway URL`: probe先Gatewayを明示します。このoptionを指定した場合、到達不能はwarning
  ではなくfailureになります。
- `--require-ready`: Gatewayが未起動・unreadyならfailureにします。監視や起動確認向けです。
- `--config PATH`: 診断するconfigを明示します。

FAILが一つでもあれば終了code`1`、warningだけなら`0`です。

## `securitymasker gateway`

一つのmode・一つのloopback portでmasking proxyを起動します。

```console
securitymasker gateway [--mode chatgpt|claude] [--host HOST] [--port PORT] \
  [--config PATH]
```

- `--mode {chatgpt,claude}`: `runtime.mode`を今回のprocessだけ上書きします。
- `--host {127.0.0.1,::1,localhost}`: `runtime.host`を一時上書きします。public bindは
  指定できません。
- `--port PORT`: `runtime.port`を今回のprocessだけ上書きします。
- `--config PATH`: 使用するconfigを明示します。

CLI overrideはconfig fileを書き換えません。優先順位はCLI、config、組込み既定値です。
ChatGPTとClaudeを同時に使う場合は、別config・別DB・別key・別portで2process起動します。

foregroundで起動したGatewayは、クライアント操作を終えてから起動terminalで`Ctrl+C`を1回
入力し、shellのpromptが戻るまで待って終了します。background processには`SIGTERM`を送ります。
通常の終了で応答しない場合を除き、`SIGKILL`やterminalの強制終了は使用しないでください。

次の省略形も同じ意味です。

```console
securitymasker
securitymasker --config /path/to/securitymasker.config --port 4000
```

## `securitymasker model-load`

固定revisionの日本語NER modelを明示的に取得し、manifestのsizeとSHA-256で検証します。
通常は`scripts/setup`が実行するため、手動復旧やmodel準備時だけ使用します。prompt処理中の
暗黙downloadは行いません。稼働中Gatewayをhot reloadするcommandではなく、次回起動時に
読み込める検証済みlocal artifactを準備するcommandです。

```console
securitymasker model-load [--model MODEL] [--revision REVISION] \
  [--allow-unverified] [--config PATH]
```

- `--model MODEL`: model ID。省略時はconfigの`detectors.japanese_ner.model`を使います。
- `--revision REVISION`: model revision。省略時はconfigの
  `detectors.japanese_ner.revision`を使います。
- `--allow-unverified`: manifestのないmodelを未検証のまま受理します。供給網検証を無効化する
  危険な開発用optionで、通常運用では使用しないでください。
- `--config PATH`: model/revisionの既定値を読むconfigを明示します。

modelまたはrevisionをconfigからも解決できない場合は終了code`2`、取得・検証失敗は非zeroです。
