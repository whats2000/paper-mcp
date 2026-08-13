# paper-mcp Plan D — OIDC auth + quota

**Goal:** the endpoint can face the public internet. A bearer JWT identifies the caller, per-subject buckets stop one caller starving the rest, and `AUTH_MODE=open` stays a loud development-only escape.

**What identity is for here:** quota and revocation, nothing else. There is no per-user data to scope (SRS NFR-01), so every authenticated caller can reach every tool. Authorisation would be theatre; metering is the real need.

## Global Constraints

Plans A–C hold. Plus:

- **This service is a resource server, never an authorization server.** It validates tokens against a configured issuer; it does not issue, refresh, or consent. Owning token lifecycle is a security-critical subsystem far from this project's competence (SRS §II-4).
- **A 401 must not leak.** Invalid signature, wrong audience, expired, and malformed all return the same shape.
- **Quota exhaustion is a typed answer with `retry_after`,** never a hang or a silent drop.

## Tasks

### Task 1: JWT verification
`src/paper_mcp/auth.py` — fetch JWKS from the issuer, cache it, verify signature/`iss`/`aud`/`exp`/`nbf`, return a `Principal`. A `kid` that misses the cache triggers exactly one refetch (keys rotate; a stale cache must not lock everyone out), and that refetch is rate-limited so an attacker cannot use unknown `kid`s to hammer the IdP.

The logged identity is `HMAC(salt, sub)`, not `sub` — metering needs a stable key, not a record of who read what.

### Task 2: Token buckets
`src/paper_mcp/quota.py` — refill-over-time buckets per `(subject, resource)`. Three resources, because they are scarce in different ways: **calls/minute** (cheap, bursty), **extractions/hour** (GPU minutes), **compile-seconds/hour** (CPU). Unauthenticated mode meters per-IP instead.

### Task 3: Enforcement
Middleware on the app: authenticate, meter, attach the principal. `/health` stays open — a readiness probe that needs a token is useless.

### Task 4: Real-connection verification
Sign a token with a local key, serve JWKS, drive the service over MCP with and without it. Assert: no token → 401, bad audience → 401, valid → tools work, over-quota → typed `quota_exceeded` with `retry_after`.

## Deliberately out of scope

Token issuance, refresh, consent screens, per-user data scoping, and role-based tool access. The first three belong to an IdP; the last two have nothing to scope in a stateless service.
