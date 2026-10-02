"""register_data writes per-node defs/refs to the symbols table for code.

The symbols table is the index behind cross-file linking: every def/ref the code
parser records becomes a row at ingest. Rows are rewritten per source on every
re-ingest (delete-by-(project, data_key) then insert) so stale names don't
linger, and non-code formats write nothing.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import Mock, patch

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import select

from src.core.db_models import Embedding, Symbol
from src.core.semantic_matcher import SemanticDataMatcher

PY = '''\
from mypkg.helpers import load_config, Thing

def top_level(a):
    load_config(a)

class Service:
    def run(self):
        return top_level(self.x)
'''

PY_V2 = '''\
def only_this():
    return 1
'''


@pytest_asyncio.fixture
async def matcher(db):
    with patch("src.core.semantic_matcher.OnnxEmbedder") as mock_cls:
        model = Mock()
        model.get_sentence_embedding_dimension.return_value = 768
        model.encode.side_effect = lambda x, *a, **k: (
            np.array([0.1] * 768, dtype=np.float32)
            if isinstance(x, str)
            else np.array([[0.1] * 768] * len(x), dtype=np.float32)
        )
        mock_cls.return_value = model
        yield SemanticDataMatcher(db=db)


async def _symbols(db, project_id):
    async with db.session() as session:
        rows = (await session.execute(
            select(Symbol).where(Symbol.project_id == project_id)
        )).scalars().all()
    return {(r.name, r.role) for r in rows}


@pytest.mark.asyncio
async def test_code_ingest_writes_defs_and_refs(matcher, db):
    await matcher.register_data("p", "repo:app.py", PY, "code")
    syms = await _symbols(db, "p")
    # defs: every function/class/method the parser found
    assert ("top_level", "def") in syms
    assert ("Service", "def") in syms
    assert ("run", "def") in syms
    # refs: call targets + imported symbols
    assert ("load_config", "ref") in syms
    assert ("top_level", "ref") in syms
    assert ("Thing", "ref") in syms


@pytest.mark.asyncio
async def test_reingest_replaces_symbols(matcher, db):
    await matcher.register_data("p", "repo:app.py", PY, "code")
    await matcher.register_data("p", "repo:app.py", PY_V2, "code")
    syms = await _symbols(db, "p")
    assert ("only_this", "def") in syms
    # old names from v1 are gone
    assert ("top_level", "def") not in syms
    assert ("load_config", "ref") not in syms


@pytest.mark.asyncio
async def test_non_code_writes_no_symbols(matcher, db):
    await matcher.register_data("p", "config", {"backend": "FastAPI"}, "json")
    assert await _symbols(db, "p") == set()


@pytest.mark.asyncio
async def test_long_keys_store(matcher, db):
    # Deep paths under long file paths overflowed varchar(255) node/data keys.
    long_dir = "/".join(["snapshots"] * 30)
    await matcher.register_data("p_long", f"repo:{long_dir}/app.py", PY_V2, "code")
    await matcher.register_data(
        "p_long", f"repo:{long_dir}/stack.json",
        {"Resources": {"Sg" * 40: {"Properties": {"Egress": [{"Cidr": "0.0.0.0/0"}]}}}},
        "json",
    )
    async with db.session() as session:
        keys = (await session.execute(
            select(Embedding.node_key).where(Embedding.project_id == "p_long")
        )).scalars().all()
    assert max(len(k) for k in keys) > 255
    assert ("only_this", "def") in await _symbols(db, "p_long")
