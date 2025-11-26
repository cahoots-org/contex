"""Data models for Context Engine v2"""

from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field, HttpUrl


class AgentRegistration(BaseModel):
    """Agent registration request"""

    agent_id: str = Field(..., description="Unique agent identifier")
    project_id: str = Field(..., description="Project this agent works on")
    data_needs: List[str] = Field(
        ...,
        description="Semantic descriptions of data the agent needs (natural language)",
        examples=[
            "programming languages and frameworks used",
            "event model with events and commands",
            "completed tasks and patterns",
        ],
    )
    last_seen_sequence: Optional[str] = Field(
        default="0",
        description="Last event sequence number agent processed (for catch-up)",
    )

    # Response format preference
    response_format: Literal["json", "yaml", "toml", "csv", "xml", "markdown", "toon", "text"] = Field(
        default="toon",
        description="Preferred data format: 'toon' (40% fewer tokens), 'json', 'yaml', 'toml', 'csv', 'xml', 'markdown', or 'text'",
    )

    # Notification method: redis or webhook
    notification_method: Literal["redis", "webhook"] = Field(
        default="redis",
        description="How to notify agent of updates: 'redis' (pub/sub) or 'webhook' (HTTP POST)",
    )

    # Redis pub/sub configuration (used when notification_method='redis')
    notification_channel: Optional[str] = Field(
        default=None,
        description="Redis pub/sub channel for updates (defaults to agent:{agent_id}:updates)",
    )

    # Webhook configuration (used when notification_method='webhook')
    webhook_url: Optional[HttpUrl] = Field(
        default=None,
        description="HTTP endpoint to POST updates to (required when notification_method='webhook')",
    )
    webhook_secret: Optional[str] = Field(
        default=None,
        description="Secret key for HMAC signature verification (optional but recommended)",
    )


class DataPublishEvent(BaseModel):
    """Event published by main app when data changes"""

    project_id: str = Field(..., description="Project identifier")
    data_key: str = Field(
        ..., description="Data identifier (e.g., 'tech_stack', 'event_model')"
    )
    data: Any = Field(
        ...,
        description="The actual data in any format (JSON dict, YAML string, TOML string, plain text, etc.)"
    )
    data_format: Optional[str] = Field(
        default=None,
        description="Optional format hint: 'json', 'yaml', 'toml', 'text', 'markdown' (auto-detected if not provided)"
    )
    event_type: Optional[str] = Field(
        default=None, description="Optional event type (auto-generated if not provided)"
    )


class MatchedDataSource(BaseModel):
    """A data source that matches an agent's semantic need"""

    data_key: str = Field(..., description="Data identifier")
    similarity: float = Field(..., description="Similarity score (0-1)")
    data: Dict[str, Any] = Field(..., description="The matched data")
    description: Optional[str] = Field(
        default=None, description="Auto-generated description of the data"
    )
    token_count: Optional[int] = Field(
        default=None, description="Approximate token count for this data"
    )
    preview: Optional[str] = Field(
        default=None, description="Preview of the data (first 200 chars)"
    )


class AgentContext(BaseModel):
    """Context sent to agent (organized by semantic needs)"""

    agent_id: str
    project_id: str
    context: Dict[str, List[MatchedDataSource]] = Field(
        ..., description="Matched data organized by semantic need"
    )
    current_sequence: str = Field(..., description="Latest event sequence number")


class RegistrationResponse(BaseModel):
    """Response to agent registration"""

    status: str = Field(..., description="'registered' or 'error'")
    agent_id: str
    project_id: str
    caught_up_events: int = Field(
        ..., description="Number of missed events sent during registration"
    )
    current_sequence: str = Field(..., description="Latest event sequence number")
    matched_needs: Dict[str, int] = Field(
        ..., description="Number of matches found for each semantic need"
    )
    notification_channel: str = Field(
        ..., description="Channel where agent will receive updates"
    )


class SemanticSearchRequest(BaseModel):
    """Request for semantic search over project data"""

    project_id: str
    index: str = Field(
        ..., description="Index to search (e.g., 'events', 'files', 'tasks')"
    )
    query: str = Field(..., description="Search query (natural language)")
    top_k: int = Field(default=5, description="Number of results to return")


class QueryRequest(BaseModel):
    """Request for ad-hoc semantic query"""

    query: str = Field(..., description="Natural language query", min_length=1)
    top_k: int = Field(
        default=5, description="Number of results to return", ge=1, le=50
    )
    threshold: Optional[float] = Field(
        default=None,
        description="Minimum similarity threshold (0-1). None uses system default (0.5). Lower for specific value searches.",
        ge=0.0,
        le=1.0,
    )
    max_tokens: Optional[int] = Field(
        default=None,
        description="Maximum tokens to return (truncates results to fit budget). None = no limit",
        ge=1000,
        le=128000,
    )
    response_format: Literal["json", "yaml", "toml", "csv", "xml", "markdown", "toon", "text"] = Field(
        default="toon",
        description="Response format: 'toon' (token-optimized), 'json', 'yaml', 'toml', 'csv', 'xml', 'markdown', or 'text'",
    )


class QueryResponse(BaseModel):
    """Response from ad-hoc semantic query"""

    query: str = Field(..., description="The query that was executed")
    matches: List[MatchedDataSource] = Field(..., description="Matched data sources")
    total_matches: int = Field(..., description="Total number of matches found")


class SemanticSearchResult(BaseModel):
    """Result from semantic search"""

    item: Dict[str, Any] = Field(..., description="The matched item")
    similarity: float = Field(..., description="Similarity score (0-1)")
