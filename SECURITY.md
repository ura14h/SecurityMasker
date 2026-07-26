# SECURITY

SecurityMasker is a **security boundary**: it keeps original sensitive data from
leaving the trusted zone for external LLMs. This document states its guarantees,
limits, and hardening guidance (`doc/00-First-Order.md` §33).

## Guarantees (and how they are enforced)

- **No original secret reaches an external LLM.** The gateway masks the request
  before forwarding, and re-scans the FINAL payload and headers immediately before
  they are sent; a registered/high-confidence secret still present fails closed
  (`engine._verify_no_leak` and `engine.assert_no_leak_in_payload`, §18). Verified
  end-to-end against a local mock upstream by
  `tests/integration/test_live_gateway.py`, which asserts on what actually left
  the process.
- **Sessions/tenants are isolated.** Aliases are per-session (HMAC keyed by a
  per-session CSPRNG key); restoration only reverses *this* session's aliases (§7).
  Redis keys are tenant-namespaced and sealed under a process master key (§8).
- **Structure is preserved.** Only string values are transformed; ids, types, tool
  names, and JSON Schema keys are never changed (§16).
- **Fail-closed by default.** Detector/crypto/session/stream failures block the
  request rather than forwarding original data (§26).

## Secrets and keys

- API keys / private keys default to `env_reference`: they become
  `${SECURITYMASKER_SECRET_...}` and are **never restored to their literal value**
  in responses (§10, §27), so they don't land in shell history or process lists.
- Per-session keys come from `secrets.token_bytes`, never derived from the session
  id. The Redis store seals everything under `SECURITYMASKER_MASTER_KEY` (32 bytes,
  base64); that master key must come from the environment or a Secret Manager and
  must not be stored in Redis (§8).

## Logging (§25)

- Original secrets, decrypted mappings, keys, auth headers, full prompts/responses,
  and restored tool arguments are **never** logged.
- Only safe fields (entity types, counts, timings, irreversible fingerprints) are
  logged/audited (`securitymasker.metrics`, `securitymasker.logging`).
- Do not enable request/response body logging anywhere in the path — the gateway's
  own debug logging, a reverse proxy in front of it, or an APM agent. The gateway
  handles original text before masking and restored text after, so a body log is a
  copy of exactly what this product exists to keep out of external systems.
- External log/telemetry integrations stay disabled until it is verified that they
  do not forward raw request bodies.

## Known limits (§34)

Not handled (blocked if detected, never silently forwarded): text in images/audio,
binary/compressed/encrypted files, recursive Base64, full AST analysis of every
language, the WebSocket Responses API, passing real values to hosted tools, 100%
detection of unregistered Japanese names/addresses, and reconstructing values from
model-mutated aliases. Python cannot guarantee memory zeroization — run the gateway
locally / on a trusted network, restrict swap, disable core dumps, use a read-only
container filesystem, and keep the admin port off public interfaces (§33).

## Reporting a vulnerability

This is a reference implementation. Report issues privately to the maintainers
before public disclosure. Do not include real secrets in reports.
