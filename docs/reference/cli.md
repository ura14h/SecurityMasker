# CLIリファレンス

全commandとoptionを確認するための仕様書です。初めて導入する場合は
[導入ガイド](../getting-started.md)から始めてください。

この文書はSecurityMaskerの利用者向けcommandとoptionを網羅します。source版では
`securitymasker`を`python3 securitymasker.py`に、one-file版では`./securitymasker`に
読み替えられます。

## 共通仕様

- `-h`, `--help`: 対象commandのhelpを表示して終了します。
- `--version`: SecurityMaskerのversionを表示します。top-levelだけで指定できます。
- `--config PATH`: 使用する`securitymasker.config`を明示します。指定がなければ
  `SECURITYMASKER_CONFIG`、実行ファイルに隣接する`securitymasker.config`の順で探索します。
  current working directoryや親directoryは探索しません。
- commandを省略した場合はtop-level helpを表示し、終了code`0`で終了します。
- Gatewayは`gateway` commandを明示した場合だけ起動します。`--config`、`--host`、`--mode`、
  `--port`だけをtop-levelへ指定してもGatewayとは解釈せず、終了code`2`で拒否します。
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
安全のため`gateway` commandは省略できません。

### ログの読み方

Gatewayの状態と監査eventは、ANSI装飾や桁揃えを使わない次の一行形式で標準errorへ表示します。
監査eventには検出件数、不可逆なsession fingerprintなどの固定fieldだけを含め、processのmodeから
自明なprovider、prompt、元の機密値、alias対応表、認証情報は表示しません。

```text
2026-07-27 21:21:03 [info] gateway_started url=http://127.0.0.1:4000 mode=chatgpt
2026-07-27 21:22:06 [info] request_masked entity_count=3 session_fp=3ed714a9735a
```

先頭はlocal時刻、`[info]`や`[warning]`はlevel、その次がevent名、残りが
`key=value`形式のfieldです。1 processは一つのmodeだけを扱うため、各監査eventで自明な
providerは表示しません。modeは起動時の`gateway_started`で確認します。

| event | 意味 |
|---|---|
| `gateway_started` | `url`と`mode`でGatewayを起動した |
| `request_masked` | providerへの転送前にrequestのマスク処理が完了した |
| `request_blocked` | 安全検査、request形式、session解決などに失敗し、providerへ転送せず拒否した |
| `store_error` | session DBのreadiness、request処理、response bindingのいずれかに失敗した |
| `stream_error` | streaming responseの処理、取消し、response bindingのいずれかに失敗した |

`request_masked`はマスク処理の完了を示し、providerからのresponse成功までは意味しません。
`request_blocked`、`store_error`、`stream_error`の`reason`は、機密値ではなく固定された原因分類です。
`sm_`で始まるwarningはblock箇所を示す補助eventで、直前の監査eventと同じ一件を表す場合があります。

- `entity_count`: 現在のrequestでマスクした出現箇所数。同じ機密値が3箇所にあれば`3`であり、
  ユニーク値数やsession累計ではありません。クライアントが過去の会話をrequestへ再掲すると、
  過去の出現箇所もそのrequestで改めて数えます。
- `session_fp`: 元のsession IDを表示せず、同じマスキングsessionのlogを照合するための短い
  fingerprint。同じ値なら同じsessionとして処理されたことを示しますが、利用者IDや永続的な
  一意IDとしては使用できません。
- `mode`: `chatgpt`または`claude`。一つのprocessでは起動中に変わりません。
- `url`: clientが接続するloopback URLです。

### Gatewayの終了方法

foregroundで起動したGatewayは、クライアント操作を終えてから起動terminalで`Ctrl+C`を1回
入力し、shellのpromptが戻るまで待って終了します。background processには`SIGTERM`を送ります。
通常の終了で応答しない場合を除き、`SIGKILL`やterminalの強制終了は使用しないでください。

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
