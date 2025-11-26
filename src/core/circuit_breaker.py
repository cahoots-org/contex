"""Circuit breaker pattern for webhook reliability"""

import time
from enum import Enum
from typing import Optional, Callable, Any
from dataclasses import dataclass
from datetime import datetime, timezone
from src.core.logging import get_logger

# Import metrics (lazy import to avoid circular dependencies)
_metrics_imported = False
_circuit_breaker_state = None
_circuit_breaker_failures_total = None
_circuit_breaker_successes_total = None
_circuit_breaker_transitions_total = None

def _import_metrics():
    """Lazy import metrics to avoid circular dependencies"""
    global _metrics_imported, _circuit_breaker_state, _circuit_breaker_failures_total
    global _circuit_breaker_successes_total, _circuit_breaker_transitions_total

    if not _metrics_imported:
        try:
            from src.core.metrics import (
                circuit_breaker_state,
                circuit_breaker_failures_total,
                circuit_breaker_successes_total,
                circuit_breaker_transitions_total
            )
            _circuit_breaker_state = circuit_breaker_state
            _circuit_breaker_failures_total = circuit_breaker_failures_total
            _circuit_breaker_successes_total = circuit_breaker_successes_total
            _circuit_breaker_transitions_total = circuit_breaker_transitions_total
            _metrics_imported = True
        except ImportError:
            pass

logger = get_logger(__name__)


class CircuitState(str, Enum):
    """Circuit breaker states"""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, rejecting requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration"""
    failure_threshold: int = 5  # Failures before opening
    success_threshold: int = 2  # Successes in half-open before closing
    timeout: int = 60  # Seconds before trying half-open
    
    def __post_init__(self):
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if self.success_threshold < 1:
            raise ValueError("success_threshold must be >= 1")
        if self.timeout < 1:
            raise ValueError("timeout must be >= 1")


class CircuitBreaker:
    """
    Circuit breaker for protecting against cascading failures.
    
    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Too many failures, rejecting all requests
    - HALF_OPEN: Testing if service recovered
    
    Usage:
        breaker = CircuitBreaker(name="webhook-service")
        
        try:
            with breaker:
                result = await send_webhook(...)
        except CircuitBreakerOpen:
            # Handle circuit open
            logger.warning("Circuit breaker open, skipping webhook")
    """
    
    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None
    ):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self.last_state_change: float = time.time()
        
        logger.info(f"Circuit breaker '{name}' initialized",
                   failure_threshold=self.config.failure_threshold,
                   timeout=self.config.timeout)
    
    def __enter__(self):
        """Context manager entry"""
        if not self.can_execute():
            raise CircuitBreakerOpen(f"Circuit breaker '{self.name}' is OPEN")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        if exc_type is None:
            # Success
            self.record_success()
        else:
            # Failure
            self.record_failure()
        return False  # Don't suppress exceptions
    
    def can_execute(self) -> bool:
        """Check if request can be executed"""
        if self.state == CircuitState.CLOSED:
            return True
        
        if self.state == CircuitState.OPEN:
            # Check if timeout has passed
            if self.last_failure_time and \
               (time.time() - self.last_failure_time) >= self.config.timeout:
                self._transition_to_half_open()
                return True
            return False
        
        # HALF_OPEN state
        return True
    
    def record_success(self):
        """Record a successful execution"""
        _import_metrics()
        if _circuit_breaker_successes_total:
            _circuit_breaker_successes_total.labels(name=self.name).inc()

        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            logger.debug(f"Circuit breaker '{self.name}' success in HALF_OPEN",
                        success_count=self.success_count,
                        threshold=self.config.success_threshold)

            if self.success_count >= self.config.success_threshold:
                self._transition_to_closed()
        elif self.state == CircuitState.CLOSED:
            # Reset failure count on success
            if self.failure_count > 0:
                logger.debug(f"Circuit breaker '{self.name}' success, resetting failures",
                            previous_failures=self.failure_count)
                self.failure_count = 0
    
    def record_failure(self):
        """Record a failed execution"""
        _import_metrics()
        if _circuit_breaker_failures_total:
            _circuit_breaker_failures_total.labels(name=self.name).inc()

        self.failure_count += 1
        self.last_failure_time = time.time()

        logger.warning(f"Circuit breaker '{self.name}' failure recorded",
                      failure_count=self.failure_count,
                      threshold=self.config.failure_threshold,
                      state=self.state.value)

        if self.state == CircuitState.HALF_OPEN:
            # Failure in half-open immediately opens circuit
            self._transition_to_open()
        elif self.state == CircuitState.CLOSED:
            if self.failure_count >= self.config.failure_threshold:
                self._transition_to_open()
    
    def _transition_to_open(self):
        """Transition to OPEN state"""
        old_state = self.state
        self.state = CircuitState.OPEN
        self.last_state_change = time.time()
        self.success_count = 0

        _import_metrics()
        if _circuit_breaker_state:
            _circuit_breaker_state.labels(name=self.name).set(2)  # 2 = OPEN
        if _circuit_breaker_transitions_total:
            _circuit_breaker_transitions_total.labels(
                name=self.name,
                from_state=old_state.value,
                to_state='open'
            ).inc()

        logger.error(f"Circuit breaker '{self.name}' OPENED",
                    failure_count=self.failure_count,
                    timeout=self.config.timeout)
    
    def _transition_to_half_open(self):
        """Transition to HALF_OPEN state"""
        old_state = self.state
        self.state = CircuitState.HALF_OPEN
        self.last_state_change = time.time()
        self.failure_count = 0
        self.success_count = 0

        _import_metrics()
        if _circuit_breaker_state:
            _circuit_breaker_state.labels(name=self.name).set(1)  # 1 = HALF_OPEN
        if _circuit_breaker_transitions_total:
            _circuit_breaker_transitions_total.labels(
                name=self.name,
                from_state=old_state.value,
                to_state='half_open'
            ).inc()

        logger.info(f"Circuit breaker '{self.name}' transitioned to HALF_OPEN")
    
    def _transition_to_closed(self):
        """Transition to CLOSED state"""
        old_state = self.state
        self.state = CircuitState.CLOSED
        self.last_state_change = time.time()
        self.failure_count = 0
        self.success_count = 0

        _import_metrics()
        if _circuit_breaker_state:
            _circuit_breaker_state.labels(name=self.name).set(0)  # 0 = CLOSED
        if _circuit_breaker_transitions_total:
            _circuit_breaker_transitions_total.labels(
                name=self.name,
                from_state=old_state.value,
                to_state='closed'
            ).inc()

        logger.info(f"Circuit breaker '{self.name}' CLOSED (recovered)")
    
    def reset(self):
        """Manually reset the circuit breaker"""
        logger.info(f"Circuit breaker '{self.name}' manually reset")
        self._transition_to_closed()
    
    def get_state(self) -> dict:
        """Get current circuit breaker state"""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_time": datetime.fromtimestamp(
                self.last_failure_time, tz=timezone.utc
            ).isoformat() if self.last_failure_time else None,
            "last_state_change": datetime.fromtimestamp(
                self.last_state_change, tz=timezone.utc
            ).isoformat(),
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "success_threshold": self.config.success_threshold,
                "timeout": self.config.timeout,
            }
        }


class CircuitBreakerOpen(Exception):
    """Exception raised when circuit breaker is open"""
    pass


class CircuitBreakerRegistry:
    """Registry for managing multiple circuit breakers"""
    
    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}
    
    def get_breaker(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None
    ) -> CircuitBreaker:
        """Get or create a circuit breaker"""
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name, config)
        return self._breakers[name]
    
    def get_all_states(self) -> dict[str, dict]:
        """Get states of all circuit breakers"""
        return {
            name: breaker.get_state()
            for name, breaker in self._breakers.items()
        }
    
    def reset_all(self):
        """Reset all circuit breakers"""
        for breaker in self._breakers.values():
            breaker.reset()


# Global registry
_registry = CircuitBreakerRegistry()


def get_circuit_breaker(
    name: str,
    config: Optional[CircuitBreakerConfig] = None
) -> CircuitBreaker:
    """Get a circuit breaker from the global registry"""
    return _registry.get_breaker(name, config)


def get_all_circuit_breakers() -> dict[str, dict]:
    """Get all circuit breaker states"""
    return _registry.get_all_states()
