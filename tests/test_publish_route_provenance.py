"""
Test: HTTP publish route stamps server-attested provenance (source/actor/tenant_id).

TDD: this test must FAIL before the route wiring (Task 4) and PASS after.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI, Request
from httpx import AsyncClient
from src.api.routes import router as api_router


@pytest.mark.asyncio
async def test_publish_route_stamps_provenance():
    """The HTTP publish route must forward server-attested source/actor/tenant_id to publish_data."""
    app = FastAPI()

    # Seed an authenticated principal via middleware (server-side, not request body)
    @app.middleware("http")
    async def _seed_principal(request: Request, call_next):
        request.state.api_key_id = "test-key"
        request.state.tenant_id = "t1"
        request.state.request_id = "req-1"
        return await call_next(request)

    app.include_router(api_router, prefix="/api/v1")

    # Spy on publish_data — returns a sequence number
    mock_engine = MagicMock()
    mock_engine.publish_data = AsyncMock(return_value="42")
    app.state.context_engine = mock_engine

    # Patch post-publish side effects so they don't error in a bare app.
    # The route does lazy `from src.core.metrics import ...` inside the function body,
    # so we patch at src.core.metrics (the canonical location of the objects).
    chainable_hist = _chainable_histogram()
    with (
        patch("src.api.routes.audit_log", new=AsyncMock()),
        patch("src.api.routes.emit_webhook", new=AsyncMock()),
        patch("src.core.metrics.record_event_published", new=MagicMock()),
        patch("src.core.metrics.publish_duration_seconds", new=chainable_hist),
    ):
        async with AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/data/publish",
                json={"project_id": "route-prov", "data_key": "k", "data": {"x": 1}},
            )

    # publish_data was called exactly once
    mock_engine.publish_data.assert_awaited_once()

    call_args = mock_engine.publish_data.call_args

    # Exact-value assertions on provenance kwargs
    assert call_args.kwargs["source"] == "api", (
        f"Expected source='api', got: {call_args.kwargs.get('source')!r}"
    )
    assert call_args.kwargs["tenant_id"] == "t1", (
        f"Expected tenant_id='t1', got: {call_args.kwargs.get('tenant_id')!r}"
    )
    actor = call_args.kwargs["actor"]
    assert actor["actor_id"] == "test-key", (
        f"Expected actor_id='test-key', got: {actor.get('actor_id')!r}"
    )
    assert actor["actor_type"] == "api_key", (
        f"Expected actor_type='api_key', got: {actor.get('actor_type')!r}"
    )
    assert "actor_ip" in actor, f"Expected 'actor_ip' key in actor dict, got: {actor!r}"

    # The route must have returned 200 (publish_data called first, so even if 200 is tricky,
    # the call_args assertions above already enforce wiring correctness)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


def _chainable_histogram():
    """Return a MagicMock whose .labels(...).observe(...) chain succeeds."""
    mock = MagicMock()
    mock.labels.return_value = MagicMock()
    mock.labels.return_value.observe = MagicMock()
    return mock
