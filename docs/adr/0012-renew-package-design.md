# ADR-0012 — ローカルデスクトップ向けパッケージ設計の再構築

> config schemaの番号と旧形式の互換性については、後続の
> [ADR-0016](0016-reset-config-schema-version.md)が本ADRを置き換える。本書のv2表記は
> 採用時点の履歴として残す。
>
> 明示的な完全初期化については、後続の
> [ADR-0019](0019-add-explicit-destructive-init.md)が本ADRの`--force`禁止を置き換える。

- 状態：採用
- 日付：2026-07-26
- 変更対象：現行の両provider同居Gateway、`securitymasker run` 中心の起動、memory/Redis store、
  Docker/GitHub Actions/PyPIを前提にした配布、optional NER
- 置換・修正するADR：
  [ADR-0004](0004-presidio-in-process.md) の Presidio 採用を撤回し、
  [ADR-0008](0008-tenant-user-identity.md) のmulti-tenant構成を標準製品範囲から外す。
  [ADR-0009](0009-japanese-ner-backend.md) の採用backendは維持するが、optional・既定OFFから
  標準搭載・既定ONへ変更する

## 背景

現行実装は初期briefと監査是正を積み重ねた結果、個人がローカルPCへ展開してすぐ使う製品に
対して、次の運用要素を抱えている。

- CodexとClaude Codeのrouteを一つのGatewayが同時公開する
- wrapperがprocessごとのclient設定と独自session headerを注入する
- 再起動を越えた永続化にはRedis、暗号鍵、Docker/Composeを使う
- GitHub Actions、Docker、PyPI installを中心にした文書が利用者導線と混在する
- 未登録の日本語人名・法人名・地名を補完するNERがoptional・既定OFFである
- sourceから使う利用者とone-file binaryを使う利用者の契約が定まっていない

目標利用者は、ローカルのCodex appまたはClaude Code Desktopを
SecurityMaskerへ向け、promptをmaskして送り、responseを同じ対応表で復元したい個人である。
複数worker、複数host、複数利用者の共有基盤は目標ではない。

利用者は「機密情報がmaskされる」ことを期待する。binary sizeを理由に標準検出能力からNERを
外すと製品価値を損なう。一方、社内だけで意味を持つ任意のproject名までlocal modelが完全に
推測することはできないため、標準NERとユーザー辞書を重ね、検出能力の境界を正確に示す必要がある。

## 決定

### 1. 製品範囲

製品を、単一利用者のローカルPCで動かす可逆マスキングproxyへ絞る。

- user-facing modeは`chatgpt`と`claude`
- 内部protocol名は`openai_responses`と`anthropic_messages`
- 1 processは1 mode、1 port、1 workerだけを提供
- `chatgpt` modeはOpenAI Responses routeだけを公開
- `claude` modeはAnthropic Messages routeだけを公開
- wrong-protocol routeと未知routeはlocalで拒否し、上流へ送らない
- ChatGPTとClaudeを使う場合は同じsource/binaryを別configから2 process起動
- public bind、multi-tenant、multi-worker、複数host storeは標準製品範囲から外す
- cloud/remote sessionや通常のChatGPT会話など、localhost proxyを通らない通信は保証しない

`codex`はmode名に使わず、共有設定とprotocol compatibilityを検証するCLI executable名として残す。

### 2. source実行とbinary実行

次を同等の利用経路として提供する。

```console
python3 securitymasker.py gateway
./securitymasker gateway
```

root `securitymasker.py` は `src/securitymasker` のCLIを呼ぶ薄いlauncherとし、製品logicを複製しない。
source実行では`./scripts/setup`がvenv、固定依存、固定NER modelの取得とdigest検証を行う。
application起動時に暗黙の`pip install`や未検証downloadを行わない。

PyInstaller one-file binaryはPython runtime、runtime dependency、日本語NER runtime、検証済みmodel
artifactを含む。binary sizeより標準の検出能力を優先する。PyInstallerはcross-compilerでは
ないため、対応OSごとにnative buildとclean-machine testを行う。

### 3. 隣接configを設定のrootにする

`securitymasker.config`を唯一の設定rootとする。拡張子は`.config`だが内容はstrict YAMLである。
runtime、state、masking policy、detector parameter、単一辞書への参照を配下へ置く。

```yaml
version: 2

runtime:
  mode: chatgpt
  host: 127.0.0.1
  port: 4000

state:
  database: ./securitymasker.state/securitymasker.db
  key: ./securitymasker.state/securitymasker.key

dictionary: ./securitymasker.dict

detectors:
  secrets:
    enabled: true
  formats:
    enabled: true
  japanese_ner:
    enabled: true
```

config探索順はCLI `--config`、`SECURITYMASKER_CONFIG`、実行されたbinaryまたはroot scriptと同じ
directoryの`securitymasker.config`とする。current working directoryや任意の親directoryは
自動探索しない。PyInstaller one-fileでは一時展開先ではなく`sys.executable`のdirectoryを使う。
見つからなければfail-closedで起動を拒否する。

標準配置は次とする。配布形態に応じて`securitymasker`と`securitymasker.py`の片方だけでもよいが、
どちらも同じ隣接configを読む。

```text
SecurityMasker/
├── securitymasker
├── securitymasker.py
├── securitymasker.config
├── securitymasker.dict
└── securitymasker.state/
    ├── securitymasker.db
    └── securitymasker.key
```

modeとportはconfigに既定値を置き、CLI optionは一時的な上書きとして扱う。優先順位は
CLI > config > 安全な組込み既定値である。modeはconfig/CLIのどちらにも無ければ拒否する。
LLM model IDはclientが選択する値であり、proxyはrequestの`model`を暗黙に変更しない。

### 4. ユーザー辞書は単一ファイルへ分離する

`securitymasker.dict`をYAML形式のユーザー辞書とし、entitiesとユーザー定義patternsを格納する。
configは辞書を1ファイルだけ明示的に参照する。

- include、glob、directory探索、複数辞書mergeは実装しない
- 相対パスは`securitymasker.config`のdirectoryを基準に解決
- 欠落、構文違反、ID重複、矛盾する定義は起動時に拒否
- 辞書は機密ファイルとしてuserだけが読める権限を要求
- API key等は平文登録より`value_from_env`を推奨
- 二つのmodeから同じ辞書を参照することは許可

### 5. SQLiteとmaster keyは1対1にする

modeごとに別のSQLiteとmaster keyを使い、両modeで同じDB/keyを共有しない。パスはconfigへ
明示する。

```yaml
state:
  database: ./securitymasker.state/securitymasker.db
  key: ./securitymasker.state/securitymasker.key
```

SQLite標準extensionによるfile全体の暗号化には依存せず、`MaskingSession`全体を
AES-256-GCMで封緘したblobとして保存する。

- master keyをSQLite内へ平文保存しない
- DBに保存できるのはkey ID、salt、検証値など非secret metadataだけ
- session/response lookup keyはmaster keyによるHMACとし、raw IDをDB keyにしない
- DB作成時にrandom database IDとmodeをmetadataへ記録
- AADにschema version、database ID、mode、record type、lookup keyを含める
- DB作成modeと起動modeが違えば拒否
- wrong key、tamper、schema不一致、DB障害はfail-closed
- 同じDBを使う二重起動はactive writer leaseで拒否
- idle/absolute TTLとtransactional migrationを実装

ChatGPTとClaudeを同時に使う場合の例：

```text
securitymasker-chatgpt.config
securitymasker-chatgpt.state/
  securitymasker.db
  securitymasker.key
securitymasker-claude.config
securitymasker-claude.state/
  securitymasker.db
  securitymasker.key
securitymasker.dict
```

### 6. `init`がconfig、辞書、keyを生成する

`securitymasker init`は次を一度だけ行う。

- starter `securitymasker.config`を生成
- 単一のstarter `securitymasker.dict`を生成
- configに明記したstate directoryをuser専用権限で生成
- DBごとの256 bit `securitymasker.key`をCSPRNGで生成
- keyの実値をstdout、stderr、log、例外へ出さない
- SQLiteは作成せず、最初のGateway起動時にschemaとmode metadataを生成

既存config、辞書、DB、keyを暗黙に上書きしない。採用時点では`--force`でも既存DBに対応するkeyを
交換しない方針だったが、明示的な完全初期化に限ってADR-0019がこの判断を置き換える。
DBがあるのにkeyが無い、keyが不正、DB/keyが一致しない場合は新しいkeyを生成して続行せず拒否する。
rekeyは将来必要になった場合に専用のatomic migration commandとして設計する。

### 7. 日本語NERを標準保護層にする

ADR-0009で選定した日本語NER backendを標準binary/source setupへ含め、既定ONにする。
Presidioは削除する。

- 辞書、決定論的detector、日本語NERを重ねる
- model欠落、digest不一致、load失敗、推論失敗を黙ってNER無しへdowngradeしない
- NERの上限制御とfail-closedはADR-0011を維持
- model、revision、全artifact SHA-256、license情報をmanifestへ固定
- source setupとbinary buildは同じ取得・検証処理を使う
- model weightをGitへcommitしない
- release前にmodel、base model、学習datasetの再配布条件を確認

NERを含めても、組織固有の任意語を100%推測するとは主張しない。一般的な固有表現はNER、
形式的secret/PIIは決定論的detector、組織固有語はユーザー辞書という責任分担を利用者文書へ
明記する。

### 8. 配布・repository運用

- Redis、Docker、Docker Compose、GitHub Actions、PyPI公開を標準製品範囲から外す
- release gateはlocal scriptで再現する
- source checkoutと手動GitHub Release用one-file artifactを作成できる
- binaryをGit repositoryへcommitしない
- mock upstream、dummy credential、test-only knobを通常運用と配布binaryへ含めない
- 利用者向け文書、設計/ADR、開発/test文書を分離する

## 実装計画とcommit境界

各Phaseを独立して実装・検証・commitする。後続Phaseの変更を同じcommitへ混ぜず、各合格条件を
満たしてから次へ進む。

### Phase 0 — 現行ルールの切替

- 本ADRを`AGENTS.md`の最新製品方針へ反映
- ADR-0004を撤回、ADR-0008の適用範囲変更、ADR-0009の標準ON化を各文書へ反映
- `doc/07-Remediation-Status.md`に旧構成の最終状態と移行開始を記録

合格条件：

- `AGENTS.md`、ADR-0012、現行statusに起動方式・store・NER・配布方針の矛盾がない
- 機密を外部へ送らない、sessionを混ぜない、構造を壊さない、fail-closed等の不変条件は維持

### Phase 1 — config/dictionary/init/source launcher

- strict YAMLの`securitymasker.config` v2 schemaを実装
- 単一`securitymasker.dict` schemaとloaderを実装
- config相対path、隣接config探索、CLI override優先順位を実装
- `securitymasker init`でconfig、辞書、state directory、keyを安全に生成
- root `securitymasker.py`と`./scripts/setup`を実装
- 既存file/keyを上書きしないfailure testを追加

合格条件：

- clean cloneからsetup後に`python3 securitymasker.py`で起動できる
- current working directoryに置かれた別configを誤読しない
- config/辞書の欠落、重複、未知field、危険な権限で起動拒否
- key実値がstdout、stderr、log、例外に出ない

### Phase 2 — `chatgpt` / `claude` modeとroute分離

- runtimeを1 process/1 providerへ変更
- configのmode/host/portをserve既定値へ接続
- modeごとにroute tableとupstreamを一つだけ構築
- wrong-protocol route、未知route、`both`相当をlocalで拒否

合格条件：

- 異なるconfigから2 processを4000/4001で同時起動できる
- ChatGPT portにAnthropic credential/body、Claude portにOpenAI credential/bodyが到達しない
- CLI `--mode`/`--port` overrideとconfigだけの起動が同じruntime契約になる

### Phase 3 — client protocol compatibility

- `x-claude-code-session-id`をsession resolverとheader policyへ追加
- `/v1/messages/count_tokens`、Claude `/v1/models`、`HEAD /`を実装
- Anthropicのopen-list headerとfeature pairを透過
- ChatGPT Responsesのauth passthrough、session/thread/response bindingをmode内で再検証

合格条件：

- 実Claude Code CLIと実Codex CLIがmock upstream相手にrequest/streamを完走
- client session間でaliasを共有しない
- `count_tokens`がmask後に実際に送るpayloadを数える
- providerが追加する未知field/headerで既知機能を壊さず、機密値は通さない

### Phase 4 — encrypted SQLite store

- DB/key 1対1のSQLite store、schema、TTL cleanup、writer leaseを実装
- database ID、mode metadata、key検証値、keyed lookup、AES-GCM AADを実装
- response bindingを含むpersisted stateを暗号化またはkeyed identifier化
- memory storeをpreview/unit test専用へ変更

合格条件：

- DB binary scanで合成secret、session key、raw response/session IDが見つからない
- wrong key、tamper、DB lock、disk full相当、schema mismatchで外部送信0
- process再起動後もaliasが安定しresponseを復元できる
- DB作成modeと異なるmode、同じDBを使う二重起動を拒否
- ChatGPT/Claudeの別DB/keyは同時利用でき、同じ辞書だけを共有できる

### Phase 5 — standard日本語NER

- Presidio code、extra、test、文書を削除
- ADR-0009の日本語NER、inference runtime、model artifactを標準setup/buildへ接続
- model/revision/artifact digest/license manifestをsetupとbuildで共有
- model欠落・破損・load/inference失敗のsilent downgradeを削除
- standard NERを既定ONにしたsource/gateway/evaluation testを追加

合格条件：

- source標準setupでNERが実際にload・推論され、未登録の合成人名・法人名・地名をmask
- modelを削除・改竄すると送信せず明示失敗
- deterministic detectorとユーザー辞書がNER有無にかかわらず全contextで動く
- model、base model、学習datasetの再配布条件を公開前に記録

### Phase 6 — preview/setup/doctorと通常運用

- previewをGatewayと同じmasking pipelineへ接続
- modeごとのclient snippetをconfigから生成
- read-only doctorでconfig、辞書、state/key、port、NER、client設定を確認
- client設定を自動変更しないことをtestで固定

合格条件：

- network access無しでstandard NERを含むpreviewを実行できる
- setup snippetとE2E fixtureが同じ生成元を使う
- error、log、doctor JSONに合成secretが出ない

### Phase 7 — 通常運用とtest setupの分離

- 実CLI E2Eをpersistent client config型へ書き換え
- isolated HOME/CODEX_HOME/CLAUDE_CONFIG_DIR/config/dict/state/key/portを使う
- mock upstreamとtest-only knobを`devtools`/`tests`へ隔離
- local release scriptへruff、mypy、unit/evaluation、mock gateway、real CLI E2Eを集約
- providerへのoutbound routeが存在しないことをE2E前に構造検査

合格条件：

- testは利用者の実HOME/config/stateを変更しない
- test collectionだけではnetwork接触しない
- 必須gateのskipを成功扱いしない
- mock/test assetが通常運用文書と配布binaryへ混入しない

### Phase 8 — one-file packaging spike

- PyInstallerを固定build dependencyとして追加
- clean build venvとspec/build scriptを作成
- Python runtime、runtime dependency、NER runtime、model artifactを収集
- Redis、Presidio、dev/test/mock assetを除外
- sourceとbinaryへ同じleakage/mock E2Eを実行

合格条件：

- Python/venv未導入のclean machineで起動
- config init、validate、preview、両modeのmock E2Eがbinaryで成功
- binary size、起動時間、一時展開、signal終了、cleanup、`noexec`制約を記録
- source実行とbinary実行のmask/fail-closed結果が一致

このPhaseは旧infra撤去より前に行う。one-fileが成立しない場合は、先に原因と代替
（onedir、別packager、対応OS縮小）を判断する。

#### Phase 8 実測結果（2026-07-26、macOS arm64）

PyInstaller 6.21.0を固定し、Python 3.12.13のclean venvから、標準NER runtimeと検証済み
model artifactを含むMach-O one-fileを生成できた。`scripts/build-binary`はPython 3.12未満を
成果物作成前に拒否し、runtime / NER / buildの各lockだけを導入する。

| 項目 | 結果 |
|---|---|
| artifact | arm64 thin Mach-O、961,152,432 bytes（約917 MiB） |
| clean build | 約244秒、収集対象331件 |
| cold `--help` | 約25.5秒 |
| warm `config-check` | 約11.5秒 |
| 標準NER `preview` | 約46.8秒（初期化・推論を含む） |
| 外部runtime | `otool -L`ではmacOS標準`libSystem` / `libz`のみ。外部Pythonへのlinkなし |
| 署名 | ad-hoc。Developer ID署名・notarizationは未実施 |
| 一時展開 | `TMPDIR`配下の`_MEI*`。通常終了とSIGTERM後に残存なし |
| binary E2E | init / validate / NER preview、ChatGPT / Claude mock、SQLite作成、mask・復元・漏えいゼロが成功 |

標準modelではTransformersの`AutoTokenizer`が全model registryを動的探索し、凍結環境で
無関係なmodule欠落を起こした。採用model/revisionは固定済みなので、XLM-RoBERTaの
tokenizer / token-classification classを直接loadする経路にした。sourceの任意model互換用
Auto経路は残すが、v2標準設定では採用model以外を許可しない。

one-fileは展開した子実行ファイルを起動するため、`noexec` filesystemを一時directoryに
指定すると動作しない。実行許可のあるlocal `TMPDIR`を使用するかsource版を使う必要がある
（[PyInstaller operation mode](https://pyinstaller.org/en/stable/operating-mode.html)、
[usage / `--runtime-tmpdir`](https://pyinstaller.org/en/stable/usage.html)）。macOS上で
`noexec` mountそのものは再現していない。

技術的なone-file成立は確認できた。一方、次は未完了であり、現時点のbinaryを公開artifactとは
扱わない。

- Linux / Windows native buildと同じbinary gate
- macOS Developer ID署名・notarization
- model weight、base model、学習datasetを一体配布する場合の最終的な再配布判断
- Python自体が存在しない別machineでの実行（本検証は空環境変数＋外部Python linkなしの代替確認）

再現コマンド：

```bash
PYTHON_COMMAND=python3.12 ./scripts/build-binary
./scripts/test-binary ./dist/securitymasker
```

### Phase 9 — 旧infra撤去と文書再編

- Redis、Dockerfile、Compose、GitHub Actions、multi-tenant/public-bind codeを削除
- `securitymasker run`を通常運用から削除
- root READMEを5分の利用者quickstartへ置換
- `docs/user`、`docs/design`、`docs/development`へ現行情報を整理
- `doc/00..07`は必要な現在情報を移した後、working treeから削除

合格条件：

- 利用者導線にRedis、Docker、GitHub Actions、PyPI publishが残らない
- local release scriptが旧CIと同等以上の必須gateを実行
- READMEのsource/binary commandをclean環境で機械実行できる
- 通常運用とtest setup、localとremote、Desktop実証とCLI代替を混同しない

#### Phase 9 実施結果（2026-07-26）

Redis backend、Docker/Compose、GitHub Actions、multi-tenant/public-bind、`securitymasker run`、
旧v1 config生成commandを製品とtestから撤去した。通常運用は暗号化SQLiteだけとなり、
`docs/user`、`docs/design`、`docs/development`へ現行情報を再編した。旧 `doc/00..07` は
必要な不変条件・status・運用情報を移したうえで削除した。

### Phase 10 — release candidate

- clean checkout/clean machineでsource gateとbinary gateを再実行
- mode別TTL/restart、DB/key backup・欠落・不一致scenarioを検証
- ownerが可能ならsynthetic promptだけでDesktopの手動smoke testを実施
- checksum、version、release note、既知の制限を生成

合格条件：

- 全release gate成功、必須skip 0、worktree clean
- binaryとsource tagの対応をchecksumで追跡可能
- user-only operationを除いて公開可能なartifactと文書が揃う
- Desktopを未検証なら「CLIと共有設定で検証済み」とだけ表現する

## 事前検証

2026-07-26の現行`main`で次を確認した。

| 検証 | 結果 | 本ADRへの反映 |
|---|---|---|
| 品質基準 | ruff成功、mypy strict 67 files成功、unit/evaluation 707 tests成功 | masking coreの移行基準線 |
| 実client CLI | `codex-cli 0.145.0`、Claude Code `2.1.212` | Desktop共有設定のprotocol surrogate |
| Codex設定層 | 公式manualでCodex app、CLI、IDE extensionが同じconfiguration layersを共有 | user modeは`chatgpt`、自動gateはCodex CLI |
| 現行route | 両provider routeを常時公開し、`/v1/models`はOpenAI固定 | mode別route tableが必須 |
| Claude session | 現resolverは`x-claude-code-session-id`を未使用 | Phase 3 blocker |
| Claude endpoint | `/v1/messages/count_tokens`が未実装 | Phase 3 gap |
| SQLite | runtimeのSQLite 3.53.4、threadsafety 3 | 新runtime dependency無しで実装可能 |
| session暗号 | Redis storeにsession全体のAES-GCM serialize/deserializeが存在 | codecを抽出してSQLiteで再利用 |
| PyInstaller | 現venvには未導入 | 独立packaging spikeが必要 |
| NER規模 | venv 1.7GB、torch 529MB、transformers 112MB | clean build環境とsize実測が必要 |
| NER model license | model cardはMIT表示 | base model・学習datasetを含む再配布条件はPhase 5で確定 |
| one-file制約 | 公式文書でOSごとのnative buildと一時展開を確認 | OS別artifactとtemp/noexec/cleanup test |

設計は実現可能と判断するが、release可能との最終判断はまだしない。最大の未検証項目は、
標準NER込みone-fileの実生成、SQLite障害時のfail-closed、persistent client configを使う
実CLI E2Eであり、Phase 3、4、5、8で先送りせず検証する。

参照した現行仕様：

- [OpenAI Codex configuration](https://learn.chatgpt.com/docs/config-file/config-basic)
- [Claude Code Desktop](https://code.claude.com/docs/en/desktop)
- [Claude Code gateway protocol](https://code.claude.com/docs/en/llm-gateway-protocol)
- [Connect Claude Code to a gateway](https://code.claude.com/docs/en/llm-gateway-connect)
- [PyInstaller manual](https://pyinstaller.org/en/stable/)
- [日本語NER model card](https://huggingface.co/tsmatz/xlm-roberta-ner-japanese)

## ownerの判断・操作

実装中にownerの判断が必要な残項目：

1. 最初のbinary対象をmacOS arm64だけにするか、macOS x86_64 / Windows / Linuxも同時に
   release gateへ含めるか
2. model、base model、学習datasetの再配布条件の確認結果を受けて、そのmodelをbinaryへ
   同梱して公開するか

公開時にownerだけが行う操作：

1. GitHub repositoryの公開設定、tag、GitHub Release、binary upload
2. 必要なApple Developer ID/notarization、Windows code signing
3. 実アカウントを使うCodex app / Claude Code Desktopの手動smoke test

## 影響

- cloneするrepositoryはmodel weightを含まず軽量に保てるが、source setupとbinary buildには
  model downloadと検証時間が必要になる。
- standard binaryはtorch/transformers等を含む可能性があり大きくなる。size、起動時間、
  one-file一時展開はrelease gateで実測する。
- Redisの分散lockとmulti-tenant identityは標準製品から消え、local single-writer leaseと
  mode別DB分離へ置き換わる。
- config、辞書、state/keyの4要素が利用者の明示的なlocal artifactになる。keyを失うと既存DBは
  復号できないため、backup/recovery手順が必要になる。
- client設定の自動書換えは行わず、snippet生成とread-only doctorだけを提供する。
- sourceとbinaryの挙動差を許さず、同じleakage/E2E testを両方へ適用する。

## 検討した代替案

- **一つのprocess/DBで両providerを扱う**：route/credential/session分離と障害範囲が複雑になるため
  却下。
- **Redisを標準storeとして残す**：単一利用者・単一workerのlocal用途に不要なservice運用を課すため
  却下。
- **master keyをSQLite内へ保存する**：暗号文と復号鍵が同時に漏れ、at-rest暗号化の意味がないため
  却下。
- **OS keychainだけを使う**：DB単体漏えいには強いが、sourceのcross-platform実行とportableな
  folder運用を複雑にする。初版は明示sidecar keyとし、将来のkey provider候補に残す。
- **NERをsource-only/既定OFFにする**：標準利用者が期待する一般的固有表現の検出能力を落とすため
  却下。
- **Presidioと日本語NERを両方残す**：重複するfuzzy detection、巨大依存、供給網とtestの二重保守を
  招くため却下。
- **複数辞書、include、globを許可する**：現時点の利用者価値より設定merge規則と誤読込リスクが
  大きいため却下。
- **source実行を開発者専用にする**：cloneしてbuildせず使いたい利用者要件を満たさないため却下。
