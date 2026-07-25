# Configuration

The proxy (ADR-0006) is configured by the **SecurityMasker dictionary**
(`SECURITYMASKER_CONFIG`) plus a few environment variables. Example dictionary in
[`config/securitymasker.example.yaml`](../config/securitymasker.example.yaml).

## Running the proxy

```bash
export SECURITYMASKER_CONFIG=config/securitymasker.example.yaml
securitymasker gateway --port 4000
```

Environment:

- `SECURITYMASKER_CONFIG` — dictionary/policy YAML (below). **Required**: if unset the
  gateway fails to start (fail-closed, doc/06 P0-1). To run without masking for
  development only, set `SECURITYMASKER_DEV_TRANSPARENT=1` explicitly — never point that
  mode at a real provider.
- `SECURITYMASKER_OPENAI_UPSTREAM` — default `https://chatgpt.com/backend-api/codex`
  (Codex ChatGPT auth). For API-key OpenAI: `https://api.openai.com/v1`.
- `SECURITYMASKER_ANTHROPIC_UPSTREAM` — default `https://api.anthropic.com`.
- `SECURITYMASKER_MODE` — `local` (default; one implicit tenant) or `multitenant`
  (tenant read from `SECURITYMASKER_TENANT_HEADER`, which a trusted authenticator sets;
  missing tenant fails closed, doc/06 P0-9).
- `SECURITYMASKER_STORE` — `memory` (default) or `redis`; `redis` also needs
  `SECURITYMASKER_REDIS_URL` and fails closed if the package/URL is missing (doc/06 P1-9).
- `SECURITYMASKER_MASTER_KEY` — 32 bytes base64, required by the Redis session store (§8).

Clients: Codex uses a `requires_openai_auth = true` provider pointing at the proxy
(the ChatGPT OAuth token is passed through — no API key); Claude Code sets
`ANTHROPIC_BASE_URL`. See [operations.md](operations.md).

## Dictionary (SECURITYMASKER_CONFIG)

```yaml
version: 1
defaults:
  fail_mode: closed            # fail-closed on error (§26)
  normalization: nfkc          # detection normalization; restore keeps original form
  merge_surface_forms: false   # per-surface-form aliases
  session_idle_ttl: 4h
  session_absolute_ttl: 24h

entities:                      # highest-trust, exact match (§12)
  - id: employee
    type: PERSON
    values: ["山田太郎", "山田 太郎"]     # multiple surface forms
    # value_from_env: EMPLOYEE_NAME       # or pull from env (no plaintext secrets!)
    replacement_profile: prose_identifier
    restore_policy: literal               # literal | env_reference | redacted | block
    priority: 100

patterns:                      # user regexes
  - id: ticket
    pattern: 'INC-[0-9]{6}'
    type: CUSTOMER_ID
    replacement_profile: numeric

enable_secret_detector: true   # API keys / JWT / PEM / DB URLs -> env_reference
enable_format_detectors: true  # email / IPv4 / credit card (Luhn -> block)

japanese_pii:
  enabled: true
  my_number_restore_policy: block

presidio:                      # optional, in-process; no-op if not installed
  enabled: false
  language: ja
ner:                           # optional Japanese NER; model never hardcoded (§14.1)
  model: null                  # e.g. a HF token-classification model id
```

## Replacement profiles (§9)

`prose_identifier` (`SM_PERSON_2B891C`), `hostname` (`sm-host-….example.invalid`),
`email` (`sm-user-…@example.invalid`), `ipv4`/`ipv6` (documentation ranges), `uuid`,
`numeric` (digit-count preserving), `file_path`, `url`, `environment_reference`
(`${SECURITYMASKER_SECRET_…}`).

## Restore policies (§10)

`literal` (restore before returning), `env_reference` (keep `${…}`, never restore
the real value), `redacted` (irreversible `[REDACTED]`), `block` (reject the
request). Defaults: names/addresses/hosts/paths → literal; API keys/passwords/keys →
env_reference; My Number → block; credit card → block.

## Environment variables

- `SECURITYMASKER_CONFIG` — path to the dictionary YAML (required to mask).
- `SECURITYMASKER_SESSION_ID` — session id the wrapper/headers propagate.
- `SECURITYMASKER_MASTER_KEY` — 32 bytes base64, required by the Redis store (§8).
- Values referenced by `value_from_env` in the dictionary.

Config is validated at load; invalid enums / regex / duplicate ids fail startup (§12).
