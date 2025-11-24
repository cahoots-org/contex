"""
Contex Python SDK
~~~~~~~~~~~~~~~~~

Official Python client for Contex - Semantic context routing for AI agents.

Basic usage:

    >>> from contex import ContexClient
    >>> client = ContexClient(url="http://localhost:8001", api_key="ck_...")
    >>> await client.publish(project_id="my-app", data_key="config", data={"env": "prod"})

Full documentation: https://github.com/cahoots-org/contex
"""

__version__ = "0.2.0"
__author__ = "Contex Team"
__license__ = "MIT"

from .client import ContexClient, ContexAsyncClient
from .models import (
    DataEvent,
    AgentRegistration,
    RegistrationResponse,
    MatchedData,
)
from .exceptions import (
    ContexError,
    AuthenticationError,
    RateLimitError,
    ValidationError,
    NotFoundError,
    ServerError,
)

__all__ = [
    "ContexClient",
    "ContexAsyncClient",
    "DataEvent",
    "AgentRegistration",
    "RegistrationResponse",
    "MatchedData",
    "ContexError",
    "AuthenticationError",
    "RateLimitError",
    "ValidationError",
    "NotFoundError",
    "ServerError",
]
