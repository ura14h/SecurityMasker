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
  auth pass-through), `responses_stream` (OpenAI Responses SSE restorer),
  `session` (session-id resolution), `runtime` (engine/store + upstream config).
- **integrations/** — `codex` / `claude_code` client-config helpers (no LiteLLM).
- **protocols/** — `openai_responses`, `anthropic_messages`, `sse`,
  `structured_walker`. Decide *which* fields carry user text; never touch structure.
- **engine** — orchestrates normalize → detect → policy resolve → alias → replace →
  leak re-scan (mask) and alias → original (restore).
- **detectors/** — `existing_alias`, `dictionary`, `regex`, `secret_patterns`,
  `formats`, Japanese recognizers, optional `presidio` / `japanese_ner`.
- **aliases/** — replacement `profiles` + collision-safe `factory`.
- **sessions/** — `store` Protocol, `memory`, `redis`, `crypto`.
- **streaming/** — `text_replacer` (carry buffer), `tool_arguments`, `anthropic_stream`.
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
  - OpenAI Responses (`gateway/responses_stream`): `output_text.delta` via a
    per-block carry buffer; `output_text.done` / `content_part.*` / `output_item.*`
    / `response.completed` full text; `function_call_arguments` buffered and
    re-emitted as one restored delta.
  - Anthropic (`streaming/anthropic_stream`): `text_delta` per-block carry buffer;
    `input_json_delta` reassembled to one restored delta.
- Only this session's `literal` aliases are restored; `env_reference` stays as
  `${...}` (§10, §19, §20, §21).

See [compatibility.md](compatibility.md) for pinned versions and verified upstream
event shapes, and [adr/](adr/) for design decisions (esp. ADR-0006).
