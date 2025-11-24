"""Contex SDK data models"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DataEvent(BaseModel):
    """Data event to publish"""
    project_id: str = Field(..., description="Project identifier")
    data_key: str = Field(..., description="Unique key for this data")
    data: Any = Field(..., description="Data payload (any JSON-serializable type)")
    data_format: Optional[str] = Field(default="json", description="Data format: json, yaml, toml, text")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Optional metadata")


class AgentRegistration(BaseModel):
    """Agent registration request"""
    agent_id: str = Field(..., description="Unique agent identifier")
    project_id: str = Field(..., description="Project identifier")
    data_needs: List[str] = Field(..., description="List of data needs in natural language")
    notification_method: Optional[str] = Field(default="redis", description="Notification method: redis or webhook")
    webhook_url: Optional[str] = Field(default=None, description="Webhook URL (if notification_method=webhook)")
    webhook_secret: Optional[str] = Field(default=None, description="Webhook secret for HMAC verification")
    last_seen_sequence: Optional[str] = Field(default="0", description="Last seen sequence number")


class MatchedData(BaseModel):
    """Matched data returned to agent"""
    data_key: str
    data: Any
    similarity_score: float
    sequence: str
    timestamp: str


class RegistrationResponse(BaseModel):
    """Agent registration response"""
    agent_id: str
    project_id: str
    notification_channel: Optional[str] = None
    matched_data: List[MatchedData]
    last_seen_sequence: str


class QueryRequest(BaseModel):
    """Query request"""
    project_id: str
    query: str
    max_results: Optional[int] = Field(default=10, ge=1, le=100)


class QueryResponse(BaseModel):
    """Query response"""
    results: List[MatchedData]
    total: int


class APIKeyResponse(BaseModel):
    """API key creation response"""
    key_id: str
    key: str
    name: str
    created_at: str


class RateLimitInfo(BaseModel):
    """Rate limit information"""
    limit: int
    remaining: int
    reset_at: str
