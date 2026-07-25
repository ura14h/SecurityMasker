# Architecture

SecurityMasker is a purpose-built transparent proxy (ADR-0006) that pseudonymizes
sensitive data before it leaves for an external LLM and restores it on the way back
(`doc/00-First-Order.md` §3). It owns both directions, so streaming restoration
works — the reason LiteLLM was dropped.

```
Codex (OpenAI Responses /responses) · Claude Code (Anthropic /v1/messages) — SSE
   │   client's own credentials (ChatGPT OAuth / Anthropic key) attached
   ▼
SecurityMasker Proxy  (securitymasker.gateway — Starlette + httpx)
   │   resolve session → mask request → forward (auth passed through, never logged, §25)
   │   → restore response (non-stream JSON and SSE stream)
   ▼
OpenAI / Anthropic / ChatGPT backend   (receives only masked data)
```

One handler invocation resolves the session, masks the request, forwards it, and
restores the response — so mask and restore always share one session (no pre/post
correlation problem).

## Layers

- **gateway/** — the proxy: `app` (routes), `forwarder` (transparent httpx forward,
  auth pass-through), `session` (session-id resolution), `identity` (tenant+user
  binding), `runtime` (engine/store + upstream config). SSE restoration lives in
  `streaming/`, not here — see the layout note below.
- **integrations/** — `codex` / `claude_code` client-config helpers (no LiteLLM).
- **protocols/** — `openai_responses`, `anthropic_messages`, `sse`,
  `structured_walker`. Decide *which* fields carry user text; never touch structure.
- **engine** — orchestrates normalize → detect → policy resolve → alias → replace →
  leak re-scan (mask) and alias → original (restore).
- **detectors/** — `existing_alias`, `dictionary`, `regex`, `secret_patterns`,
  `formats`, Japanese recognizers, optional `presidio` / `japanese_ner`.
- **aliases/** — replacement `profiles` + collision-safe `factory`.
- **sessions/** — `store` Protocol, `memory`, `redis`, `crypto`.
- **streaming/** — `text_replacer` (carry buffer), `tool_arguments`,
  `openai_responses_stream`, `anthropic_messages_stream`.
- **policy / normalization / models / config / cli / logging / metrics**.

## Data flow (request)

1. resolve session (header → previous_response_id → session-id/thread-id → ephemeral)
2. route by endpoint (`/responses` = OpenAI, `/messages` = Anthropic)
3. walk the protocol structure; for each user-text field: NFKC-normalize →
   run detectors → resolve overlaps (longest/highest-priority wins, existing
   aliases protected) → get/create alias → structure-preserving replace
4. pre-send re-scan; fail closed on any残存 secret
5. forward masked payload

## Data flow (response / stream)

The proxy owns the response, so restoration always reaches the client (unlike the
LiteLLM callbacks it replaced).

- Non-streaming: parse the upstream JSON, restore text/tool-arg fields, return it.
- Streaming (SSE bytes → decode → parse → restore → re-serialize):
  - OpenAI Responses (`streaming/openai_responses_stream`): `output_text.delta` via a
    per-block carry buffer; `output_text.done` / `content_part.*` / `output_item.*`
    / `response.completed` full text; `function_call_arguments` buffered and
    re-emitted as one restored delta.
  - Anthropic (`streaming/anthropic_messages_stream`): `text_delta` per-block carry
    buffer;
    `input_json_delta` reassembled to one restored delta.
- Only this session's `literal` aliases are restored; `env_reference` stays as
  `${...}` (§10, §19, §20, §21).

See [compatibility.md](compatibility.md) for pinned versions and verified upstream
event shapes, and [adr/](adr/) for design decisions (esp. ADR-0006).


## Module ownership (updated after the round-4 review)

One responsibility, one home:

- `context/` — segmentation only. Knows nothing about detectors, HTTP or sessions.
- `detectors/` — detection. `detectors/inference.py` owns the bounded pool that all
  model-backed detectors share, so the thread ceiling is global rather than
  per-detector.
- `streaming/` — SSE restoration for BOTH protocols:
  `openai_responses_stream.py` and `anthropic_messages_stream.py`. They previously
  lived in different packages (`gateway/` vs `streaming/`) for no reason other
  than the order they were written; the split invited the assumption that one was
  gateway-specific.
- `gateway/` — request orchestration. `gateway/identity.py` owns assertion
  verification, so no crypto sits in a request handler and the store receives an
  already-validated namespace.
- `integrations/` — per-client knowledge (Codex/Claude), keeping provider
  branching out of the CLI.
- `devtools/` — the Compose demo's mock upstream and manual tooling. Deliberately
  NOT under `tests/`: a runnable service is not a test, and pointing a Compose
  service at a test module makes the test namespace a deployment dependency.

The masking core (`engine`, `policy`, `aliases`, `detectors`, `normalization`)
imports nothing from `gateway`, `integrations`, `cli` or `doctor`, and every
optional dependency (torch, transformers, presidio, redis) is imported inside a
function so a minimal install never loads them.
