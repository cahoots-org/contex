"""Web UI routes for Contex Query Sandbox"""

import json
import asyncio
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import toon_format as toon
from src.core.models import AgentRegistration

router = APIRouter()

# Setup templates
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


@router.get("/", response_class=HTMLResponse)
async def sandbox_home(request: Request):
    """Query sandbox home page"""
    engine = request.app.state.context_engine

    # Get all available projects from Redis
    projects = set()
    try:
        from redis.commands.search.query import Query
        q = Query("*").return_fields("project_id").paging(0, 1000)
        results = await engine.semantic_matcher.redis.ft(
            engine.semantic_matcher.INDEX_NAME
        ).search(q)

        for doc in results.docs:
            projects.add(doc.project_id)
    except:
        # Index might not exist yet
        pass

    return templates.TemplateResponse(
        "sandbox.html",
        {
            "request": request,
            "projects": sorted(list(projects)),
        }
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
        # Execute query
        matches = await engine.query_project_data(
            project_id=project_id,
            query=query,
            top_k=top_k
        )

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


@router.get("/subscribe")
async def subscribe_to_updates(
    request: Request,
    project_id: str = Query(...),
    data_needs: str = Query(...),
    session_id: str = Query(...)
):
    """
    Subscribe to real-time project data updates via Server-Sent Events (SSE).
    Registers the sandbox session as a temporary agent.
    """
    engine = request.app.state.context_engine

    # Parse data needs
    needs = json.loads(data_needs)

    async def event_stream():
        # Register as agent with webhook notification (we'll intercept via Redis)
        registration = AgentRegistration(
            agent_id=session_id,
            project_id=project_id,
            data_needs=needs,
            notification_method="redis",
            response_format="json"
        )

        try:
            # Register the sandbox session as an agent
            response = await engine.register_agent(registration)

            # Send initial context
            initial_data = {
                "type": "initial_context",
                "message": f"Subscribed to {project_id}",
                "matched_needs": response.matched_needs
            }
            yield f"data: {json.dumps(initial_data)}\n\n"

            # Subscribe to Redis pub/sub for updates
            channel = f"agent:{session_id}:updates"
            pubsub = engine.redis.pubsub()
            await pubsub.subscribe(channel)

            # Stream updates
            try:
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        data = json.loads(message["data"])

                        # Transform for UI display
                        if data["type"] == "data_update":
                            ui_update = {
                                "type": "data_update",
                                "data_key": data.get("data_key"),
                                "sequence": data.get("sequence"),
                                "data": data.get("data")
                            }
                        elif data["type"] == "initial_context":
                            # Initial context with matched data
                            ui_update = {
                                "type": "initial_context",
                                "context": data.get("context")
                            }
                        else:
                            ui_update = data

                        yield f"data: {json.dumps(ui_update)}\n\n"

                        # Flush to ensure immediate delivery
                        await asyncio.sleep(0)

            finally:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()

        except Exception as e:
            error_data = {
                "type": "error",
                "message": str(e)
            }
            yield f"data: {json.dumps(error_data)}\n\n"

        finally:
            # Cleanup: unregister agent
            try:
                await engine.unregister_agent(session_id)
            except:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )
