# 運用

proxy は Starlette + httpx で実装した専用の透過マスキング Gateway です。LiteLLM は
使用しません（[ADR-0006](adr/0006-drop-litellm-purpose-built-proxy.md)）。

## ローカル実行（pip + venv）

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export SECURITYMASKER_CONFIG=config/securitymasker.example.yaml
securitymasker gateway --port 4000
```

`SECURITYMASKER_CONFIG` は dictionary YAML を指す**必須**設定です。未設定なら Gateway
は fail-closed で起動に失敗します（doc/06 P0-1）。マスクなしの開発 mode は
`SECURITYMASKER_DEV_TRANSPARENT=1` を明示した場合だけ有効で、実 provider の前段では
使用禁止です。upstream は `SECURITYMASKER_OPENAI_UPSTREAM`（既定値
`https://chatgpt.com/backend-api/codex`）と `SECURITYMASKER_ANTHROPIC_UPSTREAM`
（既定値 `https://api.anthropic.com`）で設定します。

## Docker実行（自己完結したdemo）

```bash
docker compose up --build
# 別のshellから実行（Responses API）：
curl http://127.0.0.1:4000/responses \
  -H 'Content-Type: application/json' -H 'X-SecurityMasker-Session-ID: demo' \
  -d '{"model":"m","input":"担当は山田太郎、株式会社極秘技研の件です"}'
```

Compose stack は Gateway を Compose 内の mock upstream へ接続するため、実 key は
不要です。

共有 Redis session store を使うには Redis を起動するだけでなく、Gateway の store
も切り替える必要があります。

```bash
docker compose -f docker-compose.yml -f docker-compose.redis.yml --profile redis up
```

shell prefix の `SECURITYMASKER_STORE=redis docker compose ...` では切り替わりません。
Compose は YAML の変数展開には利用しますが、service 定義にない変数を container へ
渡さないためです。その方法では Redis が起動しても Gateway は黙って in-process
store のままです。overlay が `SECURITYMASKER_STORE` と
`SECURITYMASKER_REDIS_URL` の両方を service に設定します。

Compose には起動確認用の `SECURITYMASKER_MASTER_KEY`（base64 32 bytes）が含まれます。
実運用では独自に生成して secret store から注入してください。欠落または32 bytes
でない場合、Gateway は fail-closed で起動しません。

```bash
openssl rand -base64 32
```

## Codex／Claude Code

- **Codex**：`~/.codex/config.toml` に SecurityMasker provider を追加します。
  `base_url = "http://127.0.0.1:4000"`（`/v1` なし）、
  `wire_api = "responses"`、`requires_openai_auth = true` とし、
  `X-SecurityMasker-Session-ID` を `SECURITYMASKER_SESSION_ID` から設定します。
  ChatGPT OAuth token は透過し、API key は保存しません。設定 block は次で生成できます。
  `python -c "from securitymasker.integrations.codex import codex_config_toml; print(codex_config_toml())"`
- **Claude Code**：`ANTHROPIC_BASE_URL=http://127.0.0.1:4000` と session header を
  設定します。
- **wrapper**：`securitymasker run codex`／`securitymasker run claude` は proxy
  route を設定できなければ tool を起動しません。`/ready` の `ready: true` を要求し、
  Claude Code には `ANTHROPIC_BASE_URL` と session header、Codex には process 単位の
  `-c` override を設定します。`~/.codex/config.toml` は変更しません。
  `ANTHROPIC_API_URL`／`OPENAI_BASE_URL`／`OPENAI_API_BASE` によって迂回される場合や、
  routing 不能な tool は拒否します。session UUID は wrapper が生成します。

  **保証範囲**：unit test は生成設定、override の TOML 妥当性、利用者の実設定を
  変更しないこと、拒否経路を検証します。さらに
  `tests/integration/test_real_cli_e2e.py` は、**実際の** `codex`／`claude` binary を
  `run` 経由で local mock upstream に接続し、process 外へ alias だけが出ること、
  session header が到着すること、tool の home に `config.toml` を書かないことを
  検証します。実 process を起動するため opt-in です。

```bash
SM_RUN_CLI_E2E=1 .venv/bin/python -m pytest tests/integration/test_real_cli_e2e.py -v
```

  この E2E は egress boundary を要求し、自己検査します。local URL を指定するだけでは
  routing は決まっても隔離されません。両 CLI は provider 設定を通らない update、
  analytics、crash reporting 通信を行うため、routing mistake が fail-closed にならず
  internet へ到達し得ます。suite は開始前に routable address への TCP 接続を試し、
  成功した場合は process と CLI が internet に到達可能として skip します。

  実行時は stack 全体を同じ Linux network namespace に入れます。

```bash
./devtools/run_cli_e2e.sh
```

  または CLI を導入した image を network なしで実行します。

```bash
docker run --rm --network none -v "$PWD:/w" -w /w <image> devtools/run_cli_e2e.sh
```

  CLI だけを隔離すると namespace 固有の loopback により Gateway へ到達できません。
  CI はこの E2E を実行し、skip を失敗として扱います。

  実 provider の挙動は保証範囲外です。E2E upstream は意図的に local mock とし、
  実 provider へは送信しません。
- proxy は client 自身の資格情報を透過し、保存もログ記録もしません（§25）。

## CLI

```bash
securitymasker gateway --port 4000 --config <dict.yaml>   # proxyを起動
securitymasker config validate --config <dict.yaml>
securitymasker entities list    --config <dict.yaml>      # 件数だけを表示し、値は表示しない
securitymasker entities test "<text>" --config <dict.yaml>
securitymasker doctor --config <dict.yaml>
securitymasker run codex                                    # session内で起動
```

CLI は元の機密値を表示しません（§12）。

## session

idle TTL（既定4時間）と absolute TTL（既定24時間）は dictionary の `defaults` で
設定します。in-memory session は Gateway process 内に存在します。multi-worker
構成では `SECURITYMASKER_MASTER_KEY`（base64 32 bytes）を設定した Redis store を
使用してください。

## 可観測性

Gateway は全 HTTP request を最終 response body（SSE の最終 chunk を含む）まで観測し、
次の process 内 counter（`securitymasker.metrics.METRICS`）を更新します。

- `gateway_requests_total{provider}`
- `gateway_responses_total{provider,outcome}`
- `gateway_request_duration_{ms_sum,count}{provider,outcome}`
- `gateway_masked_entities_total{provider,entity}`
- `gateway_blocks_total{provider,reason}`
- `gateway_detector_timeouts_total{provider}`
- `gateway_store_errors_total{provider,operation}`
- `gateway_stream_errors_total{provider,reason}`

label は enum の固定集合です。未知の entity 名は `CUSTOM` へ畳み込み、path、session ID、
tenant／user ID、例外文、入力値は label にしません。audit も任意 field の辞書ではなく
`AuditRecord` の固定 schema だけを受け取り、session は一方向 fingerprint のみを記録します。
元の値、alias 対応表、資格情報、full prompt は metrics／audit のどちらにも渡しません（§25）。

既定では exporter／外部 sink を公開しません。`Metrics.snapshot()` を収集基盤へ接続する場合は
Gateway と同じ信頼領域内に置き、公開範囲と保持期間を別途レビューしてください。

## test／CI

```bash
pytest tests/unit tests/evaluation -q                            # 高速test
SM_RUN_LIVE=1 pytest tests/integration/test_live_gateway.py -q   # proxy + mock
SM_RUN_REDIS=1 SECURITYMASKER_REDIS_TEST_URL=redis://127.0.0.1:6379/15 \
  pytest tests/integration/test_real_redis_fencing.py -q         # 実Redisのfencing
python -m tests.evaluation.benchmark                             # latency benchmark
```

CI（`.github/workflows/ci.yml`）は ruff、mypy `--strict`、test suite、live Gateway
integration test に加え、実Redis上でlock owner確認とsession書込みがatomicであること、
複数worker相当のstoreがalias割当を直列化することを実行します。

## 固定したbase imageの更新

base image は tag だけでなく**digest**で固定します。`python:3.12-slim` と
`redis:7-alpine` は継続的に再公開されるため、tag だけでは同じ commit から異なる
image が作られます。`tests/unit/test_supply_chain.py` は digest 固定が外れた場合に
失敗します。

CVE 対応または定期更新として意図的に固定値を更新する場合：

```bash
IMAGE=python TAG=3.12-slim
TOKEN=$(curl -s "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/$IMAGE:pull" | python3 -c "import json,sys;print(json.load(sys.stdin)['token'])")
curl -sI -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json" \
  "https://registry-1.docker.io/v2/library/$IMAGE/manifests/$TAG" | grep -i docker-content-digest
```

結果を `Dockerfile`／`docker-compose.yml` に `tag@sha256:...` として記載し、
可読性のため tag も残します。arm64 と amd64 の両方で解決できるよう、architecture
固有 manifest ではなく上記の **index digest** を使います。その後 image を再 build
して test suite を実行します。

**脆弱性対応**：digest を固定しても安全になるわけではなく、既知 CVE を含む状態も
固定します。upstream image の security update 時に再固定し、昇格前に
`docker scout cves` や `trivy image` などで build 済み image を scan してください。
固定は再現性と scan 対象を与えるものであり、scan の代替ではありません。

### supply chainの現状

| control | 状態 |
|---|---|
| base image の digest 固定 | **完了**（Dockerfile、Compose、test で強制） |
| runtime dependency の固定 | **完了**（`requirements.lock`、`--no-deps` で導入） |
| dev／CI dependency の固定 | **完了**（runtime を包含する `requirements-dev.lock`） |
| Python package の hash 検証 | **未実装** — lock は version だけを固定。`pip install --require-hashes` には hash 付き lock が必要 |
| image signing／provenance attestation | **未実装** — cosign signature と SLSA provenance を生成していない |
| SBOM 生成 | **未実装** — build 時に SBOM を出力していない |
| CI の脆弱性 scan | **未実装** — 現在は手動 |

4件の「未実装」は実際の残存リスクです。digest は build 対象を特定するだけで、
既知の脆弱性がないことも、実行中 image がこの repository の build 成果物であることも
証明しません。

## deploymentの診断

```bash
securitymasker doctor --config <dictionary.yaml>          # 人間向け
securitymasker doctor --config <dictionary.yaml> --json   # monitoring向け
```

いずれかの check が `FAIL` なら非0で終了します。Python／dependency version、
config load と engine build、全 `value_from_env`、detector pipeline、Presidio／HF
model availability、fail mode、session TTL、store backend、Redis package／URL、
master key の形式、AES-GCM round-trip、live store の write/read/delete probe と
cleanup、identity mode と secret、upstream scheme／host、dev-transparent mode、
public bind、Gateway readiness、local client の routing 状態を検査します。

`doctor` は master key、URL credential、dictionary value などの秘密を表示しません。
provider へも接続せず、upstream は構文だけを検証します。
