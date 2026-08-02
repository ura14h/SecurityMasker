# CLIリファレンス

全commandとoptionを確認するための仕様書です。初めて導入する場合は
[導入ガイド](../getting-started.md)から始めてください。

この文書はSecurityMaskerの利用者向けcommandとoptionを網羅します。source版では仮想環境を有効にして
`securitymasker`を`python securitymasker.py`に、one-file版では`./securitymasker-lite`または
`./securitymasker-full`に読み替えられます。Windowsで仮想環境を有効にしない場合は、
`.venv\Scripts\python.exe securitymasker.py`を使用できます。

## 共通仕様

- `-h`, `--help`: 対象commandのhelpを表示して終了します。
- `--version`: SecurityMaskerのversionと、`source`／`binary lite`／`binary full`の配布形態を表示します。
  top-levelだけで指定できます。
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
通常は既存fileを一切変更せず拒否します。`--force`を明示した場合だけ、標準layoutを完全初期化
します。SQLite DBは最初のGateway起動時に作成します。

```console
securitymasker init [--directory DIRECTORY] [-f | --force] \
  [--mode chatgpt|claude] [--port PORT]
```

- `--directory DIRECTORY`: 作成先directory。source版の既定値は全OSでroot scriptと同じdirectoryです。
  Windows nativeではlocal fixed NTFSを要求し、管理対象artifactのowner、protected DACL、ACE、
  reparse pointを検査します。
- `-f`, `--force`: 指定directory直下の既存`securitymasker.config`、
  `securitymasker.dict`、`securitymasker.state/`を削除して再作成します。辞書、SQLite内の
  全session、response binding、alias対応表、master keyは復元できなくなります。このoptionでは
  対象を明示する`--directory`が必須です。
- `--mode {chatgpt,claude}`: configへ書く製品mode。既定値は`chatgpt`です。
- `--port PORT`: configへ書くloopback port。既定値は`4000`です。Claude用に別processを
  起動する例では`4001`を推奨します。

`--force`はGateway稼働中、標準state内に管理外entryがある場合、削除対象がsymlinkや別ownerの
場合に拒否します。古いconfigが参照するdirectory外の辞書やstate、client設定、NER model cache、
指定directory内のその他のfileは変更しません。必要な状態は実行前に一組としてbackupし、実行後は
古いaliasを使わずclientで新しい会話を開始してください。

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

Claude modeではmacOS／Linuxに`export ANTHROPIC_BASE_URL="..."`、Windowsのcmd.exeに
`set "ANTHROPIC_BASE_URL=..."`を表示します。現在のprocessへ手動適用するsnippetであり、永続環境や
Claudeの設定fileを変更しません。Codex modeは全platformで手動追記用TOMLを表示します。

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
2026-07-27 21:23:00 [info] gateway_stopped mode=chatgpt
```

先頭はlocal時刻、`[debug]`、`[info]`、`[warning]`、`[error]`はlevel、その次がevent名、残りが
`key=value`形式のfieldです。1 processは一つのmodeだけを扱うため、各監査eventで自明な
providerは表示しません。modeは起動時の`gateway_started`で確認します。

表示閾値は`securitymasker.config`の`logging.level`で指定します。既定の`INFO`ではINFO、WARNING、
ERRORを表示し、接続・通信の詳細は表示しません。

| level | 意味 | 主なevent |
|---|---|---|
| `INFO` | 製品の動作目標 | `gateway_started`、`request_masked`、`gateway_stopped` |
| `WARNING` | 現在のCodex request／接続は継続不能だが、Gatewayは次の処理を受けられる | `request_blocked`、`stream_error`、`sm_upstream_network_error`、`sm_response_stream_blocked` |
| `ERROR` | Gatewayの起動または正常動作を継続できない | `gateway_configuration_error`、`gateway_store_error`、`store_error`、`gateway_bind_failed`、`gateway_runtime_error` |
| `DEBUG` | 接続と通信statusを検証する詳細 | `sm_websocket_connected`、`sm_websocket_disconnected`、`sm_websocket_turn_completed`、`sm_upstream_stream_started`、`sm_upstream_stream_completed`、`sm_upstream_response_completed`、`sm_block_*` |

`request_masked`はマスク処理の完了を示し、providerからのresponse成功までは意味しません。
`request_blocked`、`store_error`、`stream_error`の`reason`は、機密値ではなく固定された原因分類です。
`sm_`で始まるDEBUG eventは接続状態、通信status、block箇所を示す補助情報で、直前の監査eventと
同じ一件を表す場合があります。Uvicorn固有のlifecycle/access logは抑止し、bind失敗などを
SecurityMaskerの固定eventへ変換します。

WebSocketの接続再利用を調べる場合は、一時的に次のように変更してGatewayを再起動します。

```yaml
logging:
  level: DEBUG
```

DEBUGには原文、alias対応表、認証情報を含めませんが、通常運用では高頻度になるため、確認後は
`INFO`へ戻します。

- `entity_count`: 現在のrequestでマスクした出現箇所数。同じ機密値が3箇所にあれば`3`であり、
  ユニーク値数やsession累計ではありません。クライアントが過去の会話をrequestへ再掲すると、
  過去の出現箇所もそのrequestで改めて数えます。
- `session_fp`: 元のsession IDを表示せず、同じマスキングsessionのlogを照合するための短い
  fingerprint。同じ値なら同じsessionとして処理されたことを示しますが、利用者IDや永続的な
  一意IDとしては使用できません。
- `mode`: `chatgpt`または`claude`。一つのprocessでは起動中に変わりません。
- `url`: clientが接続するloopback URLです。
- `duration_ms`: WebSocket上の個別response処理時間です。Codex turn全体やHTTP比較のwall
  timeではありません。

### Gatewayの終了方法

foregroundで起動したGatewayは、クライアント操作を終えてから起動terminalで`Ctrl+C`を1回
入力し、shellのpromptが戻るまで待って終了します。background processには`SIGTERM`を送ります。
通常の終了で応答しない場合を除き、`SIGKILL`やterminalの強制終了は使用しないでください。

## `securitymasker model-load`

固定revisionの日本語NER modelを明示的に取得し、manifestのsizeとSHA-256で検証します。
通常は`scripts/setup`が実行するため、手動復旧やmodel準備時だけ使用します。prompt処理中の
暗黙downloadは行いません。稼働中Gatewayをhot reloadするcommandではなく、次回起動時に
読み込める検証済みlocal artifactを準備するcommandです。

one-file Lite版では初回利用前の必須準備です。Full版は同じ固定modelを同梱するため通常は不要です。

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
