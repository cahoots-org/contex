"""Enhanced health check system for Contex"""

from typing import Dict, Any, Optional
from datetime import datetime, timezone
from enum import Enum
import asyncio


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
    
    def __init__(self, redis, context_engine):
        self.redis = redis
        self.context_engine = context_engine
        self.startup_time = datetime.now(timezone.utc)
    
    async def check_redis(self) -> ComponentHealth:
        """Check Redis connectivity and performance"""
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
                        "used_memory_human": info.get("used_memory_human", "unknown")
                    }
                )
            
            return ComponentHealth(
                status=HealthStatus.HEALTHY,
                message="Redis is healthy",
                details={
                    "latency_ms": round(latency, 2),
                    "connected_clients": info.get("connected_clients", 0),
                    "used_memory_human": info.get("used_memory_human", "unknown")
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
    
    async def check_redisearch(self) -> ComponentHealth:
        """Check RediSearch index health"""
        try:
            # Check if index exists
            index_name = "contex:semantic_data"
            
            # Try to get index info
            try:
                info = await self.redis.execute_command("FT.INFO", index_name)
                
                # Parse info (it's a list of key-value pairs)
                info_dict = {}
                for i in range(0, len(info), 2):
                    key = info[i].decode() if isinstance(info[i], bytes) else info[i]
                    value = info[i+1]
                    if isinstance(value, bytes):
                        value = value.decode()
                    info_dict[key] = value
                
                num_docs = int(info_dict.get("num_docs", 0))
                
                return ComponentHealth(
                    status=HealthStatus.HEALTHY,
                    message="RediSearch index is healthy",
                    details={
                        "num_docs": num_docs,
                        "index_name": index_name
                    }
                )
            except Exception as e:
                if "Unknown index name" in str(e):
                    return ComponentHealth(
                        status=HealthStatus.DEGRADED,
                        message="RediSearch index not initialized",
                        details={"index_name": index_name}
                    )
                raise
        except Exception as e:
            return ComponentHealth(
                status=HealthStatus.UNHEALTHY,
                message=f"RediSearch check failed: {str(e)}",
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
    
    async def get_full_health(self) -> Dict[str, Any]:
        """Get comprehensive health status"""
        # Run all checks in parallel
        redis_health, embedding_health, redisearch_health, resources_health = await asyncio.gather(
            self.check_redis(),
            self.check_embedding_model(),
            self.check_redisearch(),
            self.check_system_resources(),
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
        
        redis_health = safe_health(redis_health, "Redis")
        embedding_health = safe_health(embedding_health, "Embedding")
        redisearch_health = safe_health(redisearch_health, "RediSearch")
        resources_health = safe_health(resources_health, "Resources")
        
        # Determine overall status
        statuses = [
            redis_health.status,
            embedding_health.status,
            redisearch_health.status,
            resources_health.status
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
                "redis": redis_health.to_dict(),
                "embedding_model": embedding_health.to_dict(),
                "redisearch": redisearch_health.to_dict(),
                "system_resources": resources_health.to_dict()
            }
        }
    
    async def get_readiness(self) -> Dict[str, Any]:
        """Get readiness status (can accept traffic)"""
        # Check critical components only
        redis_health = await self.check_redis()
        embedding_health = await self.check_embedding_model()
        
        ready = (
            redis_health.status != HealthStatus.UNHEALTHY and
            embedding_health.status != HealthStatus.UNHEALTHY
        )
        
        return {
            "ready": ready,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": {
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
