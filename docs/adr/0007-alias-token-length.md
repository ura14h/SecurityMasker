# ADR-0007 — Alias token length and shape-constrained alias spaces

- Status: Accepted
- Date: 2026-07-25
- Supersedes: nothing. Relates to ADR-0005 (alias HMAC/AES-GCM), doc/06 P1-8.

## Context

An audit found the alias space too small to support the guarantees the docs
implied:

- prose aliases (`SM_PERSON_7F3A91`) used a **6 hex char = 24 bit** token;
- IPv4 aliases used a single RFC 5737 `/24`, i.e. **254 possible values**;
- short `numeric` aliases inherit the digit count of the original, so a 4-digit
  original has only 10,000 possible aliases;
- collisions were detected **within** a session but never across sessions, while
  the docs claimed aliases are "different per session".

24 bits is weak on two axes. Birthday collisions: with 1,000 mappings in one
session the probability of at least one collision is roughly
`1 - exp(-1000^2 / 2^25) ≈ 3 x 10^-2` — about 1 in 34, which is far too likely for
something the collision path is supposed to make rare. Guessing: 16.7M candidates
is trivially enumerable offline, so an attacker who learns the alias *format* can
brute-force a specific token space cheaply.

## Decision

1. **Raise the default alias token to 12 hex chars (48 bits).** With the enforced
   ceiling of 10,000 mappings per session (`MAX_MAPPINGS_PER_SESSION`), the
   birthday collision probability is `~1 - exp(-10^8 / 2^49) ≈ 1.8 x 10^-10`.
   Collision lengthening is retained, so 12 hex is a floor and not a cap.

2. **Use all three RFC 5737 documentation ranges for IPv4** (`192.0.2.0/24`,
   `198.51.100.0/24`, `203.0.113.0/24`), giving 762 aliases per session instead
   of 254.

3. **Accept that shape-preserving profiles have small spaces, and fail closed when
   they are exhausted.** An IPv4 alias must remain a valid, non-routable IPv4
   address, so 762 is the honest ceiling; a 4-digit numeric alias cannot exceed
   10,000. When every candidate is taken, `aliases.factory` raises
   `AliasCollisionError`, which surfaces as a blocked request — never a reused
   alias pointing at two different secrets.

4. **State the cross-session property accurately.** Keys are per-session and
   independent, so the same secret gets an *independent* alias in another
   session. For large spaces (prose, hostname, email, env reference) a collision
   is negligible; for IPv4/numeric two sessions will often coincide simply because
   the space is small. We therefore claim **unlinkability of the mapping**, not
   "always a different alias".

## Consequences

- Aliases are longer: `SM_PERSON_7F3A91` becomes `SM_PERSON_7F3A9155C21B`. Prompts
  grow marginally; readability is unaffected.
- Sessions that mask more than 762 distinct IPv4 addresses (or more distinct
  n-digit numbers than n digits allow) now fail closed instead of silently
  reusing an alias. This is the intended safety behaviour; operators who hit it
  should switch those entities to the `prose_identifier` profile, which has no
  shape constraint.
- The documentation ranges are non-routable by definition, so an alias can never
  be mistaken for — or accidentally reach — a real endpoint.

## Alternatives considered

- **RFC 6598 shared address space (100.64.0.0/10)** for ~4M IPv4 aliases:
  rejected. It is real, routable-within-carrier space; an alias that looks like a
  reachable host is exactly what §9.4 forbids.
- **Dropping IPv4 shape preservation** (aliasing addresses as `SM_IP_...`):
  rejected as the default because it breaks config files, connection strings, and
  any code that parses the value. It remains available by choosing a different
  profile for that entity.
- **Global (cross-session) collision detection**: rejected. It would require a
  shared index of alias values across sessions, re-introducing exactly the
  cross-session linkage the per-session key design exists to prevent.
