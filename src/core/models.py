"""Data models for Context Engine v2"""

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class DataPublishEvent(BaseModel):
    """Event published by main app when data changes"""

    project_id: str = Field(..., description="Project identifier")
    data_key: str = Field(
        ..., description="Data identifier (e.g., 'tech_stack', 'event_model')", pattern=r"^[^\x00-\x1f]*$"
    )
    data: Any = Field(
        ...,
        description="The actual data in any format (JSON dict, YAML string, plain text, etc.)"
    )
    data_format: Optional[str] = Field(
        default=None,
        description="Optional format hint: 'json', 'yaml', 'csv', 'text', 'markdown', 'code', 'pdf', 'docx', 'image' (auto-detected if not provided)"
    )
    event_type: Optional[str] = Field(
        default=None, description="Optional event type (auto-generated if not provided)"
    )
    published_at: Optional[datetime] = Field(
        default=None, description="When the source changed this content; defaults to ingest time"
    )

