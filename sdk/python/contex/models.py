"""
Contex SDK data models.

These models are designed to be compatible with the Contex server's response formats.
When updating these models, ensure they match the server models in src/core/models.py.

Last synced with server: 2024-11-30
"""

from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field


class DataEvent(BaseModel):
    """
    Data event to publish to Contex.

    This is the format used to publish data from your application to Contex.
    """
    project_id: str = Field(..., description="Project identifier")
    data_key: str = Field(..., description="Unique key for this data (e.g., 'tech_stack', 'event_model')")
    data: Any = Field(..., description="Data payload (any JSON-serializable type)")
    data_format: Optional[str] = Field(
        default="json",
        description="Data format: json, yaml, toml, text, markdown"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata about this data"
    )


class AgentRegistration(BaseModel):
    """
    Agent registration request.

    Sent to Contex when an agent wants to register for data updates.
    Matches server's AgentRegistration in src/core/models.py.
    """
    agent_id: str = Field(..., description="Unique agent identifier")
    project_id: str = Field(..., description="Project identifier")
    data_needs: List[str] = Field(
        ...,
        description="Semantic descriptions of data the agent needs (natural language)",
        examples=[
            "programming languages and frameworks used",
            "event model with events and commands",
            "completed tasks and patterns",
        ],
    )
    notification_method: Literal["redis", "webhook"] = Field(
        default="redis",
        description="How to notify agent of updates: 'redis' (pub/sub) or 'webhook' (HTTP POST)"
    )
    webhook_url: Optional[str] = Field(
        default=None,
        description="Webhook URL (required when notification_method='webhook')"
    )
    webhook_secret: Optional[str] = Field(
        default=None,
        description="Webhook secret for HMAC verification"
    )
    last_seen_sequence: Optional[str] = Field(
        default="0",
        description="Last event sequence number agent processed (for catch-up)"
    )
    response_format: Literal["json", "yaml", "toml", "csv", "xml", "markdown", "toon", "text"] = Field(
        default="json",
        description="Preferred data format for responses"
    )


class MatchedDataSource(BaseModel):
    """
    A data source that matches an agent's semantic need.

    Returned as part of initial context sent to agents.
    Matches server's MatchedDataSource in src/core/models.py.
    """
    data_key: str = Field(..., description="Data identifier")
    similarity: float = Field(..., description="Similarity score (0-1)")
    data: Dict[str, Any] = Field(..., description="The matched data")
    description: Optional[str] = Field(
        default=None,
        description="Auto-generated description of the data"
    )
    token_count: Optional[int] = Field(
        default=None,
        description="Approximate token count for this data"
    )
    preview: Optional[str] = Field(
        default=None,
        description="Preview of the data (first 200 chars)"
    )


class RegistrationResponse(BaseModel):
    """
    Response from agent registration.

    IMPORTANT: This model must match the server's RegistrationResponse exactly.
    Server source: src/core/models.py and src/core/context_engine.py:register_agent()

    Fields:
        status: Always "registered" on success
        agent_id: The registered agent's ID
        project_id: The project the agent registered with
        caught_up_events: Number of missed events sent during registration
        current_sequence: Latest event sequence number in the project
        matched_needs: Dict mapping each need to number of data items that matched
        notification_channel: Redis channel or webhook URL for notifications
    """
    status: str = Field(..., description="'registered' or 'error'")
    agent_id: str = Field(..., description="Registered agent ID")
    project_id: str = Field(..., description="Project ID")
    caught_up_events: int = Field(
        ...,
        description="Number of missed events sent during registration"
    )
    current_sequence: str = Field(
        ...,
        description="Latest event sequence number"
    )
    matched_needs: Dict[str, int] = Field(
        ...,
        description="Number of matches found for each semantic need"
    )
    notification_channel: str = Field(
        ...,
        description="Channel where agent will receive updates"
    )


class MatchedData(BaseModel):
    """
    Matched data returned from queries.

    Used in QueryResponse to represent individual search results.
    """
    data_key: str = Field(..., description="Data identifier")
    data: Any = Field(..., description="The matched data")
    similarity_score: float = Field(..., description="Similarity score (0-1)", ge=0, le=1)
    sequence: str = Field(..., description="Event sequence number")
    timestamp: str = Field(..., description="When this data was published")


class QueryRequest(BaseModel):
    """
    Semantic query request.

    Used to search for data within a project.
    """
    project_id: str = Field(..., description="Project to search in")
    query: str = Field(..., description="Natural language query", min_length=1)
    max_results: Optional[int] = Field(
        default=10,
        description="Maximum number of results to return",
        ge=1,
        le=100
    )
    threshold: Optional[float] = Field(
        default=None,
        description="Minimum similarity threshold (0-1)",
        ge=0.0,
        le=1.0
    )


class QueryResponse(BaseModel):
    """
    Response from semantic query.

    Contains matched data items sorted by relevance.
    """
    results: List[MatchedData] = Field(..., description="Matched data items")
    total: int = Field(..., description="Total number of matches")


class APIKeyResponse(BaseModel):
    """
    Response from API key creation.

    Contains the newly created API key (only shown once).
    """
    key_id: str = Field(..., description="Key identifier (for management)")
    key: str = Field(..., description="The API key (only shown once)")
    name: str = Field(..., description="Key name/description")
    created_at: str = Field(..., description="When the key was created")


class RateLimitInfo(BaseModel):
    """
    Rate limit information from response headers.

    Returned when rate limiting is active.
    """
    limit: int = Field(..., description="Maximum requests per window")
    remaining: int = Field(..., description="Requests remaining in current window")
    reset_at: str = Field(..., description="When the rate limit resets")


class PublishResponse(BaseModel):
    """
    Response from publishing data.

    Returned after successfully publishing data to a project.
    """
    status: str = Field(..., description="'published' on success")
    sequence: str = Field(..., description="Event sequence number assigned")
    data_key: str = Field(..., description="The data key that was published")
    project_id: Optional[str] = Field(default=None, description="Project ID")


class UnregisterResponse(BaseModel):
    """
    Response from agent unregistration.
    """
    status: str = Field(..., description="'unregistered' on success")
    agent_id: str = Field(..., description="The agent that was unregistered")


class HealthResponse(BaseModel):
    """
    Response from health check endpoint.
    """
    status: str = Field(..., description="'healthy' when server is operational")
    version: str = Field(..., description="Server version")
    uptime: Optional[float] = Field(default=None, description="Server uptime in seconds")
