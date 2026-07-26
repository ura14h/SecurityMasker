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

`SECURITYMASKER_CONFIG` points at your dictionary YAML and is **required** — the
gateway fails to start without it (fail-closed, doc/06 P0-1). A masking-free
development mode exists only behind the explicit `SECURITYMASKER_DEV_TRANSPARENT=1`
flag and must never front a real provider. Upstreams are configurable:
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

To use the shared Redis session store you must start Redis **and** switch the
gateway to it — starting the container alone leaves the gateway on its in-process
store:

```bash
docker compose -f docker-compose.yml -f docker-compose.redis.yml --profile redis up
```

A shell prefix (`SECURITYMASKER_STORE=redis docker compose ...`) does **not** do
this. Compose reads such a variable for substitution in the YAML, but it does not
pass it into a container unless the file says so — so that form starts Redis and
leaves the gateway on its in-process store, silently. The overlay is what sets
both `SECURITYMASKER_STORE` and `SECURITYMASKER_REDIS_URL` on the service.

Compose supplies a demo `SECURITYMASKER_MASTER_KEY` (32 bytes, base64) so the
stack starts; generate your own for anything real and inject it from a secret
store — the gateway fails closed if it is missing or not 32 bytes:

```bash
openssl rand -base64 32
```

## Codex / Claude Code

- **Codex**: add a securitymasker provider to `~/.codex/config.toml` with
  `base_url = "http://127.0.0.1:4000"` (no `/v1`), `wire_api = "responses"`,
  `requires_openai_auth = true` (forwards the ChatGPT OAuth token — no API key),
  and map `X-SecurityMasker-Session-ID` from `SECURITYMASKER_SESSION_ID`. Generate
  the block: `python -c "from securitymasker.integrations.codex import codex_config_toml; print(codex_config_toml())"`.
- **Claude Code**: `ANTHROPIC_BASE_URL=http://127.0.0.1:4000` + the session header.
- Wrapper: `securitymasker run codex` / `securitymasker run claude` sets up the
  proxy route or refuses to start the tool: it requires `/ready` to report
  `ready: true`, sets `ANTHROPIC_BASE_URL` + session header for Claude Code and
  per-process `-c` overrides for Codex (never editing `~/.codex/config.toml`), and
  refuses outright if `ANTHROPIC_API_URL`/`OPENAI_BASE_URL`/`OPENAI_API_BASE` would
  bypass it or if the tool is one it cannot route. It generates a session UUID and
  launches the tool.

  **Scope of that claim.** Unit tests cover the generated settings, the TOML
  validity of the overrides, the fact that the user's real config is untouched,
  and the refusal paths. On top of that, `tests/integration/test_real_cli_e2e.py`
  launches the **real** `codex` and `claude` binaries through `run` against a mock
  upstream and asserts that only aliases leave the process, that the session
  header actually arrives, and that no `config.toml` is written into the tool's
  home. It is opt-in because it spawns real processes:

```bash
SM_RUN_CLI_E2E=1 .venv/bin/python -m pytest tests/integration/test_real_cli_e2e.py -v
```

  It refuses to run without an egress boundary, and checks rather than asks.
  Pointing a CLI at a local URL is a routing choice, not containment: both tools
  make update, analytics and crash-reporting requests that never go through the
  configured provider, so a routing mistake would reach the internet instead of
  failing. Before anything starts, the suite opens a TCP connection to a routable
  address; if that succeeds it skips, because this process — and therefore the
  CLIs — can reach the internet.

  To actually run it, put the whole stack in one network namespace (Linux):

```bash
./devtools/run_cli_e2e.sh
```

  or a container with no network at all, using an image that has the CLIs
  installed:

```bash
docker run --rm --network none -v "$PWD:/w" -w /w <image> devtools/run_cli_e2e.sh
```

  Isolating only the CLI does not work: a namespace has its own loopback, so the
  CLI could not reach the gateway. CI runs this and treats a skip as a failure.

  What remains uncovered is the provider itself: the E2E upstream is a local mock,
  deliberately, so nothing here says how a real provider behaves.
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


## Updating pinned base images

Base images are pinned by **digest**, not only by tag: `python:3.12-slim` and
`redis:7-alpine` are republished continuously, so a tag-only reference means two
builds of the same commit can contain different bases. `tests/unit/test_supply_chain.py`
fails if any image loses its digest, so this cannot silently regress.

To refresh a pin (do this deliberately — for a CVE fix, or on a routine cadence):

```bash
IMAGE=python TAG=3.12-slim
TOKEN=$(curl -s "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/$IMAGE:pull" | python3 -c "import json,sys;print(json.load(sys.stdin)['token'])")
curl -sI -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json" \
  "https://registry-1.docker.io/v2/library/$IMAGE/manifests/$TAG" | grep -i docker-content-digest
```

Put the result in `Dockerfile` / `docker-compose.yml` as `tag@sha256:...`, keeping
the tag for readability. Use the **index** digest shown above (not a
per-architecture manifest digest) so the pin resolves on both arm64 and amd64.
Then rebuild and re-run the suite.

**Vulnerability response.** A pinned digest does not become safe by being pinned —
it freezes a known state, including known CVEs. Re-pin when the upstream image
publishes a security update, and scan the built image (for example
`docker scout cves` or `trivy image`) before promoting it. Pinning gives you
reproducibility and a defined thing to scan; it is not a substitute for scanning.

### Supply-chain status, honestly

| Control | State |
|---|---|
| Base image digest pinning | **done** (Dockerfile, compose, enforced by a test) |
| Runtime dependency pinning | **done** (`requirements.lock`, installed with `--no-deps`) |
| Dev/CI dependency pinning | **done** (`requirements-dev.lock`, a superset of runtime) |
| Python package hash verification | **not implemented** — the locks pin versions, not hashes. `pip install --require-hashes` needs a hash-bearing lock; a follow-up. |
| Image signing / provenance attestation | **not implemented** — no cosign signature or SLSA provenance is produced. |
| SBOM generation | **not implemented** — no SBOM is emitted at build time. |
| Vulnerability scanning in CI | **not implemented** — scanning is a manual step today. |

The four "not implemented" rows are real residual risk, not oversights being
papered over: a pinned digest tells you *what* you built, not that it is free of
known vulnerabilities, and nothing here proves the image you run is the image this
repository built.


## Diagnosing a deployment

```bash
securitymasker doctor --config <dictionary.yaml>          # human-readable
securitymasker doctor --config <dictionary.yaml> --json   # for monitoring
```

Exits non-zero if any check FAILED. Checks cover Python and dependency versions,
config load + engine build, every `value_from_env`, the detector pipeline,
Presidio/HF model availability, fail mode, session TTLs, store backend, Redis
package/URL, master-key shape, an AES-GCM round-trip, a live store
write/read/delete probe (with cleanup verified), identity mode and its secret,
upstream scheme/host, dev-transparent mode, public bind, gateway readiness, and
whether the local clients look routed.

`doctor` never prints a secret — not the master key, not URL credentials, not
dictionary values — and never contacts a provider: upstreams are validated
syntactically only.
