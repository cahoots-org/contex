"""Web UI routes for Contex Query Sandbox"""

import json
from fastapi import APIRouter, Depends, HTTPException, Request, Form, Query
from src.core.authz import auth_enabled
from src.core.rate_limiter import RateLimiter
from src.core.version import VERSION
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.exc import SQLAlchemyError
from src.web.live import stream_subscription_updates
from src.core.logging import get_logger
from src.core.db_models import Embedding


async def _auth_off_only() -> None:
    """The sandbox has no way to send an API key, so it is served only with auth off."""
    if auth_enabled():
        raise HTTPException(status_code=404)


router = APIRouter(dependencies=[Depends(_auth_off_only)])

logger = get_logger(__name__)


async def _documents(engine, project_id: str) -> list:
    """Each document as (data_key, data_format, its nodes' data in order)."""
    async with engine.semantic_matcher.db.session() as session:
        result = await session.execute(
            select(
                Embedding.data_key,
                func.min(Embedding.data_format),
                func.jsonb_agg(aggregate_order_by(Embedding.data, Embedding.id)),
            )
            .where(Embedding.project_id == project_id)
            .group_by(Embedding.data_key)
            .order_by(Embedding.data_key)
        )
        return result.all()


_limiter = RateLimiter()


def _throttle_query() -> None:
    """Apply RATE_LIMIT_QUERY, as the MCP query tools do; these routes embed and search too."""
    wait = _limiter.retry_after("sandbox:query", "RATE_LIMIT_QUERY", 120)
    if wait is not None:
        raise HTTPException(status_code=429, detail="Too many requests", headers={"Retry-After": str(wait)})


# Setup templates
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))
templates.env.globals["version"] = VERSION


@router.get("/", response_class=HTMLResponse)
async def sandbox_home(request: Request):
    """Query sandbox home page"""
    engine = request.app.state.context_engine

    # Get all available projects from the embeddings store (Postgres/pgvector),
    # which is where published data actually lands.
    projects = set()
    try:
        async with engine.semantic_matcher.db.session() as session:
            result = await session.execute(
                select(Embedding.project_id).distinct()
            )
            projects.update(row[0] for row in result.all())
    except SQLAlchemyError as e:
        # Non-critical: this only populates the project dropdown. If the store
        # is briefly unreachable (or the table doesn't exist yet on a fresh DB),
        # render the page with an empty list instead of 500ing. Unexpected
        # (non-DB) errors propagate so real bugs surface rather than hide here.
        logger.warning("Failed to list projects for sandbox", error=str(e))

    return templates.TemplateResponse(
        request,
        "sandbox.html",
        {
            "projects": sorted(list(projects)),
        },
    )


@router.post("/query", response_class=HTMLResponse)
async def execute_query(
    request: Request,
    project_id: str = Form(...),
    query: str = Form(...),
    top_k: int = Form(10),
    threshold: float = Form(0.5),
):
    """Execute a semantic query and return results"""
    _throttle_query()
    engine = request.app.state.context_engine

    # Pass the per-request threshold through instead of mutating the shared
    # matcher singleton, so concurrent /query requests stay isolated (#105).
    matches = await engine.query_project_data(
        project_id=project_id,
        query=query,
        top_k=top_k,
        threshold=threshold,
    )

    # Defensive cap (the matcher already limits to top_k)
    matches = matches[:top_k]

    # Enhance matches with metadata
    enhanced_matches = []
    for match in matches:
        data_json = json.dumps(match["data"], indent=2)
        enhanced_matches.append({
            "data_key": match["data_key"],
            "similarity": match["similarity"],
            "similarity_percent": round(match["similarity"] * 100, 1),
            "data": match["data"],
            "data_json": data_json,
            "description": match.get("description", ""),
        })

    return templates.TemplateResponse(
        request,
        "query_results.html",
        {
            "query": query,
            "matches": enhanced_matches,
            "total_matches": len(enhanced_matches),
            "threshold": threshold,
            "top_k": top_k
        }
    )


@router.get("/data")
async def get_project_data(
    request: Request,
    project_id: str = Query(...),
):
    """Get all data for a project (JSON endpoint for sandbox UI)"""
    data_items = [
        {
            "data_key": data_key,
            "description": f"{data_key} ({data_format or 'unknown'})",
            "data": nodes[0] if len(nodes) == 1 else nodes,
        }
        for data_key, data_format, nodes in await _documents(request.app.state.context_engine, project_id)
    ]

    return {"data": data_items}


@router.get("/subscribe")
async def subscribe_to_updates(
    request: Request,
    project_id: str = Query(...),
    need: str = Query(...),
    top_k: int = Query(10),
    threshold: float = Query(0.5),
):
    """Stream a natural-language need as a live-updating context bundle over SSE.

    Backed by an ephemeral Subscription; the browser is a parallel consumer of the
    same reconcile pipeline the MCP bridge uses.
    """
    _throttle_query()
    engine = request.app.state.context_engine
    return StreamingResponse(
        stream_subscription_updates(engine, project_id, need, top_k=top_k, threshold=threshold),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


