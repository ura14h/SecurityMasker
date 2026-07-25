# ADR-0008 — Caller identity and the tenant/user isolation boundary

- Status: Accepted
- Date: 2026-07-25
- Relates to: ADR-0005 (per-session keys), ADR-0006 (purpose-built proxy), doc/06 P0-9

## Context

Alias tables are keyed by session. Whoever can name a session id can read and
extend that session's mappings, so "who is asking" *is* the confidentiality
boundary.

Until now the proxy had two modes: `local` (one implicit tenant) and
`multitenant`, where the tenant came from a header signed with a shared secret.
An audit correctly found two problems:

1. the name `multitenant` implied more isolation than it delivered — the HMAC
   covered only the tenant id, so two users **inside** one tenant shared an alias
   table and either could present the other's session id;
2. signing a single field leaves the composition unbound. Once a user id is added,
   signing tenant and user separately would let a caller pair tenant A's proof
   with user B's.

## Decision

**Three explicit modes**, named for exactly what they isolate:

| Mode | Isolates | Intended for |
|---|---|---|
| `local` | nothing (single implicit caller) | one workstation, one user |
| `tenant` | tenant | one customer per tenant |
| `tenant_user` | tenant **and** user | mutually distrusting users in one tenant |

`multitenant` still resolves to `tenant`, so existing deployments keep working and
keep exactly the isolation they had — no silent upgrade, no silent downgrade.

**A versioned, jointly-signed assertion.** The trusted authenticator computes
`HMAC-SHA256(secret, canonical_payload)` where the payload is

```
"v2" ‖ tenant ‖ user ‖ timestamp        (each component length-prefixed, ␟-joined)
```

- **Joint** signing binds the fields: no field can be swapped or recombined.
- **Length-prefixed** encoding removes delimiter ambiguity, so `("a", "b:c")` and
  `("a:b", "c")` can never produce the same payload.
- **Versioned**: a future format change cannot be replayed against this one.
- Verified with `hmac.compare_digest` — a timing oracle would leak the expected
  proof byte by byte.
- A **mandatory** timestamp is checked against a configurable skew
  (`SECURITYMASKER_MAX_CLOCK_SKEW_SECONDS`, default 300s), bounding replay. It was
  optional at first, which meant a captured proof stayed valid for the lifetime of
  the secret — so it is now required, and omitting it takes an explicit downgrade
  (`SECURITYMASKER_ALLOW_UNTIMED_ASSERTIONS=1`).
- The timestamp must be **decimal integer seconds since the epoch**. Fixing the
  form matters as much as checking the value: `float()` accepts `nan`, and every
  comparison against NaN is False, so a naive `abs(now - ts) > skew` check
  *passes* for `nan`. That hole was real and is now closed by validating the form
  before the value. `1.7e9`, `0x64`, `12.5` and whitespace-padded values are
  likewise refused rather than silently coerced.

**The boundary is enforced in depth**, not at one checkpoint:

- store keys use the length-prefixed identity namespace, so the same session id
  under different identities is a different key;
- response bindings (`previous_response_id` continuity) use the same namespace, so
  one user cannot resume another's conversation;
- both stores additionally verify the session's recorded `tenant_id`/`user_id` on
  read and raise rather than return a mismatched session.

**Fail-closed everywhere.** A non-`local` mode without a configured secret fails at
startup. A missing, forged, recombined, or expired assertion is a 403 with nothing
forwarded. Identity errors never echo the proof, the claimed identity, or the
secret; logs use a truncated SHA-256 fingerprint of the namespace.

## Alternatives considered

- **JWT (RS256/EdDSA).** Standard, supports key rotation and expiry natively, and
  many authenticators already mint them. Rejected *for now* because it needs a JWT
  library and a key-distribution story for a single header we fully control; the
  canonical-payload HMAC gives the same binding with no new dependency. If a
  deployment already issues JWTs, adding a verifier is the natural next step and
  the `Identity` interface is where it plugs in.
- **mTLS.** Strongest transport-level identity, and it authenticates the *channel*
  rather than a header. Rejected as the baseline because it pushes certificate
  issuance and rotation onto every client (Codex and Claude Code do not offer it
  per-process), and the proxy would still need a header to distinguish users
  behind one client certificate.
- **Trusted reverse proxy asserting bare headers.** Simplest, and common in
  practice. Rejected as the default because it is indistinguishable, from our
  side, from a client setting the header itself: the security depends entirely on
  a network invariant we cannot verify. The signed assertion makes the trust
  explicit and detectable, and a reverse proxy can still be the thing that signs.
- **Per-user secrets instead of one shared secret.** Better blast radius, but it
  needs a secret-distribution mechanism we do not have. Recorded as a possible
  evolution.

## Consequences

- Deployments wanting user isolation must set `SECURITYMASKER_MODE=tenant_user`
  and have their authenticator send `X-SecurityMasker-User-ID` plus an assertion
  over both fields. There is no way to get user isolation implicitly — which is
  the point.
- The shared secret is a symmetric credential: anyone holding it can mint any
  identity. It must live with the authenticator only, and rotating it invalidates
  outstanding proofs immediately (acceptable: they are per-request).
- Timestamps are required, so an authenticator that does not send one must be
  updated (or run with the explicit `SECURITYMASKER_ALLOW_UNTIMED_ASSERTIONS=1`
  downgrade, which doctor should flag). This is a deliberate compatibility break:
  the alternative was leaving every deployment replayable by default.

## Residual risk

- The proxy trusts whatever the authenticator asserts. A compromised
  authenticator can impersonate any caller — unavoidable for any assertion-based
  scheme, and the reason mTLS remains on the table for higher-assurance setups.
- `tenant` mode remains *not* safe for mutually distrusting users in one tenant.
  The mode name says so, the docs say so, and `tenant_user` exists for that case.
