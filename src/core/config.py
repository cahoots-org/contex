"""Configuration management with validation for Contex"""

import os
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator
from src.core.logging import get_logger

logger = get_logger(__name__)


class DatabaseConfig(BaseModel):
    """PostgreSQL database configuration"""
    url: str = Field(
        default="postgresql+asyncpg://contex:contex_password@localhost:5432/contex",
        description="PostgreSQL connection URL"
    )
    pool_size: int = Field(default=5, ge=1, le=100, description="Connection pool size")
    max_overflow: int = Field(default=10, ge=0, le=100, description="Max overflow connections")
    pool_timeout: float = Field(default=30.0, ge=1, le=300, description="Pool timeout in seconds")
    pool_recycle: int = Field(default=1800, ge=60, le=7200, description="Recycle connections after seconds")
    echo: bool = Field(default=False, description="Echo SQL statements")

    @field_validator('url')
    @classmethod
    def validate_url(cls, v):
        if not v.startswith(('postgresql+asyncpg://', 'postgresql://', 'sqlite+aiosqlite://')):
            raise ValueError("Database URL must start with postgresql+asyncpg://, postgresql://, or sqlite+aiosqlite://")
        return v


class RedisConfig(BaseModel):
    """Redis configuration (for pub/sub only)"""
    url: str = Field(default="redis://localhost:6379", description="Redis connection URL")
    max_connections: int = Field(default=50, ge=1, le=1000, description="Maximum Redis connections")
    timeout: int = Field(default=5, ge=1, le=60, description="Redis timeout in seconds")

    @field_validator('url')
    @classmethod
    def validate_url(cls, v):
        if not v.startswith(('redis://', 'rediss://')):
            raise ValueError("Redis URL must start with redis:// or rediss://")
        return v


class SecurityConfig(BaseModel):
    """Security configuration"""
    api_key_salt: Optional[str] = Field(default=None, description="Salt for API key hashing")
    rate_limit_enabled: bool = Field(default=True, description="Enable rate limiting")
    rate_limit_requests: int = Field(default=100, ge=1, le=10000, description="Requests per minute")
    rate_limit_window: int = Field(default=60, ge=1, le=3600, description="Rate limit window in seconds")
    cors_origins: List[str] = Field(default=["*"], description="Allowed CORS origins")
    cors_allow_credentials: bool = Field(default=True, description="Allow credentials in CORS")

    @field_validator('api_key_salt')
    @classmethod
    def validate_salt(cls, v):
        if v and len(v) < 16:
            raise ValueError("API key salt must be at least 16 characters")
        return v

    @field_validator('cors_origins', mode='before')
    @classmethod
    def validate_cors_origins(cls, v):
        if isinstance(v, str):
            # Allow comma-separated string from env var
            return [origin.strip() for origin in v.split(',')]
        return v


class ObservabilityConfig(BaseModel):
    """Observability configuration"""
    log_level: str = Field(default="INFO", description="Log level")
    log_json: bool = Field(default=True, description="Output logs as JSON")
    metrics_enabled: bool = Field(default=True, description="Enable Prometheus metrics")
    tracing_enabled: bool = Field(default=False, description="Enable distributed tracing")
    tracing_endpoint: Optional[str] = Field(default=None, description="Tracing endpoint URL")
    
    @field_validator('log_level')
    @classmethod
    def validate_log_level(cls, v):
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if v.upper() not in valid_levels:
            raise ValueError(f"Log level must be one of: {', '.join(valid_levels)}")
        return v.upper()


class FeaturesConfig(BaseModel):
    """Feature configuration"""
    similarity_threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="Similarity threshold")
    max_matches: int = Field(default=10, ge=1, le=100, description="Maximum matches per query")
    max_context_size: int = Field(default=51200, ge=1024, le=1048576, description="Maximum context size in tokens")
    hybrid_search_enabled: bool = Field(default=False, description="Enable hybrid search with RRF")
    rrf_k: int = Field(default=60, ge=1, le=1000, description="RRF constant (typical value: 60)")
    vector_boost: float = Field(default=1.0, ge=0.1, le=10.0, description="Vector rank boost multiplier")


class ContexConfig(BaseModel):
    """Main Contex configuration"""
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)

    @classmethod
    def from_env(cls) -> 'ContexConfig':
        """Load configuration from environment variables"""
        return cls(
            database=DatabaseConfig(
                url=os.getenv('DATABASE_URL', 'postgresql+asyncpg://contex:contex_password@localhost:5432/contex'),
                pool_size=int(os.getenv('DATABASE_POOL_SIZE', '5')),
                max_overflow=int(os.getenv('DATABASE_MAX_OVERFLOW', '10')),
                pool_timeout=float(os.getenv('DATABASE_POOL_TIMEOUT', '30.0')),
                pool_recycle=int(os.getenv('DATABASE_POOL_RECYCLE', '1800')),
                echo=os.getenv('DATABASE_ECHO', 'false').lower() == 'true',
            ),
            redis=RedisConfig(
                url=os.getenv('REDIS_URL', 'redis://localhost:6379'),
                max_connections=int(os.getenv('REDIS_MAX_CONNECTIONS', '50')),
                timeout=int(os.getenv('REDIS_TIMEOUT', '5')),
            ),
            security=SecurityConfig(
                api_key_salt=os.getenv('API_KEY_SALT'),
                rate_limit_enabled=os.getenv('RATE_LIMIT_ENABLED', 'true').lower() == 'true',
                rate_limit_requests=int(os.getenv('RATE_LIMIT_REQUESTS', '100')),
                rate_limit_window=int(os.getenv('RATE_LIMIT_WINDOW', '60')),
                cors_origins=os.getenv('CORS_ORIGINS', '*').split(',') if os.getenv('CORS_ORIGINS') else ['*'],
                cors_allow_credentials=os.getenv('CORS_ALLOW_CREDENTIALS', 'true').lower() == 'true',
            ),
            observability=ObservabilityConfig(
                log_level=os.getenv('LOG_LEVEL', 'INFO'),
                log_json=os.getenv('LOG_JSON', 'true').lower() == 'true',
                metrics_enabled=os.getenv('METRICS_ENABLED', 'true').lower() == 'true',
                tracing_enabled=os.getenv('TRACING_ENABLED', 'false').lower() == 'true',
                tracing_endpoint=os.getenv('TRACING_ENDPOINT'),
            ),
            features=FeaturesConfig(
                similarity_threshold=float(os.getenv('SIMILARITY_THRESHOLD', '0.5')),
                max_matches=int(os.getenv('MAX_MATCHES', '10')),
                max_context_size=int(os.getenv('MAX_CONTEXT_SIZE', '51200')),
                hybrid_search_enabled=os.getenv('HYBRID_SEARCH_ENABLED', 'false').lower() == 'true',
                rrf_k=int(os.getenv('RRF_K', '60')),
                vector_boost=float(os.getenv('VECTOR_BOOST', '1.0')),
            ),
        )
    
    def validate_config(self) -> list[str]:
        """Validate configuration and return warnings"""
        warnings = []

        # Check security warnings
        if not self.security.api_key_salt:
            warnings.append("API_KEY_SALT not set - using default (INSECURE for production)")

        if self.security.api_key_salt == "CHANGE_ME_IN_PRODUCTION":
            warnings.append("API_KEY_SALT is set to default value - CHANGE THIS IN PRODUCTION")

        # Check CORS configuration
        if "*" in self.security.cors_origins:
            warnings.append("CORS allows all origins (*) - INSECURE for production. Set CORS_ORIGINS env var.")

        if self.security.cors_allow_credentials and "*" in self.security.cors_origins:
            warnings.append("CORS allows credentials with wildcard origin - SECURITY RISK! This combination is not allowed by browsers.")

        # Check hybrid search configuration
        if self.features.hybrid_search_enabled:
            if self.features.rrf_k < 1:
                warnings.append(f"RRF constant must be positive (got {self.features.rrf_k})")
            if self.features.vector_boost < 0.1:
                warnings.append(f"Vector boost is very low: {self.features.vector_boost}")

        # Check resource limits
        if self.redis.max_connections > 500:
            warnings.append(f"High Redis connection limit: {self.redis.max_connections}")

        if self.features.max_context_size > 100000:
            warnings.append(f"Very large context size: {self.features.max_context_size} tokens")

        return warnings
    
    def log_config(self):
        """Log configuration (without sensitive data)"""
        # Mask password in database URL
        db_url_masked = self.database.url.split("@")[-1] if "@" in self.database.url else self.database.url
        logger.info("Configuration loaded",
                   database_url=db_url_masked,
                   redis_url=self.redis.url,
                   log_level=self.observability.log_level,
                   metrics_enabled=self.observability.metrics_enabled,
                   rate_limit_enabled=self.security.rate_limit_enabled,
                   similarity_threshold=self.features.similarity_threshold,
                   max_matches=self.features.max_matches)


def load_and_validate_config() -> ContexConfig:
    """Load configuration from environment and validate"""
    try:
        config = ContexConfig.from_env()
        
        # Validate and log warnings
        warnings = config.validate_config()
        for warning in warnings:
            logger.warning(f"Configuration warning: {warning}")
        
        # Log configuration
        config.log_config()
        
        return config
        
    except Exception as e:
        logger.error(f"Configuration validation failed: {e}")
        raise ValueError(f"Invalid configuration: {e}")
