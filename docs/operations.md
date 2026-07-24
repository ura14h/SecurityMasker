# Operations

## Run locally (pip + venv)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,litellm]"
export SECURITYMASKER_CONFIG=config/securitymasker.example.yaml
litellm --config config/litellm.example.yaml --port 4000
```

`SECURITYMASKER_CONFIG` must point at your dictionary YAML; if unset, SecurityMasker
is a transparent no-op and the proxy behaves as vanilla LiteLLM (§38-17).

## Run with Docker (self-contained demo)

```bash
docker compose up --build
# then, in another shell:
curl http://127.0.0.1:4000/v1/chat/completions \
  -H 'Content-Type: application/json' -H 'X-SecurityMasker-Session-ID: demo' \
  -d '{"model":"securitymasker-openai","messages":[{"role":"user",
       "content":"担当は山田太郎、株式会社極秘技研の件です"}]}'
```

The compose stack routes the gateway to an in-compose mock upstream, so no real API
keys are needed. Enable Redis with `docker compose --profile redis up`.

## Codex / Claude Code

- **Codex**: point the custom model provider `base_url` at `http://127.0.0.1:4000/v1`,
  `wire_api = "responses"`, `supports_websockets = false`, and map
  `X-SecurityMasker-Session-ID` from `SECURITYMASKER_SESSION_ID`
  (`src/securitymasker/integrations/codex.py` prints a ready TOML block).
- **Claude Code**: set `ANTHROPIC_BASE_URL=http://127.0.0.1:4000` and the session
  header. Use the wrapper: `securitymasker run claude` / `securitymasker run codex`
  generates a session UUID and launches the tool.

## CLI

```bash
securitymasker config validate --config <dict.yaml>
securitymasker entities list    --config <dict.yaml>   # counts only, no values
securitymasker entities test "<text>" --config <dict.yaml>
securitymasker doctor --config <dict.yaml>
```

The CLI never prints original sensitive values (§12).

## Sessions

Idle TTL (default 4h) and absolute TTL (default 24h) are configurable in the
dictionary `defaults`. In-memory sessions live in the gateway process; for
multi-worker deployments use the Redis store with `SECURITYMASKER_MASTER_KEY`
(32 bytes, base64).

## Observability

Structured JSON logs and in-process counters (`securitymasker.metrics`) expose only
safe fields (§25). Do not enable LiteLLM verbose logging or unverified external log
sinks in production.

## Tests / CI

```bash
pytest tests/unit tests/evaluation -q          # fast
SM_RUN_LIVE=1 pytest tests/integration -q      # boots the real proxy + mock
python -m tests.evaluation.benchmark           # latency benchmark
```

CI (`.github/workflows/ci.yml`) runs ruff, mypy --strict, the test suites, and the
LiteLLM hook-contract compatibility guard.
