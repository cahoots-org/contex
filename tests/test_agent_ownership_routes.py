"""Route-level tests for agent-registration ownership (issue #59).

The register routes record the caller's API-key id as the agent owner and
reject re-registration by any other caller with a 403. Batch registration
enforces the same check per item, and rejected hijack attempts are audited.
"""

import httpx
import numpy as np
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import AsyncClient

from src.api.routes import router as api_router
from src.core.authz import get_identity
from src.core.context_engine import ContextEngine
from src.core.identity import Identity
from src.core.rbac import Permission


def _identity():
    return Identity(
        key_id="k",
        scopes=frozenset(Permission),
        tenant_id="tenant-a",
        projects=(),
        role=None,
    )


@pytest_asyncio.fixture
async def app(db, redis):
    """FastAPI app with a real DB-backed engine and a settable caller identity."""
    with patch("src.core.semantic_matcher.SentenceTransformer") as mock_model_cls:
        mock_model = Mock()
        mock_model.encode.side_effect = lambda x, *a, **k: (
            np.array([0.1] * 384, dtype=np.float32)
            if isinstance(x, str)
            else np.array([[0.1] * 384] * len(x), dtype=np.float32)
        )
        mock_model_cls.return_value = mock_model

        engine = ContextEngine(db=db, redis=redis)
        await engine.semantic_matcher.initialize_index()

        application = FastAPI()

        @application.exception_handler(PermissionError)
        async def _denied(request, exc):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})

        application.state.caller = {"api_key_id": "key-A"}

        @application.middleware("http")
        async def _set_actor(request, call_next):
            request.state.api_key_id = application.state.caller["api_key_id"]
            return await call_next(request)

        application.include_router(api_router, prefix="/api/v1")
        application.state.context_engine = engine
        application.state.db = db
        application.dependency_overrides[get_identity] = _identity

        yield application


async def _register(client, agent_id, url):
    return await client.post(
        "/api/v1/agents/register",
        json={
            "agent_id": agent_id,
            "project_id": "proj1",
            "data_needs": ["data"],
            "webhook_url": url,
        },
    )


@pytest.mark.asyncio
async def test_first_registration_records_owner(app):
    with (
        patch("src.api.routes.audit_log", new=AsyncMock()),
        patch("src.api.routes.emit_webhook", new=AsyncMock()),
    ):
        async with AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await _register(c, "agent1", "https://a.example.com/hook")

    assert resp.status_code == 200
    assert app.state.context_engine.agents["agent1"]["created_by"] == "key-A"


@pytest.mark.asyncio
async def test_owner_can_reregister(app):
    with (
        patch("src.api.routes.audit_log", new=AsyncMock()),
        patch("src.api.routes.emit_webhook", new=AsyncMock()),
    ):
        async with AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            await _register(c, "agent1", "https://a.example.com/hook")
            resp = await _register(c, "agent1", "https://a2.example.com/hook")

    assert resp.status_code == 200
    assert (
        app.state.context_engine.agents["agent1"]["webhook_url"]
        == "https://a2.example.com/hook"
    )


@pytest.mark.asyncio
async def test_hijack_is_rejected_and_audited(app):
    audit = AsyncMock()
    with (
        patch("src.api.routes.audit_log", new=audit),
        patch("src.api.routes.emit_webhook", new=AsyncMock()),
    ):
        async with AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            await _register(c, "agent1", "https://victim.example.com/hook")

            app.state.caller["api_key_id"] = "key-B"
            resp = await _register(c, "agent1", "https://attacker.example.com/hook")

    assert resp.status_code == 403
    # Victim webhook untouched.
    assert (
        app.state.context_engine.agents["agent1"]["webhook_url"]
        == "https://victim.example.com/hook"
    )
    # The rejected hijack was audit-logged.
    actions = [kw.get("action", "") for _, kw in audit.call_args_list]
    assert any("Rejected hijack" in a for a in actions)


@pytest.mark.asyncio
async def test_batch_registration_enforces_ownership_per_item(app):
    with (
        patch("src.api.routes.audit_log", new=AsyncMock()),
        patch("src.api.routes.emit_webhook", new=AsyncMock()),
    ):
        async with AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            # key-A owns owned1.
            await _register(c, "owned1", "https://a.example.com/hook")

            app.state.caller["api_key_id"] = "key-B"
            resp = await c.post(
                "/api/v1/batch/register",
                json=[
                    {
                        "agent_id": "owned1",
                        "project_id": "proj1",
                        "data_needs": ["data"],
                        "webhook_url": "https://attacker.example.com/hook",
                    },
                    {
                        "agent_id": "fresh1",
                        "project_id": "proj1",
                        "data_needs": ["data"],
                    },
                ],
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["successful"] == 1
    assert body["failed"] == 1
    by_agent = {r["agent_id"]: r for r in body["results"]}
    assert by_agent["owned1"]["status"] == "failed"
    assert by_agent["fresh1"]["status"] == "success"
    # Hijack did not overwrite the owner's webhook.
    assert (
        app.state.context_engine.agents["owned1"]["webhook_url"]
        == "https://a.example.com/hook"
    )
