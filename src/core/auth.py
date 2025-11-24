"""Authentication module for Contex"""

import secrets
import hashlib
from typing import Optional, List
from pydantic import BaseModel
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from redis.asyncio import Redis

class APIKey(BaseModel):
    """API Key model"""
    key_id: str
    name: str
    prefix: str
    scopes: List[str] = []
    created_at: str

class APIKeyMiddleware(BaseHTTPMiddleware):
    """Middleware to validate API keys"""

    def __init__(self, app, public_paths: List[str] = None):
        super().__init__(app)
        self.public_paths = public_paths or [
            "/health",
            "/api/docs",
            "/api/openapi.json",
            "/sandbox",
            "/static",
            "/favicon.ico"
        ]

    async def dispatch(self, request: Request, call_next):
        # Skip auth for public paths
        if request.url.path == "/" or any(request.url.path.startswith(path) for path in self.public_paths):
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        if not api_key:
            return JSONResponse(status_code=401, content={"detail": "Missing API Key"})

        key_id = await self.validate_key(request, api_key)
        if not key_id:
            return JSONResponse(status_code=401, content={"detail": "Invalid API Key"})

        # Store key_id in request state for RBAC middleware
        request.state.api_key_id = key_id

        return await call_next(request)

    async def validate_key(self, request: Request, api_key: str) -> Optional[str]:
        """Validate API key against Redis and return key_id if valid"""
        # Key format: ck_<random>
        if not api_key.startswith("ck_"):
            return None

        # Hash key for lookup
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()

        # Get Redis from app state
        redis = request.app.state.redis

        # Check if key exists in Redis and get key_id
        data = await redis.hgetall(f"contex:apikey:{key_hash}")
        if not data:
            return None

        # Extract key_id
        key_id = data.get(b"key_id")
        if key_id:
            return key_id.decode() if isinstance(key_id, bytes) else key_id

        return None

async def create_api_key(redis: Redis, name: str, scopes: List[str] = None) -> tuple[str, APIKey]:
    """Create a new API key"""
    import datetime
    
    # Generate key
    raw_key = f"ck_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_id = secrets.token_hex(8)
    
    api_key = APIKey(
        key_id=key_id,
        name=name,
        prefix=raw_key[:7],
        scopes=scopes or [],
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    
    # Store in Redis
    data = api_key.model_dump()
    data['scopes'] = str(data['scopes'])
    
    # Map hash -> metadata
    await redis.hset(
        f"contex:apikey:{key_hash}",
        mapping=data
    )
    
    # Map ID -> hash (for management)
    await redis.set(f"contex:apikey_id:{key_id}", key_hash)
    
    return raw_key, api_key

async def revoke_api_key(redis: Redis, key_id: str) -> bool:
    """Revoke an API key by ID"""
    # Get hash from ID
    key_hash = await redis.get(f"contex:apikey_id:{key_id}")
    if not key_hash:
        return False
        
    if isinstance(key_hash, bytes):
        key_hash = key_hash.decode()
        
    # Delete both
    await redis.delete(f"contex:apikey:{key_hash}")
    await redis.delete(f"contex:apikey_id:{key_id}")
    return True

async def list_api_keys(redis: Redis) -> List[APIKey]:
    """List all API keys"""
    # Scan for ID mappings
    keys = []
    async for key in redis.scan_iter("contex:apikey_id:*"):
        key_hash = await redis.get(key)
        if isinstance(key_hash, bytes):
            key_hash = key_hash.decode()
            
        data = await redis.hgetall(f"contex:apikey:{key_hash}")
        if data:
            # Decode bytes to strings
            decoded = {k.decode(): v.decode() for k, v in data.items()}
            # Handle scopes list (stored as string representation)
            if 'scopes' in decoded:
                import ast
                try:
                    decoded['scopes'] = ast.literal_eval(decoded['scopes'])
                except:
                    decoded['scopes'] = []
            keys.append(APIKey(**decoded))
            
    return keys
