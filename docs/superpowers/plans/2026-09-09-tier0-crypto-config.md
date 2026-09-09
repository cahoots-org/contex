# Tier 0 Crypto/Config Hardening (#64, #68, #69) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Close the last three Tier 0 "hardened-mode is a lie" gaps — CORS wildcard+credentials (#64), ephemeral JWT secret (#68), and unsalted API-key hashing with dead `API_KEY_SALT` (#69) — all as **non-breaking** changes (no migration, no key re-issue, demo/auth-off behavior unchanged).

**Architecture:** A single API-key hashing module (`hash_api_key` for storage, `candidate_hashes` for lookup) makes `API_KEY_SALT` a live HMAC pepper with dual-verify fallback to legacy plain-SHA. JWT signing secret is sourced from config/env and fail-closed when auth is on. CORS coerces credentials off under a wildcard origin.

**Tech Stack:** Python 3.12, FastAPI, `pyjwt`, `hashlib`/`hmac`, Postgres+pgvector, pytest (live DB via `tests/conftest.py`; full suite via plain shell — a watchdog kills suite-running subagents, so agents run TARGETED tests only).

**Basis:** recon 2026-09-09. Config lives in `src/core/config.py` (`SecurityConfig` already has `api_key_salt`, `cors_allow_credentials` fields + a `validate_config()`), but `main.py` reads CORS via `os.getenv` directly and hashing/JWT read env directly.

## Global Constraints (rulings — surfaced for Rob's review)

- **No DB migration; no API-key re-issue.** #69 uses dual-verify so existing plain-SHA hashes keep working. Alembic head unchanged.
- **Demo/auth-off unchanged:** with no `API_KEY_SALT` set, hashing stays plain SHA-256; with `AUTH_ENABLED=false`, JWT keeps its random fallback and CORS keeps its current default.
- **#64:** if `*` is in the CORS origins, force `allow_credentials=False` (browsers reject `*`+creds anyway; this removes the false sense) and log a warning. Do not change the default origins.
- **#68:** `SERVICE_ACCOUNT_JWT_SECRET` sourced from env/config, read at token issue/verify time (not frozen at import); when `AUTH_ENABLED=true` and it is unset → **raise at startup** (fail-closed). Auth-off keeps the random fallback + warning.
- **#69:** `hash_api_key(raw_key)` = HMAC-SHA256(key, `API_KEY_SALT`) when the salt is set, else plain `sha256(key)`. Lookups try BOTH the peppered and the plain-SHA hash (so pre-pepper keys still verify). All 6 hash sites use the shared helpers.
- **Commits:** single-purpose, each CI-green. Branch `worktree-tier0-crypto-config`; push allowed, do NOT merge.
- **Style (Rob):** top-level imports, no meta comments, concise, no leaking internals in errors.
- Tests: `export DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5432/contex_test"`. No new `tests/` failures.

## The 6 API-key hash sites (must all use the shared helpers)
create/store: `src/core/auth.py:89`, `src/core/service_accounts.py:142` + `:327`, `main.py:109` (bootstrap).
verify/lookup: `src/core/identity.py:47`, `src/core/service_accounts.py:411`.

---

## Task 1: API-key hashing module (`src/core/keyhash.py`)

**Files:** Create `src/core/keyhash.py`; Test `tests/test_keyhash.py`.

**Interfaces — Produces:**
- `def hash_api_key(raw_key: str) -> str` — HMAC-SHA256(raw_key, salt).hexdigest() if a salt is configured, else `sha256(raw_key).hexdigest()`.
- `def candidate_hashes(raw_key: str) -> list[str]` — `[hmac_hash, sha_hash]` when a salt is set (dual-verify), else `[sha_hash]`.
- salt source: read `API_KEY_SALT` via the existing config (`from src.core.config import get_config` / `config.security.api_key_salt`) or `os.getenv("API_KEY_SALT")` — match how config is accessed elsewhere; read at call time (so tests can set it).

- [ ] **Step 1: failing tests** (`tests/test_keyhash.py`), monkeypatching the salt source:
```python
import hashlib, hmac
from src.core import keyhash

def test_no_salt_is_plain_sha(monkeypatch):
    monkeypatch.setattr(keyhash, "_get_salt", lambda: None)
    raw = "ck_abc"
    assert keyhash.hash_api_key(raw) == hashlib.sha256(raw.encode()).hexdigest()
    assert keyhash.candidate_hashes(raw) == [hashlib.sha256(raw.encode()).hexdigest()]

def test_salt_uses_hmac_and_dual_verify(monkeypatch):
    monkeypatch.setattr(keyhash, "_get_salt", lambda: "pepper")
    raw = "ck_abc"
    hm = hmac.new(b"pepper", raw.encode(), hashlib.sha256).hexdigest()
    sha = hashlib.sha256(raw.encode()).hexdigest()
    assert keyhash.hash_api_key(raw) == hm
    assert keyhash.candidate_hashes(raw) == [hm, sha]   # peppered first, legacy fallback
```
- [ ] **Step 2:** run → fail (module missing).
- [ ] **Step 3: implement** `src/core/keyhash.py` with `_get_salt()` (reads config/env), `hash_api_key`, `candidate_hashes` per the interfaces. Top-level imports (`hashlib`, `hmac`).
- [ ] **Step 4:** `python -m pytest tests/test_keyhash.py -v` → pass.
- [ ] **Step 5: commit** `feat(auth): API-key hashing module — HMAC pepper + dual-verify`.

---

## Task 2: Route all 6 hash sites through the module (#69)

**Files:** Modify `src/core/auth.py`, `src/core/identity.py`, `src/core/service_accounts.py`, `main.py`. Test: extend `tests/test_auth.py` / `tests/test_identity.py`.

**Interfaces — Consumes:** `hash_api_key`, `candidate_hashes` (Task 1).

- **Create/store sites** → `key_hash = hash_api_key(raw_key)`: `auth.py:89`, `service_accounts.py:142` and `:327`, `main.py:109`.
- **Verify/lookup sites** → look up by ANY candidate: replace `where(Model.key_hash == computed)` with `where(Model.key_hash.in_(candidate_hashes(raw_key)))`: `identity.py:47` (APIKey) and `service_accounts.py:411` (ServiceAccountKey). Import `select`/`in_` as needed (top-level).

- [ ] **Step 1: failing test** — with a salt set (monkeypatch), a key created via `create_api_key` is found by `resolve_identity` (peppered round-trip); AND a key whose `key_hash` was stored as legacy plain-SHA (insert one directly) is STILL found by `resolve_identity` when a salt is set (dual-verify). With no salt, unchanged.
- [ ] **Step 2:** run → fail.
- [ ] **Step 3:** update the 6 sites. Verify no remaining `hashlib.sha256(` for API keys: `grep -rn "sha256" src/core/auth.py src/core/identity.py src/core/service_accounts.py main.py` should only show the module (or none in those sites).
- [ ] **Step 4:** `python -m pytest tests/test_auth.py tests/test_identity.py tests/test_security.py -v` (service-account key tests too if present).
- [ ] **Step 5: commit** `fix(auth): salt API-key hashes via shared helper with legacy dual-verify (#69)`.

---

## Task 3: Persistent, fail-closed JWT secret (#68)

**Files:** `src/core/service_accounts.py` (+ `src/core/config.py` if adding a field). Test: `tests/test_service_accounts.py` (or wherever SA tests live) / a focused test.

- [ ] **Step 1:** stop freezing the secret at import (`JWT_SECRET = os.getenv(..., secrets.token_urlsafe(32))` at module top). Instead a `_jwt_secret()` helper read at issue/verify time: returns `os.getenv("SERVICE_ACCOUNT_JWT_SECRET")` (or `config.security` field); if set → use it. If unset: when `auth_enabled()` (from `src.core.authz`) → raise a clear `RuntimeError` (fail-closed — "SERVICE_ACCOUNT_JWT_SECRET must be set when AUTH_ENABLED"); when auth off → fall back to a process-random secret (current demo behavior) with a one-time warning.
- [ ] **Step 2:** `issue_token`/`validate_token` call `_jwt_secret()` instead of the module global.
- [ ] **Step 3: tests** — with `SERVICE_ACCOUNT_JWT_SECRET` set, a token issued survives a simulated "new secret read" (i.e., issue+validate use the same env secret, and two separate `_jwt_secret()` reads are equal → tokens survive restart). With auth-on + unset → issuing/validating raises (fail-closed). Auth-off + unset → still works (random). Run `python -m pytest tests/test_service_accounts.py -v` (or the SA test file; if none, add one).
- [ ] **Step 4: commit** `fix(auth): persistent env-sourced JWT secret, fail-closed when auth on (#68)`.

---

## Task 4: CORS — no credentials under a wildcard origin (#64)

**Files:** `main.py` (CORS setup ~301-316); Test: `tests/test_security.py`.

- [ ] **Step 1:** in the CORS block, after computing `CORS_ORIGINS`/`CORS_ALLOW_CREDENTIALS`: `if "*" in CORS_ORIGINS and CORS_ALLOW_CREDENTIALS: CORS_ALLOW_CREDENTIALS = False; logger.warning("CORS: credentials disabled because a wildcard origin is configured; set explicit CORS_ORIGINS to use credentials")`. Pass the coerced value to `CORSMiddleware`. (Keep the existing informational warnings.)
- [ ] **Step 2: tests** — with `CORS_ORIGINS` unset/`*`, the app is constructed with `allow_credentials=False` (assert via the CORS middleware options or a small helper); with explicit origins, credentials pass through. Update the existing `test_security.py` CORS tests that asserted the old insecure default.
- [ ] **Step 3:** `python -m pytest tests/test_security.py -v`; `python -c "import main"`.
- [ ] **Step 4: commit** `fix(cors): disable credentials under wildcard origin (#64)`.

---

## Task 5: Integration test — hardened crypto/config end-to-end

**Files:** `tests/test_tier0_crypto_config.py`.

- [ ] With `API_KEY_SALT` set: create a key → resolve it (peppered); a directly-inserted legacy plain-SHA key still resolves (dual-verify). With `SERVICE_ACCOUNT_JWT_SECRET` set: issue → validate a token across two independent `_jwt_secret()` reads. Assert `hash_api_key` output differs from plain SHA when salted. Commit `test(auth): tier0 crypto/config hardening integration`.

---

## Self-Review
- #64 → Task 4; #68 → Task 3; #69 → Tasks 1+2. Integration → Task 5.
- Non-breaking: dual-verify (Task 2) means no migration/re-issue; no-salt path == current behavior; auth-off JWT/CORS unchanged. Verified in Task 2/3/4 tests.
- All 6 hash sites enumerated (Task 2) — a missed site = keys created one way, verified another = auth breakage; the grep in Task 2 Step 3 guards it.
- Consistency: `hash_api_key`/`candidate_hashes`/`_jwt_secret()` used uniformly; config/env read at call time so tests can set them.
- Fail-closed additions (JWT unset when auth-on) must NOT trip in the test suite (auth-off by default) or in `import main` — Task 3 reads the secret lazily at token time, not import, so `import main` is safe.
