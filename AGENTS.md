# AGENTS.md — SecurityMasker 開発ルール

このrepositoryで作業するagentは、歴史文書ではなくこのファイル、最新ADR、現行statusに従います。
手段が矛盾する場合は、不変条件、最新ADR、その他の文書の順で優先します。

## 製品

SecurityMaskerは、ローカルのCodexまたはClaude Codeと外部LLMの間に立つ
可逆マスキングproxyです。

- package/CLI: `securitymasker`
- architecture: Starlette + httpxの薄い専用proxy
- user mode: `chatgpt` / `claude`
- 1 process: 1 mode、1 loopback port、1 worker
- normal store: mode別の暗号化SQLite + sidecar master key
- preview/unit test store: memory
- client設定: snippetを生成するだけ。利用者の設定を自動変更しない
- source: `python3 securitymasker.py`
- binary: PyInstaller one-fileをOS別にbuild
- Redis、Docker、Compose、GitHub Actions、PyPI、public bind、multi-tenant、
  `securitymasker run` は標準製品範囲外

現行設計は [ADR-0012](docs/adr/0012-renew-package-design.md)、状態は
[development status](docs/development/status.md) を正とします。

## 不変条件

1. 元の機密情報を外部へ送らない。
2. sessionをまたいで秘密やaliasを混ぜない。
3. JSON、code、tool call、patch、shell commandの構造を壊さない。
4. 不明・障害時はfail-closed。重大secretは常にblock。
5. upstream client/SDKをforkせず、protocol差分をadapterへ隔離する。
6. protocol adapterとmasking coreを分離する。
7. 未知field/event/headerは、leak guard後だけ可能な限り透過する。
8. log、audit、error、trace、telemetryへ原文、鍵、平文対応表を残さない。
9. Presidio/NERだけに正しさを依存せず、ユーザー辞書を最優先する。
10. credentialは平文復元より `env_reference` を優先する。
11. 機能追加よりtest可能性と保守性を優先する。

上限に達したとき一部だけ検査して成功を返してはいけません。

## 現行構成

```text
securitymasker/
├── securitymasker.py
├── pyproject.toml
├── requirements*.lock
├── scripts/
├── devtools/
├── docs/
│   ├── user/
│   ├── design/
│   ├── development/
│   └── adr/
├── src/securitymasker/
│   ├── gateway/
│   ├── protocols/
│   ├── context/
│   ├── detectors/
│   ├── aliases/
│   ├── sessions/
│   └── streaming/
└── tests/
```

## 技術

- Python 3.12+
- pip + venv。`uv`は採用しない
- pydantic、cryptography、httpx、starlette、transformers、torch
- pytest、hypothesis、ruff、mypy strict
- 日本語NERは標準・既定ON。model revisionと全artifact digestを固定し、
  local-only、safetensors、`trust_remote_code`不使用

## test

- 実在人物や実secretを使わず、合成dataだけを使う。
- 実providerへtest bodyを送らない。
- 最重要testは、上流の最終payloadに元の合成機密値が無いこと。
- streamingはalias全分割位置、tool argument delta、特殊文字を検査する。
- session並行性とresponse bindingで混在が無いことを検査する。
- rejectだけでなくclean inputのacceptも検査する。
- 通常setupとtest setupを混同しない。

手順は [testing](docs/development/testing.md) に従います。

## 作業

- 着手前に [development status](docs/development/status.md) を読む。
- `done` は実装、製品配線、回帰test、利用/運用手順が揃った場合だけ。
- 各作業単位の終わりに実行可能な状態とtest結果を残す。
- 破壊的操作、外部送信、新規依存、大きな設計転換は事前にownerへ確認する。
- repo外の `~/.codex/config.toml` 等を変更しない。
- 秘密値をcommit、log、scratch fileへ書かない。
- コミット/pushはownerが依頼した場合だけ。push、PR、公開は明示依頼なしに行わない。
- 技術文書とcode comment/docstringは日本語を基本とし、識別子とprotocol用語は英語を維持する。
- commit件名と本文は通常の日本語で書き、Conventional Commits prefixとAI trailerを付けない。
