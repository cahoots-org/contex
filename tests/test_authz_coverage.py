import pytest
from fastapi import Depends, FastAPI

from src.core.authz import require, public
from src.core.authz_coverage import find_uncovered_routes, assert_authz_coverage
from src.core.rbac import Permission


def test_covered_app_passes():
    app = FastAPI()

    @app.get("/a", dependencies=[Depends(require(Permission.QUERY_DATA))])
    async def a(): ...

    @app.get("/b", dependencies=[Depends(public)])
    async def b(): ...

    assert find_uncovered_routes(app) == []
    assert_authz_coverage(app)  # no raise


def test_uncovered_route_is_flagged():
    app = FastAPI()

    @app.get("/naked")
    async def naked(): ...

    uncovered = find_uncovered_routes(app)
    assert any("/naked" in u for u in uncovered)
    with pytest.raises(RuntimeError, match="/naked"):
        assert_authz_coverage(app)


def test_per_method_granularity():
    app = FastAPI()

    @app.get("/x", dependencies=[Depends(public)])
    async def x_get(): ...

    @app.post("/x")  # POST has no marker → must be flagged even though GET is covered
    async def x_post(): ...

    uncovered = find_uncovered_routes(app)
    assert any("POST" in u and "/x" in u for u in uncovered)


def test_unknown_mount_is_flagged():
    from starlette.applications import Starlette
    app = FastAPI()
    app.mount("/rogue", Starlette())
    with pytest.raises(RuntimeError, match="/rogue"):
        assert_authz_coverage(app)
