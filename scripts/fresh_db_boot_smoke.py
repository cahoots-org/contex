#!/usr/bin/env python3
"""Fresh-DB boot smoke test (CI gate for issue #119).

Run against an EMPTY Postgres (pgvector) + Redis. Asserts, in order:

1. ``alembic upgrade head`` brought the DB to the alembic head revision
   (the ``alembic_version`` row matches ``alembic heads``).
2. The app boots (its ``main.py`` lifespan calls ``db.migrate_to_head()``).
3. ``GET /health`` returns a healthy status.
4. ONE end-to-end publish -> query round-trip succeeds.

This catches fresh-install / migration / boot regressions that unit tests miss.
Exit code 0 on success, non-zero (with a printed reason) on any failure.

Expects ``DATABASE_URL`` (postgresql+asyncpg://...) and ``REDIS_URL`` in the env.
"""

import os
import subprocess
import sys
import time

import httpx


def _fail(msg: str) -> "None":
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def assert_alembic_at_head() -> None:
    """Assert the DB is stamped at the alembic head revision."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    heads = set(script.get_heads())
    print(f"alembic script heads: {sorted(heads)}")

    # Read the applied revision(s) from the DB's alembic_version table. Use a
    # sync psycopg-free path via asyncpg through SQLAlchemy's async engine.
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _read_db_heads() -> set:
        url = os.environ["DATABASE_URL"]
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT version_num FROM alembic_version")
                )
                return {row[0] for row in result.fetchall()}
        finally:
            await engine.dispose()

    db_heads = asyncio.run(_read_db_heads())
    print(f"alembic_version in DB: {sorted(db_heads)}")

    if not db_heads:
        _fail("alembic_version table is empty - migrations did not run")
    if db_heads != heads:
        _fail(f"DB revision {sorted(db_heads)} != script head {sorted(heads)}")
    print("OK: database is at alembic head")


def wait_for_health(base_url: str, proc: subprocess.Popen, timeout: float = 90.0) -> None:
    """Poll GET /health until healthy or timeout; fail if the app died."""
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        if proc.poll() is not None:
            _fail(f"app process exited early with code {proc.returncode}")
        try:
            resp = httpx.get(f"{base_url}/health", timeout=5.0)
            if resp.status_code == 200 and resp.json().get("status") == "healthy":
                print(f"OK: GET /health -> {resp.json()}")
                return
            last_err = f"status={resp.status_code} body={resp.text[:200]}"
        except Exception as e:  # noqa: BLE001 - transient during boot
            last_err = str(e)
        time.sleep(1.0)
    _fail(f"/health never became healthy: {last_err}")


def round_trip(base_url: str) -> None:
    """One end-to-end publish -> query round-trip."""
    api = f"{base_url}/api/v1"
    project_id = "ci-smoke-proj"

    pub = httpx.post(
        f"{api}/data/publish",
        json={
            "project_id": project_id,
            "data_key": "auth_method",
            "data": {
                "authentication": "OAuth2 with JWT bearer tokens",
                "provider": "Auth0",
            },
        },
        timeout=30.0,
    )
    if pub.status_code != 200:
        _fail(f"publish returned {pub.status_code}: {pub.text[:300]}")
    print(f"OK: published data -> {pub.json()}")

    # Query it back (JSON format for easy assertion). Retry briefly to allow
    # for any eventual-consistency in indexing.
    deadline = time.time() + 30.0
    last = None
    while time.time() < deadline:
        q = httpx.post(
            f"{api}/projects/{project_id}/query",
            json={
                "query": "what authentication method do we use",
                "top_k": 3,
                # Low threshold so the round-trip is robust to embedding-model
                # scoring; we only need to prove data flows publish -> query.
                "threshold": 0.0,
                "response_format": "json",
            },
            timeout=30.0,
        )
        if q.status_code != 200:
            _fail(f"query returned {q.status_code}: {q.text[:300]}")
        body = q.json()
        last = body
        if body.get("total_matches", 0) >= 1 and body.get("matches"):
            print(f"OK: query round-trip returned {body['total_matches']} match(es)")
            return
        time.sleep(1.0)
    _fail(f"query round-trip returned no matches: {last}")


def main() -> None:
    if "DATABASE_URL" not in os.environ:
        _fail("DATABASE_URL not set")

    # 1) Migrate the empty DB and assert head is reached.
    print("== Running alembic upgrade head ==")
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    assert_alembic_at_head()

    # 2) Boot the app (lifespan re-runs migrate_to_head; idempotent).
    host = "127.0.0.1"
    port = "8123"
    base_url = f"http://{host}:{port}"
    env = dict(os.environ)
    env.setdefault("AUTH_ENABLED", "false")
    env.setdefault("LOG_JSON", "false")
    env.setdefault("WEBHOOKS_ENABLED", "false")

    print("== Booting app ==")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", host, "--port", port],
        env=env,
    )
    try:
        # 3) /health healthy.
        wait_for_health(base_url, proc)
        # 4) publish -> query round-trip.
        round_trip(base_url)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("\nfresh-DB boot smoke test PASSED")


if __name__ == "__main__":
    main()
