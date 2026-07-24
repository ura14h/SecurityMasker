# Architecture

SecurityMasker is a thin extension to LiteLLM Proxy that pseudonymizes sensitive
data before it leaves for an external LLM and restores it on the way back
(`doc/00-First-Order.md` §3). The masking core is independent of LiteLLM.

```
Codex / Claude Code
   │  OpenAI Responses (/v1/responses) · Chat (/v1/chat/completions)
   │  Anthropic Messages (/v1/messages)  — all SSE-capable
   ▼
LiteLLM Proxy ── SecurityMaskerCallback (integrations/litellm.py)  ← only file importing LiteLLM
   │                 pre_call:   mask request        (fail-closed)
   │                 post_call:  restore response
   │                 streaming:  restore SSE stream
   ▼
OpenAI / Anthropic API   (receives only masked data)
```

## Layers

- **integrations/** — LiteLLM adapter (version-pinned, the only LiteLLM import) +
  `runtime` (engine/store construction, session-id resolution). `codex`/`claude_code`
  wrappers set the session header.
- **protocols/** — `openai_responses`, `anthropic_messages`, `sse`,
  `structured_walker`. Decide *which* fields carry user text; never touch structure.
- **engine** — orchestrates normalize → detect → policy resolve → alias → replace →
  leak re-scan (mask) and alias → original (restore).
- **detectors/** — `existing_alias`, `dictionary`, `regex`, `secret_patterns`,
  `formats`, Japanese recognizers, optional `presidio` / `japanese_ner`.
- **aliases/** — replacement `profiles` + collision-safe `factory`.
- **sessions/** — `store` Protocol, `memory` (Phase 1), `redis` (Phase 5), `crypto`.
- **streaming/** — `text_replacer` (carry buffer), `tool_arguments`, `anthropic_stream`.
- **policy / normalization / models / config / cli / logging / metrics**.

## Data flow (request)

1. resolve session (header → previous_response_id → ephemeral)
2. route by `call_type` (Anthropic vs OpenAI)
3. walk the protocol structure; for each user-text field: NFKC-normalize →
   run detectors → resolve overlaps (longest/highest-priority wins, existing
   aliases protected) → get/create alias → structure-preserving replace
4. pre-send re-scan; fail closed on any残存 secret
5. forward masked payload

## Data flow (response / stream)

- Non-streaming: restore text/tool-arg fields of the live response object.
- Streaming: chat delta / Responses `OutputTextDeltaEvent` restored via a carry
  buffer; Anthropic `/v1/messages` arrives as raw SSE bytes → decode → parse →
  restore `text_delta` (per-block buffer) and reassemble `input_json_delta` →
  re-serialize. Only this session's `literal` aliases are restored; `env_reference`
  stays as `${...}` (§10, §19, §20, §21).

See [compatibility.md](compatibility.md) for pinned versions and the exact hook /
event shapes, and [adr/](adr/) for design decisions.
