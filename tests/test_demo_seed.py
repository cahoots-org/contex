import pytest

from src.core.context_engine import ContextEngine
from src.web.demo_seed import DEMO_PROJECT_ID, ensure_demo_seed


@pytest.mark.asyncio
async def test_seed_is_idempotent(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()

    await ensure_demo_seed(engine)
    keys1 = await engine.semantic_matcher.get_registered_data(DEMO_PROJECT_ID)
    assert set(keys1) >= {"db_config", "api_config", "cache_config"}

    # Second call must not raise and must not duplicate keys.
    await ensure_demo_seed(engine)
    keys2 = await engine.semantic_matcher.get_registered_data(DEMO_PROJECT_ID)
    assert sorted(keys2) == sorted(keys1)
