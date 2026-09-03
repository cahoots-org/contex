"""Web UI routes for Contex Query Sandbox"""

import json
import asyncio
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import toon_format as toon
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from src.web.live import stream_subscription_updates
from src.core.logging import get_logger
from src.core.db_models import Embedding

router = APIRouter()

logger = get_logger(__name__)

# Setup templates
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


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
    max_tokens: int = Form(51200)
):
    """Execute a semantic query and return results"""
    engine = request.app.state.context_engine

    # Override the semantic matcher's threshold temporarily
    original_threshold = engine.semantic_matcher.threshold
    engine.semantic_matcher.threshold = threshold

    try:
        # Execute query (get more candidates for threshold filtering)
        matches = await engine.query_project_data(
            project_id=project_id,
            query=query,
            top_k=top_k * 3
        )

        # Filter by threshold (scores are normalized to 0-1 range)
        matches = [m for m in matches if m["similarity"] >= threshold]

        # Limit to top_k after filtering
        matches = matches[:top_k]

        # Apply token limit truncation if specified
        if max_tokens and matches:
            matches_dict = {query: matches}
            truncated = engine._truncate_matches(matches_dict, max_tokens)
            matches = truncated.get(query, [])

        # Calculate token counts
        import tiktoken
        try:
            enc = tiktoken.get_encoding("cl100k_base")
        except:
            enc = None

        # Enhance matches with metadata
        enhanced_matches = []
        total_tokens = 0

        for match in matches:
            # Generate both JSON and TOON formats
            data_json = json.dumps(match["data"], indent=2)
            try:
                data_toon = toon.encode(match["data"])
            except NotImplementedError:
                # TOON encoder not yet available, use JSON as fallback
                data_toon = data_json

            # Calculate token counts for both formats
            json_tokens = 0
            toon_tokens = 0
            if enc:
                try:
                    json_tokens = len(enc.encode(data_json))
                    toon_tokens = len(enc.encode(data_toon))
                    total_tokens += toon_tokens  # Use TOON tokens for total
                except:
                    pass

            # Calculate token savings
            token_savings = 0
            savings_percent = 0
            if json_tokens > 0 and toon_tokens > 0:
                token_savings = json_tokens - toon_tokens
                savings_percent = round((token_savings / json_tokens) * 100, 1)

            # No preview truncation - will be handled by CSS scrolling
            preview = data_toon

            enhanced_matches.append({
                "data_key": match["data_key"],
                "similarity": match["similarity"],
                "similarity_percent": round(match["similarity"] * 100, 1),
                "data": match["data"],
                "data_json": data_json,
                "data_toon": data_toon,
                "description": match.get("description", ""),
                "token_count": toon_tokens,
                "json_tokens": json_tokens,
                "toon_tokens": toon_tokens,
                "token_savings": token_savings,
                "savings_percent": savings_percent,
                "preview": preview
            })

        return templates.TemplateResponse(
            "query_results.html",
            {
                "request": request,
                "query": query,
                "project_id": project_id,
                "matches": enhanced_matches,
                "total_matches": len(enhanced_matches),
                "total_tokens": total_tokens,
                "threshold": threshold,
                "top_k": top_k
            }
        )
    finally:
        # Restore original threshold
        engine.semantic_matcher.threshold = original_threshold


@router.get("/projects/{project_id}/stats", response_class=HTMLResponse)
async def project_stats(request: Request, project_id: str):
    """Get statistics about a project's data"""
    engine = request.app.state.context_engine

    # Get all data for this project
    data_keys = await engine.semantic_matcher.get_registered_data(project_id)

    # Calculate stats
    import tiktoken
    try:
        enc = tiktoken.get_encoding("cl100k_base")
    except:
        enc = None

    total_tokens = 0
    data_items = []

    # Fetch data from Redis for each key
    for key in data_keys:
        redis_key = f"{engine.semantic_matcher.KEY_PREFIX}{project_id}:{key}"
        data_info = await engine.semantic_matcher.redis.hgetall(redis_key)

        if data_info:
            # Decode bytes if needed
            description = data_info.get(b"description") or data_info.get("description", "")
            if isinstance(description, bytes):
                description = description.decode()

            data_str = data_info.get(b"data") or data_info.get("data", "{}")
            if isinstance(data_str, bytes):
                data_str = data_str.decode()

            # Calculate token count
            token_count = 0
            if enc:
                try:
                    token_count = len(enc.encode(data_str))
                    total_tokens += token_count
                except:
                    pass

            data_items.append({
                "data_key": key,
                "description": description,
                "token_count": token_count
            })

    return templates.TemplateResponse(
        "project_stats.html",
        {
            "request": request,
            "project_id": project_id,
            "data_count": len(data_items),
            "total_tokens": total_tokens,
            "data_items": sorted(data_items, key=lambda x: x["token_count"], reverse=True)
        }
    )


@router.get("/projects/{project_id}/data")
async def get_project_data(request: Request, project_id: str):
    """Get all data for a project (JSON endpoint for sandbox UI)"""
    engine = request.app.state.context_engine

    # One representative row per data_key, straight from the embeddings store.
    # DISTINCT ON collapses the per-node rows back to a single item per key.
    async with engine.semantic_matcher.db.session() as session:
        result = await session.execute(
            select(
                Embedding.data_key,
                Embedding.data,
                Embedding.data_original,
                Embedding.data_format,
            )
            .where(Embedding.project_id == project_id)
            .distinct(Embedding.data_key)
            .order_by(Embedding.data_key, Embedding.id)
        )
        rows = result.all()

    data_items = []
    for data_key, data, data_original, data_format in rows:
        # Prefer the full original payload (pre node-splitting); fall back to the JSONB node data.
        data_obj = data
        if data_original:
            try:
                data_obj = json.loads(data_original)
            except (json.JSONDecodeError, ValueError):
                data_obj = data_original

        data_items.append({
            "data_key": data_key,
            "description": f"{data_key} ({data_format or 'unknown'})",
            "data": data_obj,
        })

    return {"data": data_items}


@router.get("/subscribe")
async def subscribe_to_updates(
    request: Request,
    project_id: str = Query(...),
    need: str = Query(...),
):
    """Stream a natural-language need as a live-updating context bundle over SSE.

    Backed by an ephemeral Subscription; the browser is a parallel consumer of the
    same reconcile pipeline the MCP bridge uses.
    """
    engine = request.app.state.context_engine
    return StreamingResponse(
        stream_subscription_updates(engine, project_id, need),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


