# Testing

この文書は開発中のtest方法と、test dataを外部へ出さない条件を説明します。公開判断に使うgateは
[Release gate](release.md)、現在の結果は[開発・リリース状況](status.md)を参照してください。

## 利用者setupとtest setup

通常利用者はruntimeだけを導入します。

```console
./scripts/setup
```

開発者は別の入口でtest、lint、type check依存を追加します。

```console
./scripts/test-setup
```

mock upstream、合成credential、隔離HOME、network namespace、test-only環境変数は`devtools`と
`tests`だけに置き、通常運用手順や配布binaryへ混ぜません。

## 日常的な検証

```console
.venv/bin/ruff check src tests devtools
.venv/bin/mypy src
.venv/bin/python -m pytest -q tests/unit
.venv/bin/python -m pytest -q tests/evaluation
```

Windows native source targetの開発環境は、標準のcmd.exeから次を実行します。Windows対応判断が
完了するまでは合成dataだけを使います。

```bat
scripts\test-setup.cmd
.venv\Scripts\ruff.exe check src tests devtools
.venv\Scripts\mypy.exe src
.venv\Scripts\python.exe -m pytest -q tests\unit tests\evaluation
scripts\release-check.cmd
```

Windows setupは64-bit Python 3.12と`requirements-windows*.lock`のwheelだけを使用し、Visual Studio
Build Toolsやsource buildへfallbackしません。`release-check.cmd`はlocal unit／evaluation／mock
Gatewayまでのpre-release gateであり、Windows Firewallで外向き通信を遮断した専用standard userの
実Codex／Claude Code CLI E2Eを含みません。後者は別の必須gateとして実行し、両方の成功を確認するまで
Windows対応済みと判断しません。

### Windows実CLIのFirewall gate

operatorとCodex Desktopの通信を維持したまま実CLIだけを隔離するため、別のlocal standard userを
用意します。account／profile lifecycleとFirewall ruleのinstall／removeだけは管理者cmdで行います。
`setup`は固定名`SecurityMaskerTester`だけを作成し、passwordはcommand lineへ書かず`net user`の
promptから2回入力します。

```bat
scripts\windows-test-user.cmd setup
scripts\windows-firewall-gate.cmd install "%COMPUTERNAME%\SecurityMaskerTester"
```

Firewallをinstallする前に、そのuserのprofile内へsource archive、Python 3.12、test dependency、固定
NER model、Codex CLI、Claude Code CLIを準備します。別userからoperatorのprofileやCLI設定を共有せず、
試験user自身の隔離したpathを使用します。

新規作成した試験userへ最初にsign inし、checksum検証済みsource archiveをlocal fixed NTFS上へ展開
します。archive直下のcmdで次を実行します。このrunnerは固定user、standard user権限、`.git`／`.venv`と
既存製品dataがないfresh directory、reparse pointを含まないlocal fixed NTFSを確認してから、setup、
両mode init、doctor、preview、client config、local release gateを実行します。

```bat
scripts\windows-source-gate.cmd run
```

RDP sessionでuser切替ができない場合は、開発userのcmdから次のwrapperを実行します。Windows標準の
`runas /profile`がpasswordをpromptし、現在のRDP session内に`SecurityMaskerTester`のprofileとtokenを
使う別cmdを開きます。`/savecred`は使用しません。補助runnerがPython、CLI、checksum検証、fresh展開、
上記source gateまでを実行するため、長いbootstrap commandの手入力は不要です。

```bat
scripts\windows-source-gate-runas.cmd
```

この段階はdependencyとmodelを取得するためnetworkを使用します。成功後にsign outし、管理者cmdから
Firewall gateをinstallします。

試験userのcmdでは次を実行します。runnerはActiveStoreのIPv4／IPv6 deny rule、current user SID、
standard user権限を検査し、外部canaryが拒否された後だけ両CLIを起動します。CLIが既定path以外にある
場合は`SM_CODEX_CLI`／`SM_CLAUDE_CLI`へfull pathを設定します。

```bat
scripts\windows-firewall-gate.cmd verify
scripts\windows-cli-e2e.cmd
```

RDP sessionでは、試験userのsource archive gate用cmdを閉じた後、昇格した開発userのcmdから次だけを
実行します。固定user用Firewall ruleをinstallし、`runas /profile`で実CLI E2E用の別cmdを開きます。

```bat
scripts\windows-cli-e2e-runas.cmd
```

試験後は管理者cmdでtest userを完全削除します。この操作はgate固有のruleも削除します。user所有process、
service、scheduled task、load済みprofile／registry hiveがあれば削除せずfail-closedにします。profileは
local `Users`直下の固定user名と一致し、reparse pointでないことを確認してWindows profile APIから削除
します。任意pathの再帰削除は行いません。名前が同じでもgroupが一致しないFirewall ruleは削除しません。

```bat
scripts\windows-test-user.cmd remove
```

test userを保持してFirewall ruleだけを外す場合に限り、`scripts\windows-firewall-gate.cmd remove`を
使用します。

変更範囲に対応するtestを先に実行し、統合境界へ影響する場合はmock Gateway testも実行します。
この日常suiteは実providerへtest bodyを送りません。

WebSocketを含むsource Gatewayとlocal mockの実プロセスE2Eは明示的に実行します。

```console
SM_RUN_LIVE=1 .venv/bin/python -m pytest -q tests/integration/test_live_gateway.py
```

## Test data

- 実在人物、実際のsecret、API key、credentialをfixtureへ入れない。
- 通常testではproviderへ合成promptを含めて送らない。下記の明示opt-in実OpenAI／Anthropic
  E2Eだけを例外とし、testごとに固定した合成値以外を送らない。
- 最重要assertionは「上流が受けた最終payloadに元の合成機密値が存在しない」こと。
- streamingはaliasの全分割位置、tool argument delta、特殊文字をproperty testする。
- session並行性とresponse bindingでalias混在がないことを検査する。
- reject testだけでなくclean inputが受理されるtestも必ず置く。
- test時のHOME、config、state、model cacheを通常利用者の環境と分離する。

## 実CLIと実サーバ

実Codex／Claude Codeとlocal mockを使うegress検証は、全processを外向きinterfaceとdefault routeの
ないLinux network namespaceへ入れます。Codex側がHTTPへfallbackして成功しただけでは
WebSocketの証拠にならないため、mock upstreamの記録で`transport == "websocket"`をassertします。

```console
devtools/run_cli_e2e.sh
```

実OpenAIサーバとの互換性E2Eは、実Codex app-serverの既存ChatGPT認証を表示・複製せず、
command lineの一時config overrideで行います。このtestは外部送信とモデル利用を伴います。
固定した合成PERSONだけを送ることを確認し、明示的にopt-inした場合だけ実行します。

```console
SM_RUN_OPENAI_E2E=1 .venv/bin/python -m pytest -q \
  tests/integration/test_real_openai_e2e.py
```

標準では一つのCodex turn内でdynamic toolを8回直列実行します。4〜20回の範囲で変更できます。

```console
SM_RUN_OPENAI_E2E=1 SM_OPENAI_E2E_TOOL_CALLS=12 \
  .venv/bin/python -m pytest -q -s tests/integration/test_real_openai_e2e.py
```

成功条件は実Codexのturn完了、tool call数の一致、WebSocket接続数1、完了response数がtool
call数+1以上、各tool resultのmask、最終responseでの合成値復元、alias非残存のすべてです。
transportの比較を行う場合は、同じprocess条件で同一tool chainをWebSocket、HTTPの順に実行し、
wall timeの生値と差を記録します。

```console
SM_RUN_OPENAI_E2E=1 SM_OPENAI_E2E_COMPARE_HTTP=1 \
  SM_OPENAI_E2E_TOOL_CALLS=4 \
  .venv/bin/python -m pytest -q -s tests/integration/test_real_openai_e2e.py
```

外部serviceの負荷、prompt cache、生成時間をtransport固有の時間から分離できないため、単回の
大小関係は合否条件にしません。`serverOverloaded`だけはfresh Codex/Gateway processで1回再試行
しますが、leak block、protocol error、timeoutは再試行して成功扱いにしません。実行時のJSON
出力へ両transportのwall timeと短縮率を残し、一般的な性能保証値にはしません。
通常の利用者設定fileは変更せず、threadには`ephemeral`を指定します。transport互換性に
detector modelの揺らぎを混ぜないため、このtest専用の一時configだけ日本語NERを無効にし、
辞書で固定合成値を検出します。WebSocket接続数と完了response数はDEBUG eventで検証するため、
同じ一時configの`logging.level`だけを`DEBUG`にします。

実Anthropicサーバとの互換性E2Eは、実Claude Code CLIの既存認証を表示・複製せず、process
環境の`ANTHROPIC_BASE_URL`だけを一時Gatewayへ向けます。通常suiteではskipされ、外部送信と
モデル利用を明示的にopt-inした場合だけ実行します。

```console
SM_RUN_ANTHROPIC_E2E=1 .venv/bin/python -m pytest -q -s \
  tests/integration/test_real_anthropic_e2e.py
```

標準では一つのClaude Code session内でtest専用stdio MCPの`repeat_probe`を4回直列実行します。
2〜12回の範囲で変更でき、modelと最大利用額も一時環境から指定できます。

```console
SM_RUN_ANTHROPIC_E2E=1 SM_ANTHROPIC_E2E_TOOL_CALLS=6 \
  SM_ANTHROPIC_E2E_MODEL=sonnet SM_ANTHROPIC_E2E_MAX_BUDGET_USD=1.00 \
  .venv/bin/python -m pytest -q -s tests/integration/test_real_anthropic_e2e.py
```

成功条件は実Claude Codeの完了、tool call数の一致、初回promptと各tool resultを含むMessages
requestのmask、Anthropic SSE完了数がtool call数+1以上、最終responseでの固定合成PERSON復元、
alias非残存のすべてです。MCP probeは固定合成値と進捗だけを返し、built-in tool、他のMCP、
slash command、Chrome連携、session永続化は無効にします。作業directoryは空にし、user、project、
local settings sourceを読みません。providerの実payloadをtest側から保存することはできないため、
最終payloadのwire-level漏えいゼロはlocal mock E2Eで検証し、実provider E2Eでは送信直前の
`request_masked`件数と実stream完了を組み合わせて互換性を検証します。

認証は`claude auth login`済みのOAuth、`ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN`、または
`CLAUDE_CODE_OAUTH_TOKEN`を利用できます。testは認証値を表示、複製、file保存しません。
一時configでは日本語NERを無効にして固定辞書を使い、件数検証のため`logging.level`だけを
`DEBUG`にします。

## Desktopの扱い

自動testはCodex CLIとClaude Code CLIをDesktopのprotocol surrogateとして使います。DesktopとCLIが
共有する設定生成元をtestしますが、Desktop UIそのものを自動操作したとは表現しません。

手動Desktop smoke testを行う場合も合成promptだけを使い、結果をwire-level証明として扱いません。
