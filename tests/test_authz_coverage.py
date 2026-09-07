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


def test_nested_marked_dependency_is_covered():
    """The marker may live below the top-level route dependency; the recursive
    descent through dep.dependencies must still find it."""
    app = FastAPI()

    # A route dependency that itself Depends on a marked dependency — the marker
    # is nested one level down, not a direct top-level dependency of the route.
    def marked_parent(_=Depends(require(Permission.QUERY_DATA))):
        return None

    @app.get("/nested-marked", dependencies=[Depends(marked_parent)])
    async def nested_marked(): ...

    assert find_uncovered_routes(app) == []
    assert_authz_coverage(app)  # no raise


def test_nested_unmarked_dependency_is_flagged():
    """Inverse of the above: a nested dependency tree with NO marker anywhere
    must still be flagged — guards against a false-negative in the recursion."""
    app = FastAPI()

    def plain_child():
        return None

    def plain_parent(_=Depends(plain_child)):
        return None

    @app.get("/nested-unmarked", dependencies=[Depends(plain_parent)])
    async def nested_unmarked(): ...

    uncovered = find_uncovered_routes(app)
    assert any("/nested-unmarked" in u for u in uncovered)
    with pytest.raises(RuntimeError, match="/nested-unmarked"):
        assert_authz_coverage(app)


def test_unknown_mount_is_flagged():
    from starlette.applications import Starlette
    app = FastAPI()
    app.mount("/rogue", Starlette())
    with pytest.raises(RuntimeError, match="/rogue"):
        assert_authz_coverage(app)


def test_real_app_is_fully_covered():
    from main import app
    assert find_uncovered_routes(app) == []
