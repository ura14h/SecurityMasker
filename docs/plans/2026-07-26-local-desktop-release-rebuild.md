# ローカルデスクトップ向け製品再構築計画

- 作成日: 2026-07-26
- 状態: 実装前レビュー版
- 対象: SecurityMasker の製品範囲、実行方式、配布方式、文書構成の再構築
- 実装開始条件: 本計画の合意後、ADR と `AGENTS.md` を最初の変更単位で更新する

## 1. 結論

SecurityMasker を、個人のローカル PC で動かす **ChatGPT デスクトップアプリのローカル
coding/Codex 機能**と **Claude Code Desktop のローカルセッション**向け可逆マスキング
プロキシへ絞り込む。

製品の標準運用は、同じ実行ファイルをモードとポートを変えて起動する形とする。

```console
securitymasker --mode chatgpt --port 4000 --config securitymasker.yaml
securitymasker --mode claude  --port 4001 --config securitymasker.yaml
```

1 process は 1 client protocol だけを受け付ける。`chatgpt` mode は OpenAI Responses、
`claude` mode は Anthropic Messages だけを公開し、別 protocol や未知 route はローカルで
拒否する。両方を使う利用者は同じ binary を 2 process 起動する。

標準構成から Redis、Docker、GitHub Actions、PyPI 公開、multi-worker、multi-tenant を外す。
session mapping は mode ごとに分離した SQLite へ暗号化して保存する。配布は PyInstaller の
one-file binary を第一候補とし、source からの実行は開発者向け手順へ移す。

## 2. 製品として保証する範囲

### 2.1 対象

- ChatGPT デスクトップアプリでローカル実行される coding/Codex 機能
- Claude Code Desktop の Local session
- 上記と設定層を共有する Codex CLI / Claude Code CLI
- OpenAI Responses request/response の mask/restore
- Anthropic Messages request/response の mask/restore
- 同一 client session 内の安定 alias と、SecurityMasker 再起動後の session 再開
- 利用者が入力例の mask 結果をローカルで確認する preview
- client が提示した認証情報の透過転送。SecurityMasker は資格情報を保存、復号、記録しない

### 2.2 対象外

- 通常の ChatGPT 会話、音声、connector、cloud task など、ローカル coding/Codex の
  Responses 経路を通らない通信
- Claude Code の Cloud、SSH、Remote Control、web、Slack 等の remote session
- provider API 以外へ client や tool が行う通信の包括的な DLP
- 1 process で ChatGPT と Claude の両 protocol を同時提供する構成
- 複数 PC、複数 worker、複数利用者で session state を共有する構成
- GitHub Actions による CI/CD
- Docker image / Docker Compose による利用者向け配布
- PyPI への package 公開
- optional NER / Presidio を含む巨大な標準 binary

`localhost` の proxy は cloud/remote 実行環境から到達できない。したがって「ChatGPT または
Claude の全通信を保護する」とは表現せず、上記の **local inference 経路だけ**を保証する。

## 3. 利用者の標準フロー

1. GitHub Releases から自分の OS/architecture 用 binary を取得する。
2. `securitymasker config init --output securitymasker.yaml` で starter config を作る。
3. 辞書、regex、policy を編集する。
4. 必要な mode の SecurityMasker を起動する。
5. `securitymasker setup --mode <mode> --port <port>` が表示する設定断片を、利用者が
   client の設定へ一度だけ反映する。
6. ChatGPT Desktop または Claude Code Desktop で **Local** session を開始する。
7. prompt は mask されて上流へ送られ、response は同じ local session mapping で復元される。
8. 必要に応じて次で mask 結果だけを確認する。

```console
securitymasker preview --config securitymasker.yaml "担当は山田太郎です"
```

client 設定の自動書換えは行わない。SecurityMasker は設定断片の生成と read-only の診断だけを
提供し、利用者の `~/.codex/config.toml` や `~/.claude/settings.json` を変更しない。

## 4. client ごとの接続契約

### 4.1 `chatgpt` mode

利用者向け名称を `chatgpt`、内部 protocol 名を `openai_responses` とする。`codex` は mode 名
ではなく、互換性試験に使う CLI executable 名としてだけ残す。

ChatGPT デスクトップアプリ、Codex CLI、IDE extension は同じ Codex configuration layers を
共有する。利用者は `~/.codex/config.toml` に、概ね次の custom provider を設定する。
実装時には現行 client で正確な snippet を生成し、固定文字列を複数文書へ重複させない。

```toml
model_provider = "securitymasker"

[model_providers.securitymasker]
name = "SecurityMasker"
base_url = "http://127.0.0.1:4000"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = false
```

公開 route は次だけとする。

- `POST /responses`
- `POST /v1/responses`（client compatibility のため必要な場合）
- `GET /models`
- `GET /v1/models`
- `GET /health`
- `GET /ready`

会話識別は、client が送る安定した `session-id` / `thread-id` を優先し、
`previous_response_id` binding を fallback とする。専用 wrapper が注入していた
`X-SecurityMasker-Session-ID` はテスト・後方互換の扱いを別途決め、通常運用の必須条件には
しない。

### 4.2 `claude` mode

利用者向け名称を `claude`、内部 protocol 名を `anthropic_messages` とする。

Claude Code は `ANTHROPIC_BASE_URL` で Anthropic Messages gateway を選択できる。
`~/.claude/settings.json` の `env` は CLI と Desktop の local session から利用できるため、
利用者は概ね次を設定する。

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:4001"
  }
}
```

credential 用環境変数を SecurityMasker のためだけに追加しない。保存済みの claude.ai login を
使う場合も base URL だけで gateway 経由にできるため、受信した OAuth capability を含む
`anthropic-beta`、`anthropic-version`、認証 header を上流へ透過する。

公開 route は次だけとする。

- `POST /v1/messages`
- `POST /v1/messages/count_tokens`
- `GET /v1/models`（model discovery を有効にした client 用）
- `HEAD /`（client の best-effort connectivity probe 用）
- `GET /health`
- `GET /ready`

`x-claude-code-session-id` を通常運用の安定 session ID として使う。これは Claude Code が
session ごとに生成するため、現行 wrapper の custom header 注入は不要になる。
`x-claude-code-*` と `anthropic-*` は将来追加を前提とした open list として扱う。ただし
認証以外の自由入力 header value は漏えい検査を通し、元の機密値を転送しない不変条件を維持する。

`count_tokens` は body を mask してから上流へ送り、返却値は「mask 後に実際に送る token 数」
として扱う。単なる raw passthrough にはしない。

## 5. runtime と永続化

### 5.1 process model

- 1 process / 1 mode / 1 port / 1 worker
- bind の既定は `127.0.0.1`
- public bind は製品版から削除するか、少なくとも非表示の開発専用機能へ降格する
- `--mode` は必須とし、`both` や暗黙選択を設けない
- mode と異なる provider route は 404 または明示的な local error で拒否する
- upstream は mode ごとに一つだけ構築し、誤 provider へ credential を転送できない型・構造にする

### 5.2 SQLite session store

標準 store を SQLite とし、memory は preview と unit test だけで使う。Redis は削除する。

SQLite file 自体の暗号化 extension は追加しない。既存 Redis store と同じ考え方で、
`MaskingSession` 全体を AES-256-GCM で封緘した blob として保存する。DB から分かる情報は
必要最小限の version、期限、keyed identifier に限定する。

- mode ごとに `chatgpt.sqlite3` / `claude.sqlite3` を分離
- 1 installation key を初回起動時に CSPRNG で生成
- key file と DB は user だけが読める権限で作成
- session lookup key と response binding key は master key で HMAC し、raw ID を DB key にしない
- encrypted blob の AAD に schema version、mode、lookup key を含める
- 改竄、wrong key、schema 不一致、DB error は fail-closed
- idle / absolute TTL を読み出し時と定期 cleanup の両方で適用
- mode ごとに active writer lease を取り、同じ DB を使う同一 mode の二重起動を拒否
- async handler から直接長い同期 I/O を行わず、専用 worker thread へ閉じ込める
- migration は transaction 内で実行し、失敗時に旧 DB を破壊しない

state directory の既定は OS の user data/state directory とし、`--state-dir` で明示変更できる
ようにする。config と state は binary 外部に残る。「one-file」は配布物が一つという意味であり、
利用者の設定・暗号鍵・SQLite state まで executable へ埋め込む意味ではない。

## 6. config と CLI

### 6.1 config

既存 YAML の辞書・policy を活かし、runtime の必須選択を増やしすぎない。

- `--mode`: client protocol。config と二重定義しない
- `--port`: listen port。CLI が config より優先
- `--config`: masking dictionary / policy
- `--state-dir`: advanced option。未指定なら OS 既定
- upstream override: 通常運用の文書からは隠し、開発・互換性試験用の明示 option とする

実 secret は従来どおり `value_from_env` で読み、config へ平文保存しない。

### 6.2 command grammar

subcommand 無しを serve とする。

```console
securitymasker --mode chatgpt --port 4000 --config securitymasker.yaml
securitymasker --mode claude  --port 4001 --config securitymasker.yaml
```

補助 command は次へ整理する。

```console
securitymasker config init
securitymasker config validate --config securitymasker.yaml
securitymasker preview --config securitymasker.yaml "入力例"
securitymasker setup --mode chatgpt --port 4000
securitymasker setup --mode claude --port 4001
securitymasker doctor --mode chatgpt --port 4000 --config securitymasker.yaml
```

`securitymasker run codex/claude` は利用者の通常運用から外す。persistent desktop config を
正とし、wrapper は削除するか、開発用 compatibility command として明示的に降格する。
未実装の `sessions` placeholder は削除し、必要なら SQLite 対応後に実際に機能する command
として設計し直す。

## 7. 通常運用とテストの分離

### 7.1 利用者向け通常運用

- real ChatGPT / claude.ai login または利用者自身の provider credential
- persistent client config
- fixed loopback port
- user data directory の SQLite
- user が作成した masking config
- mock upstream、dummy credential、test-only header を一切登場させない

### 7.2 自動テスト

- synthetic data のみ
- isolated temporary HOME / CODEX_HOME / CLAUDE_CONFIG_DIR / state directory
- temporary port
- mock upstream と dummy auth
- provider への outbound network が構造的に存在しない環境
- 実 Codex CLI / 実 Claude Code CLI を、Desktop と共有する設定経路の protocol surrogate として使用
- test config は `tests/` または `devtools/` に閉じ込め、製品 binary へ同梱しない

CLI E2E は wrapper 経由ではなく、利用者と同じ persistent config を隔離 HOME に作り、
次を検証する。

1. mode 指定で Gateway を起動できる。
2. client が期待 route、header、streaming format を使う。
3. mock upstream の最終 payload に元の機密値が 0 件で、alias が存在する。
4. client の表示に alias が残らず、元の表示へ復元される。
5. process 再起動後も同じ client session を復元できる。
6. mode が異なる route と未知 route は外へ送られない。
7. mock/test 設定が通常運用文書や配布 binary に混入していない。

### 7.3 Desktop の扱い

毎 release の自動 gate に Desktop UI 操作を含めない。Codex CLI と Claude Code CLI を
主要な protocol / shared-config gate とする。

ただし最初に README で「Desktop 検証済み」と主張する前、および client の設定方式が変わった
ときは、ChatGPT Desktop と Claude Code Desktop の Local session で各 1 回の手動 smoke test
を行う。実施できない場合は「CLI と共有設定で検証済み」とだけ記載し、Desktop 実証済みとは
書かない。

## 8. PyInstaller 配布

標準 binary は deterministic detector、Gateway、SQLite、CLI、starter config だけを含める。
Presidio、torch、transformers、model artifact、pytest、mypy、ruff、mock upstream を含めない。

PyInstaller は cross-compiler ではないため、OS ごとに native build が必要である。最初に現在の
macOS 環境で one-file spike を行い、対応 OS を合意した後に OS ごとの build 手順を用意する。
GitHub Actions では build しない。

one-file は起動時に内部ファイルを一時 directory へ展開する。長時間動く proxy であることを
踏まえ、通常終了、signal 終了、一時領域 cleanup、`noexec` filesystem、resource path を
受け入れ試験へ含める。

Git repository へ生成 binary は commit しない。公開時は、owner が必要なら手動で GitHub Release
へ checksum と一緒に添付する。PyPI は使わない。署名・notarization を行う場合の credential は
repository や build script に保存しない。

## 9. 文書・repository 構成

利用者の入口を root `README.md` 一つに絞り、5 分以内に「取得、config 作成、起動、client 設定、
preview」まで到達できる構成にする。

```text
README.md
LICENSE
SECURITY.md
config/
  securitymasker.example.yaml
docs/
  user/
    quickstart.md
    chatgpt-desktop.md
    claude-code-desktop.md
    configuration.md
    troubleshooting.md
  design/
    architecture.md
    threat-model.md
    compatibility.md
    adr/
  development/
    setup.md
    testing.md
    packaging.md
src/
tests/
devtools/
```

- `doc/00..07` は現行利用者文書から外す。必要な現在情報を ADR / design / development へ移した
  後に working tree から削除する。歴史は Git history に残る。
- Dockerfile、Compose、Redis 専用 code/test/dependency、`.github/workflows/ci.yml` を削除する。
- `pyproject.toml` は source 開発と binary build のために残すが、PyPI install を README の
  通常導線にしない。
- optional NER は標準 binary 外であることを明記し、必要なら source developer 向け advanced
  extension として残す。最初の release を妨げる場合は別 repository / plugin 化を後で検討する。

## 10. 実装順序と commit 境界

各項目を独立して完了、検証、commit する。後続変更を同じ commit へ混ぜない。

### Phase 0 — 決定を現行ルールへ反映

- ADR-0012 を追加し、本計画の製品範囲、mode 名、1 process/1 protocol、SQLite、配布方針を決定
- `AGENTS.md` の現行製品説明と優先文書を更新
- 古い ADR は削除せず、superseded を明記

合格条件:

- `AGENTS.md`、最新 ADR、本計画の間に起動方式・store・配布の矛盾がない
- 不変条件は維持され、変更するのは手段と製品範囲だけである

### Phase 1 — `chatgpt` / `claude` mode と CLI 骨格

- subcommand 無し serve、必須 `--mode`、`--port`、`--config` を実装
- runtime を単一 provider に変更
- mode ごとに route table を生成
- wrong-protocol route、未知 route、両 mode 同時指定を fail-closed で拒否

合格条件:

- 2 process を 4000/4001 で同時起動できる
- ChatGPT port に Anthropic credential/body、Claude port に OpenAI credential/body が到達しない
- 既存 masking core の unit/evaluation test が維持される

### Phase 2 — client protocol compatibility

- `x-claude-code-session-id` を session resolver と header policy へ追加
- `/v1/messages/count_tokens`、Claude `/v1/models`、`HEAD /` を実装
- Anthropic open-list header 方針と feature pair の透過性を回帰 test 化
- ChatGPT Responses の route、auth passthrough、session/thread/response binding を mode 内で再検証

合格条件:

- 実 Claude Code CLI と実 Codex CLI が mock upstream 相手に request/stream を完走
- client 固有 session が別 session と alias を共有しない
- count result が mask 後 payload に対応する

### Phase 3 — SQLite encrypted store

- SQLite store、schema、master key file、migration、TTL cleanup、writer lease を実装
- response binding を含む全 persisted state を暗号化または keyed identifier 化
- memory を preview/test 専用に変更
- Redis store と同じ `SessionStore` 契約 test を SQLite へ移植

合格条件:

- DB binary scan で合成 secret、session key、raw response/session ID が見つからない
- wrong key、tamper、DB lock、disk full 相当、schema mismatch で外部送信 0
- process 再起動後の alias 安定性と response 復元を確認
- 同じ mode/state の二重起動を拒否し、異なる mode は同時起動できる

### Phase 4 — 利用者向け setup / preview / doctor

- preview を本番と同じ masking pipeline へ接続
- mode ごとの client snippet を単一実装から生成
- read-only doctor で port、config、state、client 設定の整合を確認
- client 設定を自動変更しないことを test で固定

合格条件:

- starter config から network access 無しで preview できる
- snippet と E2E fixture が同じ生成元を使い、文書との drift test がある
- error、log、doctor JSON に合成 secret が出ない

### Phase 5 — 利用者運用と test setup の分離

- 実 CLI E2E を persistent-config 型へ書き換え
- mock upstream と test-only knobs を `devtools` / `tests` に隔離
- local release script に ruff、mypy、unit/evaluation、live mock gateway、real CLI E2E を集約
- 外部 provider へ接続できないことを E2E 起動前に構造検査

合格条件:

- test は利用者の実 HOME/config/state を変更しない
- test collection だけでは network 接触しない
- skip された必須 gate を成功扱いしない

### Phase 6 — 最小 one-file packaging spike

- owner 承認後に PyInstaller を build dependency として固定
- runtime lock だけの clean venv で spec/build script を作成
- package resource と lazy import を収集し、optional ML / Redis / devtools を除外
- one-file binary で config init、validate、preview、両 mode の mock E2E を実行

合格条件:

- Python/venv 未導入の clean machine で起動できる
- binary に mock fixture、test package、Redis、torch、transformers、Presidio が含まれない
- binary size、起動時間、終了時 cleanup を記録し、上限を release checklist に固定
- source 実行と binary 実行の leakage test 結果が一致する

この Phase は repository cleanup より前に行う。one-file が成立しない場合、先に原因と代替
（onedir、別 packager、対応 OS 縮小）を判断し、配布前提が崩れたまま文書を完成させない。

### Phase 7 — Redis / Docker / GitHub CI の撤去

- Redis code、test、extra、lock entry、runtime env を削除
- Dockerfile、Compose、container-specific test/docs を削除
- GitHub Actions workflow と CI 前提の文書を削除
- multi-tenant/public-bind の製品 code を削除し local-only surface を縮小

合格条件:

- `rg` で利用者導線に Redis、Docker、GitHub Actions、PyPI publish が残っていない
- optional lazy import の欠落で runtime error が起きない
- local release script が旧 CI と同等以上の必須 gate を実行する

### Phase 8 — 文書再編

- root README を利用者 quickstart に置換
- `docs/user`、`docs/design`、`docs/development` へ現行情報だけを整理
- `doc/00..07` を working tree から撤去
- Desktop/CLI、local/remote、通常運用/test の表現を全件監査

合格条件:

- 新規利用者が root README から 5 分の導線だけを追える
- README の command を clean binary で機械実行できる
- 「Desktop 実証済み」の表現が実施済み試験範囲を超えない

### Phase 9 — release candidate

- clean checkout / clean machine で source gate と binary gate を再実行
- mode 別 24 時間相当の TTL / restart scenario を時間制御 test で確認
- 利用者資格情報を使わない mock acceptance を完了
- owner が可能なら synthetic prompt で Desktop の手動 smoke test を実施
- checksum、version、release note、既知の制限を生成

合格条件:

- 全 release gate 成功、必須 skip 0、worktree clean
- binary と source tag の対応が checksum で追跡可能
- user-only operation を除いて公開可能な artifact と文書が揃う

## 11. 事前検証結果

2026-07-26 に現行 `main` で確認した。

| 検証 | 結果 | 計画への反映 |
|---|---|---|
| 現行品質 gate | ruff 成功、mypy strict 67 files 成功、unit/evaluation 707 tests 成功 | masking core を移行の基準線にする |
| 実 client CLI | `codex-cli 0.145.0`、Claude Code `2.1.212` を確認 | Desktop 共有設定の protocol surrogate に使える |
| ChatGPT 設定層 | 公式 manual で ChatGPT desktop app、Codex CLI、IDE extension が同じ configuration layers を共有すると確認 | user mode は `chatgpt`、自動 gate は Codex CLI |
| 現行 route | 両 provider route を常時公開し、`/v1/models` は OpenAI upstream 固定 | mode 別 route table が必須 |
| Claude session | 現行 resolver は `x-claude-code-session-id` を未使用 | Phase 2 の release blocker |
| Claude endpoints | 現行は `/v1/messages/count_tokens` が無い | Phase 2 の compatibility gap |
| SQLite | Python runtime の SQLite 3.53.4、threadsafety 3 を確認 | 新 runtime dependency 無しで store を実装可能 |
| session encryption | Redis store に session 全体の AES-GCM serialize/deserialize が既にある | crypto format を抽出し SQLite で再利用可能 |
| PyInstaller | 現 venv には未導入 | dependency 承認後の独立 spike を早期 gate にする |
| 開発環境の大きさ | venv 1.7GB、torch 529MB、transformers 112MB 等を確認 | 現 venv から build せず、最小 clean venv を必須化 |
| one-file 制約 | 公式文書で OS ごとの native build と一時展開を確認 | OS 別 artifact、temp/noexec/cleanup test が必要 |

現時点で **設計変更は実現可能**と判断する。ただし release 可能との最終判断はまだしない。
未検証の最大項目は、最小依存 PyInstaller binary の実生成、SQLite store の障害時 fail-closed、
そして persistent client config を使う実 CLI E2E である。これらを Phase 2、3、6 の明示 gate
として先送りせず検証する。

## 12. 実装前または公開前に owner の判断・操作が必要な項目

### 実装前

1. PyInstaller を build dependency として追加してよいか。
2. 最初の binary 対象を macOS arm64 だけにするか、macOS x86_64 / Windows / Linux も同時に
   release gate へ含めるか。PyInstaller は OS ごとの native build が必要である。
3. optional NER / Presidio を source-only advanced feature として残すか、初回 release から
   完全に外すか。

### 公開前

1. GitHub repository の公開設定変更、tag、GitHub Release 作成、binary upload。
2. 必要な場合の Apple Developer ID / notarization、Windows code signing。秘密鍵操作は owner
   だけが行い、こちらは secret を受け取らない。
3. 実アカウントが必要な ChatGPT Desktop / Claude Code Desktop の手動 smoke test。
   synthetic prompt だけを使い、実 secret は試験しない。

これら以外の code、test、文書、local artifact 作成は、各 Phase の合格条件を満たしながら
1 項目ずつ commit できる。

## 13. 参照した現行仕様

- OpenAI Codex manual:
  [ChatGPT desktop app / Codex CLI の共通 configuration](https://learn.chatgpt.com/docs/config-file/config-basic)
- Anthropic:
  [Claude Code Desktop](https://code.claude.com/docs/en/desktop)
- Anthropic:
  [LLM gateway protocol reference](https://code.claude.com/docs/en/llm-gateway-protocol)
- Anthropic:
  [Connect Claude Code to an LLM gateway](https://code.claude.com/docs/en/llm-gateway-connect)
- PyInstaller:
  [Manual](https://pyinstaller.org/en/stable/)
- PyInstaller:
  [Using PyInstaller](https://pyinstaller.org/en/stable/usage.html)

外部 client の仕様は変化する。固定した既知 header 値だけを正とせず、release ごとに実 CLI E2E
と公式 protocol reference を照合する。
