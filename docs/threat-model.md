# Threat Model

Scope: keep original sensitive data inside the trusted zone; don't break payloads;
stay maintainable against LiteLLM/Codex/Claude Code updates (`doc/00-First-Order.md`
§5, §33).

## Trust boundaries

- **Trusted**: the local machine running Codex/Claude Code, the SecurityMasker
  Gateway, the session store, explicitly-trusted local tools.
- **Untrusted**: OpenAI/Anthropic and other external LLMs, external telemetry/log
  services, external MCP servers, hosted (provider-side) tools.

Original secrets must never cross into the untrusted zone. External MCP restoration
is disabled by default (§5).

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| Original secret sent to external LLM | pre-call mask + per-text re-scan + final payload-wide block-only guard over every string incl. dict keys/unknown/structural fields, fail-closed (§18, §26, doc/06 P0-4); live leakage test |
| Cross-session / cross-tenant leakage | per-session HMAC keys; restore only this session's aliases; tenant-namespaced, master-key-sealed Redis (§7, §8) |
| Provider correlates users across sessions | different alias per session for the same secret (§6) |
| Model mutates aliases (case/split/translate) | restore only exact, this-session-issued aliases; no approximate restore (§24, doc/06 P0-7) |
| Real secret restored into an external/MCP tool | tool-argument restore is default-untrusted; only allowlisted local tools get real values, else aliases (doc/06 P0-8) |
| Prompt injection via model output | output is treated as data; only alias→original substitution, structure re-validated (§19) |
| Alias collision | detect + lengthen token; exhaustion raises (§7) |
| Log / error / telemetry leakage | only safe fields logged; errors carry no secret; verbose logging off (§25) |
| Cache/Redis/memory disclosure | AES-GCM sealing; keys not in Redis; local/trusted network, restrict swap & core dumps (§8, §33) |
| DoS via huge input | bounded scans; near-linear pipeline (clustered overlap resolution, deduped leak scan); size caps (§32) |
| Catastrophic-backtracking regex | anchored/bounded patterns; scan-length cap (§32) |
| Restored secret in dangerous shell command | client tool-approval is never bypassed; secrets prefer env_reference (§27) |
| Structure breakage (JSON/code/patch) | value-only transforms; re-serialize tool JSON; structural keys untouched (§16) |
| Codex/Claude Code adds unknown fields/events | unknown fields/events pass through **only after** the final block-only leak guard clears them of registered/high-confidence secrets (§22, §23, doc/06 P0-4) |

## Residual risk

Python cannot guarantee memory zeroization; unregistered Japanese names/addresses
are not detected with 100% recall; heuristic recognizers have false negatives. Run
on a trusted network with the hardening in [SECURITY.md](../SECURITY.md).
