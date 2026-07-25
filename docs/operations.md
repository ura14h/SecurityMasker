# Operations

The proxy is a purpose-built transparent masking gateway (Starlette+httpx, no
LiteLLM — [ADR-0006](adr/0006-drop-litellm-purpose-built-proxy.md)).

## Run locally (pip + venv)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export SECURITYMASKER_CONFIG=config/securitymasker.example.yaml
securitymasker gateway --port 4000
```

`SECURITYMASKER_CONFIG` points at your dictionary YAML; if unset, the proxy forwards
transparently (no masking). Upstreams are configurable:
`SECURITYMASKER_OPENAI_UPSTREAM` (default `https://chatgpt.com/backend-api/codex`),
`SECURITYMASKER_ANTHROPIC_UPSTREAM` (default `https://api.anthropic.com`).

## Run with Docker (self-contained demo)

```bash
docker compose up --build
# then, in another shell (Responses API):
curl http://127.0.0.1:4000/responses \
  -H 'Content-Type: application/json' -H 'X-SecurityMasker-Session-ID: demo' \
  -d '{"model":"m","input":"担当は山田太郎、株式会社極秘技研の件です"}'
```

The compose stack routes the gateway to an in-compose mock upstream (no real keys).
Enable Redis with `docker compose --profile redis up`.

## Codex / Claude Code

- **Codex**: add a securitymasker provider to `~/.codex/config.toml` with
  `base_url = "http://127.0.0.1:4000"` (no `/v1`), `wire_api = "responses"`,
  `requires_openai_auth = true` (forwards the ChatGPT OAuth token — no API key),
  and map `X-SecurityMasker-Session-ID` from `SECURITYMASKER_SESSION_ID`. Generate
  the block: `python -c "from securitymasker.integrations.codex import codex_config_toml; print(codex_config_toml())"`.
- **Claude Code**: `ANTHROPIC_BASE_URL=http://127.0.0.1:4000` + the session header.
- Wrapper: `securitymasker run codex` / `securitymasker run claude` generates a
  session UUID and launches the tool.
- The proxy passes the client's own credentials through and never stores/logs them (§25).

## CLI

```bash
securitymasker gateway --port 4000 --config <dict.yaml>   # run the proxy
securitymasker config validate --config <dict.yaml>
securitymasker entities list    --config <dict.yaml>   # counts only, no values
securitymasker entities test "<text>" --config <dict.yaml>
securitymasker doctor --config <dict.yaml>
securitymasker run codex                               # launch under a session
```

The CLI never prints original sensitive values (§12).

## Sessions

Idle TTL (default 4h) and absolute TTL (default 24h) are configurable in the
dictionary `defaults`. In-memory sessions live in the gateway process; for
multi-worker deployments use the Redis store with `SECURITYMASKER_MASTER_KEY`
(32 bytes, base64).

## Observability

Structured JSON logs and in-process counters (`securitymasker.metrics`) expose only
safe fields (§25). The proxy never logs credentials or original values; keep
external log sinks off unless verified.

## Tests / CI

```bash
pytest tests/unit tests/evaluation -q                       # fast
SM_RUN_LIVE=1 pytest tests/integration/test_live_gateway.py -q  # proxy + mock
python -m tests.evaluation.benchmark                        # latency benchmark
```

CI (`.github/workflows/ci.yml`) runs ruff, mypy --strict, the test suites, and the
live gateway integration test.
