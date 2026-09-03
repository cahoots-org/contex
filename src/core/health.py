"""Enhanced health check system for Contex"""

from typing import Dict, Any, Optional
from datetime import datetime, timezone
from enum import Enum
import asyncio

from src.core.database import DatabaseManager


class HealthStatus(str, Enum):
    """Health check status"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ComponentHealth:
    """Health status for a component"""

    def __init__(
        self,
        status: HealthStatus,
        message: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        self.status = status
        self.message = message
        self.details = details or {}
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        result = {
            "status": self.status.value,
            "timestamp": self.timestamp
        }
        if self.message:
            result["message"] = self.message
        if self.details:
            result["details"] = self.details
        return result


class HealthChecker:
    """Comprehensive health checker for Contex"""

    def __init__(self, db: DatabaseManager, redis, context_engine, graceful_degradation=None):
        self.db = db
        self.redis = redis
        self.context_engine = context_engine
        self.graceful_degradation = graceful_degradation
        self.startup_time = datetime.now(timezone.utc)
    
    async def check_postgres(self) -> ComponentHealth:
        """Check PostgreSQL connectivity and performance"""
        try:
            health = await self.db.health_check()

            if health["status"] == "unhealthy":
                return ComponentHealth(
                    status=HealthStatus.UNHEALTHY,
                    message=f"PostgreSQL connection failed: {health.get('error', 'Unknown error')}",
                    details=health
                )
            elif health["status"] == "degraded":
                return ComponentHealth(
                    status=HealthStatus.DEGRADED,
                    message=f"High PostgreSQL latency: {health.get('latency_ms', 0):.2f}ms",
                    details=health
                )

            return ComponentHealth(
                status=HealthStatus.HEALTHY,
                message="PostgreSQL is healthy",
                details=health
            )
        except Exception as e:
            return ComponentHealth(
                status=HealthStatus.UNHEALTHY,
                message=f"PostgreSQL check failed: {str(e)}",
                details={"error": str(e)}
            )

    async def check_redis(self) -> ComponentHealth:
        """Check Redis connectivity and performance (pub/sub only)"""
        try:
            # Test basic connectivity
            start = asyncio.get_event_loop().time()
            await self.redis.ping()
            latency = (asyncio.get_event_loop().time() - start) * 1000

            # Get Redis info
            info = await self.redis.info()

            # Check if latency is acceptable
            if latency > 100:
                return ComponentHealth(
                    status=HealthStatus.DEGRADED,
                    message=f"High Redis latency: {latency:.2f}ms",
                    details={
                        "latency_ms": round(latency, 2),
                        "connected_clients": info.get("connected_clients", 0),
                        "used_memory_human": info.get("used_memory_human", "unknown"),
                        "purpose": "pub/sub"
                    }
                )

            return ComponentHealth(
                status=HealthStatus.HEALTHY,
                message="Redis is healthy (pub/sub)",
                details={
                    "latency_ms": round(latency, 2),
                    "connected_clients": info.get("connected_clients", 0),
                    "used_memory_human": info.get("used_memory_human", "unknown"),
                    "purpose": "pub/sub"
                }
            )
        except Exception as e:
            return ComponentHealth(
                status=HealthStatus.UNHEALTHY,
                message=f"Redis connection failed: {str(e)}",
                details={"error": str(e)}
            )
    
    async def check_embedding_model(self) -> ComponentHealth:
        """Check embedding model availability"""
        try:
            # Test encoding a simple string
            start = asyncio.get_event_loop().time()
            test_text = "health check"
            embedding = self.context_engine.semantic_matcher.model.encode(test_text)
            latency = (asyncio.get_event_loop().time() - start) * 1000
            
            if latency > 1000:
                return ComponentHealth(
                    status=HealthStatus.DEGRADED,
                    message=f"Slow embedding generation: {latency:.2f}ms",
                    details={
                        "latency_ms": round(latency, 2),
                        "embedding_dim": len(embedding)
                    }
                )
            
            return ComponentHealth(
                status=HealthStatus.HEALTHY,
                message="Embedding model is healthy",
                details={
                    "latency_ms": round(latency, 2),
                    "embedding_dim": len(embedding),
                    "model": self.context_engine.semantic_matcher.model_name
                }
            )
        except Exception as e:
            return ComponentHealth(
                status=HealthStatus.UNHEALTHY,
                message=f"Embedding model failed: {str(e)}",
                details={"error": str(e)}
            )
    
    async def check_pgvector(self) -> ComponentHealth:
        """Check pgvector extension health"""
        try:
            from sqlalchemy import text

            async with self.db.session() as session:
                # Verify pgvector extension exists
                result = await session.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
                row = result.fetchone()

                if not row:
                    return ComponentHealth(
                        status=HealthStatus.UNHEALTHY,
                        message="pgvector extension not installed",
                        details={"error": "Extension 'vector' not found"}
                    )

                # Count embeddings
                from src.core.db_models import Embedding
                from sqlalchemy import select, func

                result = await session.execute(select(func.count(Embedding.id)))
                embedding_count = result.scalar() or 0

                return ComponentHealth(
                    status=HealthStatus.HEALTHY,
                    message="pgvector is healthy",
                    details={
                        "extension_version": row[0],
                        "embedding_count": embedding_count
                    }
                )
        except Exception as e:
            return ComponentHealth(
                status=HealthStatus.UNHEALTHY,
                message=f"pgvector check failed: {str(e)}",
                details={"error": str(e)}
            )
    
    async def check_system_resources(self) -> ComponentHealth:
        """Check system resource usage"""
        try:
            import psutil
            
            # Get memory usage
            process = psutil.Process()
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            
            # Get CPU usage
            cpu_percent = process.cpu_percent(interval=0.1)
            
            # Check if resources are concerning
            if memory_mb > 2048:  # >2GB
                status = HealthStatus.DEGRADED
                message = f"High memory usage: {memory_mb:.0f}MB"
            elif cpu_percent > 80:
                status = HealthStatus.DEGRADED
                message = f"High CPU usage: {cpu_percent:.1f}%"
            else:
                status = HealthStatus.HEALTHY
                message = "System resources are healthy"
            
            return ComponentHealth(
                status=status,
                message=message,
                details={
                    "memory_mb": round(memory_mb, 2),
                    "cpu_percent": round(cpu_percent, 2)
                }
            )
        except ImportError:
            # psutil not available
            return ComponentHealth(
                status=HealthStatus.HEALTHY,
                message="System resource monitoring not available",
                details={"note": "Install psutil for resource monitoring"}
            )
        except Exception as e:
            return ComponentHealth(
                status=HealthStatus.DEGRADED,
                message=f"Resource check failed: {str(e)}",
                details={"error": str(e)}
            )
    
    async def check_degradation(self) -> ComponentHealth:
        """Check graceful degradation status"""
        if not self.graceful_degradation:
            return ComponentHealth(
                status=HealthStatus.HEALTHY,
                message="Graceful degradation not configured",
                details={"enabled": False}
            )

        try:
            status = self.graceful_degradation.get_status()
            mode = status.get("mode", "unknown")

            if mode == "normal":
                return ComponentHealth(
                    status=HealthStatus.HEALTHY,
                    message="Service operating normally",
                    details=status
                )
            elif mode in ("degraded", "readonly"):
                return ComponentHealth(
                    status=HealthStatus.DEGRADED,
                    message=f"Service in {mode} mode",
                    details=status
                )
            else:  # unavailable
                return ComponentHealth(
                    status=HealthStatus.UNHEALTHY,
                    message="Service unavailable",
                    details=status
                )
        except Exception as e:
            return ComponentHealth(
                status=HealthStatus.UNHEALTHY,
                message=f"Degradation check failed: {str(e)}",
                details={"error": str(e)}
            )

    async def get_full_health(self) -> Dict[str, Any]:
        """Get comprehensive health status"""
        # Run all checks in parallel
        postgres_health, redis_health, embedding_health, pgvector_health, resources_health, degradation_health = await asyncio.gather(
            self.check_postgres(),
            self.check_redis(),
            self.check_embedding_model(),
            self.check_pgvector(),
            self.check_system_resources(),
            self.check_degradation(),
            return_exceptions=True
        )

        # Handle any exceptions
        def safe_health(result, component_name):
            if isinstance(result, Exception):
                return ComponentHealth(
                    status=HealthStatus.UNHEALTHY,
                    message=f"{component_name} check failed",
                    details={"error": str(result)}
                )
            return result

        postgres_health = safe_health(postgres_health, "PostgreSQL")
        redis_health = safe_health(redis_health, "Redis")
        embedding_health = safe_health(embedding_health, "Embedding")
        pgvector_health = safe_health(pgvector_health, "pgvector")
        resources_health = safe_health(resources_health, "Resources")
        degradation_health = safe_health(degradation_health, "Degradation")

        # Determine overall status
        statuses = [
            postgres_health.status,
            redis_health.status,
            embedding_health.status,
            pgvector_health.status,
            resources_health.status,
            degradation_health.status
        ]

        if any(s == HealthStatus.UNHEALTHY for s in statuses):
            overall_status = HealthStatus.UNHEALTHY
        elif any(s == HealthStatus.DEGRADED for s in statuses):
            overall_status = HealthStatus.DEGRADED
        else:
            overall_status = HealthStatus.HEALTHY

        # Calculate uptime
        uptime_seconds = (datetime.now(timezone.utc) - self.startup_time).total_seconds()

        return {
            "status": overall_status.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": round(uptime_seconds, 2),
            "version": "0.2.0",
            "components": {
                "postgresql": postgres_health.to_dict(),
                "redis": redis_health.to_dict(),
                "embedding_model": embedding_health.to_dict(),
                "pgvector": pgvector_health.to_dict(),
                "system_resources": resources_health.to_dict(),
                "graceful_degradation": degradation_health.to_dict()
            }
        }
    
    async def get_readiness(self) -> Dict[str, Any]:
        """Get readiness status (can accept traffic)"""
        # Check critical components only
        postgres_health, redis_health, embedding_health = await asyncio.gather(
            self.check_postgres(),
            self.check_redis(),
            self.check_embedding_model(),
        )

        ready = (
            postgres_health.status != HealthStatus.UNHEALTHY and
            redis_health.status != HealthStatus.UNHEALTHY and
            embedding_health.status != HealthStatus.UNHEALTHY
        )

        return {
            "ready": ready,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": {
                "postgresql": postgres_health.status.value,
                "redis": redis_health.status.value,
                "embedding_model": embedding_health.status.value
            }
        }
    
    async def get_liveness(self) -> Dict[str, Any]:
        """Get liveness status (is running)"""
        # Simple check - if we can respond, we're alive
        return {
            "alive": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": round(
                (datetime.now(timezone.utc) - self.startup_time).total_seconds(),
                2
            )
        }
