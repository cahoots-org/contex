"""Context Engine v2 - FastAPI Application"""

import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from src.context_engine import ContextEngine
from src.models import (
    AgentRegistration,
    DataPublishEvent,
    RegistrationResponse,
    QueryRequest,
    QueryResponse,
    MatchedDataSource
)

# Environment variables
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.5"))
MAX_MATCHES = int(os.getenv("MAX_MATCHES", "10"))
MAX_CONTEXT_SIZE = int(os.getenv("MAX_CONTEXT_SIZE", "51200"))  # ~40% of 128k tokens

# Global instances
app = FastAPI(
    title="Contex",
    description="Semantic context routing for AI agents",
    version="0.2.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Will be initialized on startup
context_engine: ContextEngine = None
redis: Redis = None


@app.on_event("startup")
async def startup():
    global context_engine, redis

    print("=" * 60)
    print("Contex - Starting")
    print("=" * 60)
    print(f"Redis URL: {REDIS_URL}")
    print(f"Similarity Threshold: {SIMILARITY_THRESHOLD}")
    print(f"Max Matches: {MAX_MATCHES}")
    print(f"Max Context Size: {MAX_CONTEXT_SIZE} tokens")
    print()

    # Connect to Redis
    redis = await Redis.from_url(REDIS_URL, decode_responses=False)
    print("✓ Connected to Redis")

    # Initialize Context Engine
    context_engine = ContextEngine(
        redis=redis,
        similarity_threshold=SIMILARITY_THRESHOLD,
        max_matches=MAX_MATCHES,
        max_context_size=MAX_CONTEXT_SIZE
    )
    print("✓ Context Engine initialized")
    print()
    print("Contex is ready!")
    print("=" * 60)


@app.on_event("shutdown")
async def shutdown():
    global redis
    if redis:
        await redis.aclose()
        print("✓ Redis connection closed")


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Contex",
        "status": "running",
        "version": "0.2.0"
    }


@app.get("/health")
async def health():
    """Health check endpoint for Docker and monitoring"""
    return {"status": "healthy"}


@app.post("/data/publish", response_model=dict)
async def publish_data(event: DataPublishEvent):
    """
    Main app publishes data change.

    Example:
        POST /data/publish
        {
            "project_id": "proj_123",
            "data_key": "tech_stack",
            "data": {"backend": "Python/FastAPI", "frontend": "React"}
        }
    """
    try:
        sequence = await context_engine.publish_data(event)
        return {
            "status": "published",
            "project_id": event.project_id,
            "data_key": event.data_key,
            "sequence": sequence
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agents/register", response_model=RegistrationResponse)
async def register_agent(registration: AgentRegistration):
    """
    Agent registers with semantic data needs.

    Example:
        POST /agents/register
        {
            "agent_id": "task-decomposer",
            "project_id": "proj_123",
            "data_needs": [
                "programming languages and frameworks",
                "event model with events and commands"
            ],
            "last_seen_sequence": "0"
        }
    """
    try:
        response = await context_engine.register_agent(registration)
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/agents/{agent_id}")
async def unregister_agent(agent_id: str):
    """Unregister an agent"""
    await context_engine.unregister_agent(agent_id)
    return {"status": "unregistered", "agent_id": agent_id}


@app.get("/agents")
async def list_agents():
    """List all registered agents"""
    agents = context_engine.get_registered_agents()
    return {"agents": agents, "count": len(agents)}


@app.get("/agents/{agent_id}")
async def get_agent_info(agent_id: str):
    """Get info about a registered agent"""
    info = context_engine.get_agent_info(agent_id)
    if not info:
        raise HTTPException(status_code=404, detail="Agent not found")
    return info


@app.get("/projects/{project_id}/events")
async def get_project_events(project_id: str, since: str = "0", count: int = 100):
    """Get events for a project"""
    events = await context_engine.event_store.get_events_since(
        project_id,
        since,
        count
    )
    return {"events": events, "count": len(events)}


@app.get("/projects/{project_id}/data")
async def get_project_data(project_id: str):
    """Get all registered data keys for a project"""
    data_keys = context_engine.semantic_matcher.get_registered_data(project_id)
    return {"project_id": project_id, "data_keys": data_keys, "count": len(data_keys)}


@app.post("/projects/{project_id}/query", response_model=QueryResponse)
async def query_project(project_id: str, request: QueryRequest):
    """
    Ad-hoc semantic query of project data without agent registration.

    This endpoint allows you to ask questions about your project data
    without the overhead of registering an agent. Perfect for:
    - One-off queries from CLIs or scripts
    - Interactive exploration of project data
    - Testing semantic matching quality
    - Quick lookups during development

    Example:
        POST /projects/my-app/query
        {
            "query": "What authentication methods are we using?",
            "top_k": 3
        }

    Returns:
        Matched data sources ranked by semantic similarity
    """
    try:
        matches = context_engine.query_project_data(
            project_id=project_id,
            query=request.query,
            top_k=request.top_k
        )

        # Convert to MatchedDataSource models
        matched_sources = [
            MatchedDataSource(
                data_key=match["data_key"],
                similarity=match["similarity"],
                data=match["data"],
                description=match.get("description")
            )
            for match in matches
        ]

        return QueryResponse(
            query=request.query,
            matches=matched_sources,
            total_matches=len(matched_sources)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
