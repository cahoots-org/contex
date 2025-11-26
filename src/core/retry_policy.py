"""Retry policies with exponential backoff for resilient operations"""

import asyncio
import random
from typing import Callable, TypeVar, Optional, List, Type
from functools import wraps
from src.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar('T')


class RetryExhausted(Exception):
    """Raised when all retry attempts have been exhausted"""
    def __init__(self, last_exception: Exception, attempts: int):
        self.last_exception = last_exception
        self.attempts = attempts
        super().__init__(f"All {attempts} retry attempts failed. Last error: {last_exception}")


class RetryPolicy:
    """
    Configurable retry policy with exponential backoff and jitter.

    Implements exponential backoff with jitter to prevent thundering herd:
    - Wait time = min(base_delay * (2 ** attempt) + random_jitter, max_delay)
    - Jitter helps distribute retry attempts across time

    Args:
        max_attempts: Maximum number of retry attempts (default: 3)
        base_delay: Initial delay in seconds (default: 1.0)
        max_delay: Maximum delay in seconds (default: 60.0)
        exponential_base: Base for exponential backoff (default: 2)
        jitter: Whether to add random jitter (default: True)
        retryable_exceptions: Tuple of exception types to retry (default: all Exception)
        on_retry: Optional callback called before each retry with (attempt, exception, delay)

    Example:
        ```python
        policy = RetryPolicy(max_attempts=5, base_delay=2.0)

        @policy.retry
        async def fetch_data():
            # This will retry up to 5 times with exponential backoff
            return await api_call()

        result = await fetch_data()
        ```
    """

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        retryable_exceptions: Optional[tuple] = None,
        on_retry: Optional[Callable] = None
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.retryable_exceptions = retryable_exceptions or (Exception,)
        self.on_retry = on_retry

    def calculate_delay(self, attempt: int) -> float:
        """
        Calculate delay for given attempt using exponential backoff with jitter.

        Args:
            attempt: Current attempt number (0-indexed)

        Returns:
            Delay in seconds
        """
        # Exponential backoff: base_delay * (exponential_base ^ attempt)
        delay = self.base_delay * (self.exponential_base ** attempt)

        # Cap at max_delay
        delay = min(delay, self.max_delay)

        # Add jitter (±25% of delay)
        if self.jitter:
            jitter_amount = delay * 0.25
            delay += random.uniform(-jitter_amount, jitter_amount)

        # Ensure non-negative
        return max(0, delay)

    def is_retryable(self, exception: Exception) -> bool:
        """
        Check if exception is retryable.

        Args:
            exception: Exception to check

        Returns:
            True if exception should trigger retry
        """
        return isinstance(exception, self.retryable_exceptions)

    def retry(self, func: Callable[..., T]) -> Callable[..., T]:
        """
        Decorator to add retry logic to async functions.

        Args:
            func: Async function to wrap with retry logic

        Returns:
            Wrapped function with retry capability

        Example:
            ```python
            @retry_policy.retry
            async def risky_operation():
                # This will automatically retry on failure
                return await external_api_call()
            ```
        """
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception = None

            for attempt in range(self.max_attempts):
                try:
                    # Try the operation
                    result = await func(*args, **kwargs)

                    # Success - log if this was a retry
                    if attempt > 0:
                        logger.info(
                            f"Operation succeeded after {attempt + 1} attempts",
                            function=func.__name__,
                            attempt=attempt + 1,
                            max_attempts=self.max_attempts
                        )

                    return result

                except Exception as e:
                    last_exception = e

                    # Check if we should retry
                    if not self.is_retryable(e):
                        logger.warning(
                            f"Non-retryable exception in {func.__name__}",
                            function=func.__name__,
                            exception=str(e),
                            exception_type=type(e).__name__
                        )
                        raise

                    # Check if we have more attempts left
                    if attempt >= self.max_attempts - 1:
                        logger.error(
                            f"All retry attempts exhausted for {func.__name__}",
                            function=func.__name__,
                            attempts=self.max_attempts,
                            last_exception=str(e)
                        )
                        raise RetryExhausted(e, self.max_attempts) from e

                    # Calculate delay and wait
                    delay = self.calculate_delay(attempt)

                    logger.warning(
                        f"Retry attempt {attempt + 1}/{self.max_attempts} for {func.__name__}",
                        function=func.__name__,
                        attempt=attempt + 1,
                        max_attempts=self.max_attempts,
                        delay=f"{delay:.2f}s",
                        exception=str(e),
                        exception_type=type(e).__name__
                    )

                    # Call on_retry callback if provided
                    if self.on_retry:
                        try:
                            await self.on_retry(attempt + 1, e, delay)
                        except Exception as callback_error:
                            logger.error(
                                "Error in retry callback",
                                error=str(callback_error)
                            )

                    # Wait before retrying
                    await asyncio.sleep(delay)

            # This should never be reached, but just in case
            raise RetryExhausted(last_exception, self.max_attempts) from last_exception

        return wrapper


# Pre-configured retry policies for common scenarios

# Conservative policy for critical operations (more attempts, longer delays)
CONSERVATIVE_RETRY = RetryPolicy(
    max_attempts=5,
    base_delay=2.0,
    max_delay=120.0,
    exponential_base=2.0,
    jitter=True
)

# Aggressive policy for less critical operations (fewer attempts, shorter delays)
AGGRESSIVE_RETRY = RetryPolicy(
    max_attempts=3,
    base_delay=0.5,
    max_delay=10.0,
    exponential_base=2.0,
    jitter=True
)

# Fast retry policy for quick operations
FAST_RETRY = RetryPolicy(
    max_attempts=3,
    base_delay=0.1,
    max_delay=2.0,
    exponential_base=2.0,
    jitter=True
)

# Network-specific retry policy (retries common network errors)
NETWORK_RETRY = RetryPolicy(
    max_attempts=4,
    base_delay=1.0,
    max_delay=30.0,
    exponential_base=2.0,
    jitter=True,
    retryable_exceptions=(
        ConnectionError,
        TimeoutError,
        OSError,
        Exception  # Catch-all for network-like errors
    )
)


async def retry_with_policy(
    func: Callable[..., T],
    policy: RetryPolicy,
    *args,
    **kwargs
) -> T:
    """
    Execute function with retry policy.

    This is an alternative to using the decorator when you need
    one-off retry logic without modifying the function.

    Args:
        func: Async function to execute
        policy: Retry policy to use
        *args: Positional arguments for func
        **kwargs: Keyword arguments for func

    Returns:
        Result of successful function execution

    Raises:
        RetryExhausted: If all retry attempts fail

    Example:
        ```python
        result = await retry_with_policy(
            risky_operation,
            CONSERVATIVE_RETRY,
            arg1, arg2,
            kwarg1=value1
        )
        ```
    """
    @policy.retry
    async def wrapper():
        return await func(*args, **kwargs)

    return await wrapper()


class CircuitBreakerRetryPolicy(RetryPolicy):
    """
    Retry policy that integrates with circuit breaker pattern.

    Stops retrying when circuit breaker is open to prevent
    overwhelming a failing service.

    Args:
        circuit_breaker: Circuit breaker instance to check
        **kwargs: Arguments passed to RetryPolicy

    Example:
        ```python
        from src.core.circuit_breaker import CircuitBreaker

        breaker = CircuitBreaker("api", failure_threshold=5)
        policy = CircuitBreakerRetryPolicy(breaker, max_attempts=3)

        @policy.retry
        async def call_api():
            return await external_api()
        ```
    """

    def __init__(self, circuit_breaker, **kwargs):
        super().__init__(**kwargs)
        self.circuit_breaker = circuit_breaker

    def retry(self, func: Callable[..., T]) -> Callable[..., T]:
        """Wrap function with circuit breaker aware retry logic"""
        base_retry = super().retry

        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            # Check if circuit breaker is open
            if not self.circuit_breaker.can_execute():
                logger.warning(
                    f"Circuit breaker open for {func.__name__}, skipping retries",
                    function=func.__name__,
                    circuit_breaker=self.circuit_breaker.name
                )
                raise Exception(f"Circuit breaker '{self.circuit_breaker.name}' is open")

            # Execute with normal retry logic
            try:
                result = await base_retry(func)(*args, **kwargs)
                self.circuit_breaker.record_success()
                return result
            except Exception as e:
                self.circuit_breaker.record_failure()
                raise

        return wrapper
