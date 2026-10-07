import json
from unittest.mock import MagicMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import func, select

from src.core import mcp_adapter
from src.core.context_engine import ContextEngine
from src.core.db_models import Embedding
from src.core.limits import MAX_BATCH_SIZE
from src.core.mcp_adapter import build_mcp_server


async def _server_with_doc(db):
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)
    await server.call_tool("contex_publish", {
        "project_id": "p", "data_key": "cfg", "data": {"purpose": "database connection settings"},
    })
    return server


async def _rows(db, data_key):
    async with db.session() as session:
        return await session.scalar(
            select(func.count()).select_from(Embedding).where(Embedding.data_key == data_key)
        )


@pytest.mark.asyncio
async def test_delete_tool_reports_deleted_and_missing(db):
    server = await _server_with_doc(db)

    res = json.loads((await server.call_tool("contex_delete", {
        "project_id": "p", "data_keys": ["cfg", "nope"],
    })).content[0].text)

    assert res == {"deleted": ["cfg"], "missing": ["nope"]}
    assert await _rows(db, "cfg") == 0


@pytest.mark.asyncio
async def test_delete_tool_requires_publish_scope_on_the_project(db, monkeypatch):
    server = await _server_with_doc(db)
    token = MagicMock()
    token.scopes = ["publish_data"]
    token.claims = {"tenant_id": None, "projects": ["other"]}
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(mcp_adapter, "get_access_token", lambda: token)

    with pytest.raises(ToolError) as denied:
        await server.call_tool("contex_delete", {"project_id": "p", "data_keys": ["cfg"]})
    assert isinstance(denied.value.__cause__, PermissionError)
    assert await _rows(db, "cfg") > 0


@pytest.mark.asyncio
async def test_delete_tool_caps_batch_size(db):
    server = await _server_with_doc(db)
    with pytest.raises(ToolError) as rejected:
        await server.call_tool("contex_delete", {
            "project_id": "p", "data_keys": [f"k{i}" for i in range(MAX_BATCH_SIZE + 1)],
        })
    assert "Batch too large" in str(rejected.value.__cause__)
