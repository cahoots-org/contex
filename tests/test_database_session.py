import anyio
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
@pytest.mark.parametrize("sql, then_wait", [("SELECT pg_sleep(5)", 0), ("SELECT 1", 5)])
async def test_cancelled_request_returns_its_connection(db, sql, then_wait):
    async def slow_query():
        async with db.session() as session:
            await session.execute(text(sql))
            await anyio.sleep(then_wait)

    with anyio.fail_after(3):
        async with anyio.create_task_group() as tg:
            tg.start_soon(slow_query)
            await anyio.sleep(0.2)
            tg.cancel_scope.cancel()

    assert db.engine.pool.checkedout() == 0
