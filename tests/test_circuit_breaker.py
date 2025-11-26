"""Tests for Circuit Breaker"""

import pytest
import time

from src.core.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    CircuitBreakerConfig,
    CircuitBreakerOpen,
    CircuitBreakerRegistry,
    get_circuit_breaker,
    get_all_circuit_breakers,
)


class TestCircuitBreakerStates:
    """Test circuit breaker state transitions"""

    def test_initial_state_is_closed(self):
        """Test circuit breaker starts closed"""
        cb = CircuitBreaker(name="test")
        assert cb.state == CircuitState.CLOSED

    def test_state_enum_values(self):
        """Test CircuitState enum values"""
        assert CircuitState.CLOSED == "closed"
        assert CircuitState.OPEN == "open"
        assert CircuitState.HALF_OPEN == "half_open"


class TestCircuitBreakerConfig:
    """Test CircuitBreakerConfig validation"""

    def test_default_config(self):
        """Test default configuration values"""
        config = CircuitBreakerConfig()

        assert config.failure_threshold == 5
        assert config.success_threshold == 2
        assert config.timeout == 60

    def test_custom_config(self):
        """Test custom configuration"""
        config = CircuitBreakerConfig(
            failure_threshold=3,
            success_threshold=1,
            timeout=30,
        )

        assert config.failure_threshold == 3
        assert config.success_threshold == 1
        assert config.timeout == 30

    def test_config_validation_failure_threshold(self):
        """Test configuration validates failure_threshold"""
        with pytest.raises(ValueError):
            CircuitBreakerConfig(failure_threshold=0)

    def test_config_validation_success_threshold(self):
        """Test configuration validates success_threshold"""
        with pytest.raises(ValueError):
            CircuitBreakerConfig(success_threshold=0)

    def test_config_validation_timeout(self):
        """Test configuration validates timeout"""
        with pytest.raises(ValueError):
            CircuitBreakerConfig(timeout=0)


class TestCircuitBreaker:
    """Test CircuitBreaker functionality"""

    @pytest.fixture
    def circuit_breaker(self):
        """Create a circuit breaker with short timeouts for testing"""
        config = CircuitBreakerConfig(
            failure_threshold=3,
            success_threshold=2,
            timeout=1,  # 1 second for fast testing
        )
        return CircuitBreaker(name="test_breaker", config=config)

    def test_can_execute_when_closed(self, circuit_breaker):
        """Test can execute when circuit is closed"""
        assert circuit_breaker.can_execute() is True
        assert circuit_breaker.state == CircuitState.CLOSED

    def test_failure_increments_count(self, circuit_breaker):
        """Test failures increment failure count"""
        circuit_breaker.record_failure()

        assert circuit_breaker.failure_count == 1
        assert circuit_breaker.state == CircuitState.CLOSED

    def test_opens_after_threshold(self, circuit_breaker):
        """Test circuit opens after failure threshold"""
        # Fail 3 times to reach threshold
        for _ in range(3):
            circuit_breaker.record_failure()

        assert circuit_breaker.state == CircuitState.OPEN
        assert circuit_breaker.failure_count == 3

    def test_rejects_when_open(self, circuit_breaker):
        """Test open circuit rejects execution"""
        # Open the circuit
        for _ in range(3):
            circuit_breaker.record_failure()

        assert circuit_breaker.state == CircuitState.OPEN
        assert circuit_breaker.can_execute() is False

    def test_transitions_to_half_open_after_timeout(self, circuit_breaker):
        """Test circuit transitions to half-open after timeout"""
        # Open the circuit
        for _ in range(3):
            circuit_breaker.record_failure()

        assert circuit_breaker.state == CircuitState.OPEN

        # Wait for timeout
        time.sleep(1.1)

        # Should be allowed to try again
        assert circuit_breaker.can_execute() is True
        assert circuit_breaker.state == CircuitState.HALF_OPEN

    def test_closes_after_half_open_success(self, circuit_breaker):
        """Test circuit closes after successful half-open calls"""
        # Open the circuit
        for _ in range(3):
            circuit_breaker.record_failure()

        # Wait for timeout
        time.sleep(1.1)

        # Transition to half-open
        circuit_breaker.can_execute()
        assert circuit_breaker.state == CircuitState.HALF_OPEN

        # Successful calls in half-open should close circuit
        for _ in range(2):
            circuit_breaker.record_success()

        assert circuit_breaker.state == CircuitState.CLOSED
        assert circuit_breaker.failure_count == 0

    def test_reopens_on_half_open_failure(self, circuit_breaker):
        """Test circuit reopens if half-open call fails"""
        # Open the circuit
        for _ in range(3):
            circuit_breaker.record_failure()

        # Wait for timeout
        time.sleep(1.1)

        # Transition to half-open
        circuit_breaker.can_execute()

        # Fail in half-open should reopen
        circuit_breaker.record_failure()

        assert circuit_breaker.state == CircuitState.OPEN

    def test_success_resets_failure_count_in_closed(self, circuit_breaker):
        """Test successful call resets failure count when closed"""
        # Fail twice (under threshold)
        circuit_breaker.record_failure()
        circuit_breaker.record_failure()

        assert circuit_breaker.failure_count == 2

        # Success should reset
        circuit_breaker.record_success()

        assert circuit_breaker.failure_count == 0

    def test_get_state(self, circuit_breaker):
        """Test getting circuit breaker state"""
        state = circuit_breaker.get_state()

        assert state["name"] == "test_breaker"
        assert state["state"] == "closed"
        assert state["failure_count"] == 0
        assert state["success_count"] == 0
        assert "config" in state
        assert state["config"]["failure_threshold"] == 3

    def test_manual_reset(self, circuit_breaker):
        """Test manual circuit reset"""
        # Open the circuit
        for _ in range(3):
            circuit_breaker.record_failure()

        assert circuit_breaker.state == CircuitState.OPEN

        # Manual reset
        circuit_breaker.reset()

        assert circuit_breaker.state == CircuitState.CLOSED
        assert circuit_breaker.failure_count == 0


class TestCircuitBreakerContextManager:
    """Test circuit breaker context manager"""

    def test_context_manager_success(self):
        """Test context manager with successful execution"""
        config = CircuitBreakerConfig(failure_threshold=3, timeout=1)
        cb = CircuitBreaker(name="ctx_test", config=config)

        with cb:
            # Simulate successful operation
            result = "success"

        assert cb.failure_count == 0

    def test_context_manager_failure(self):
        """Test context manager with failed execution"""
        config = CircuitBreakerConfig(failure_threshold=3, timeout=1)
        cb = CircuitBreaker(name="ctx_test", config=config)

        with pytest.raises(ValueError):
            with cb:
                raise ValueError("test error")

        assert cb.failure_count == 1

    def test_context_manager_raises_when_open(self):
        """Test context manager raises CircuitBreakerOpen when circuit is open"""
        config = CircuitBreakerConfig(failure_threshold=2, timeout=60)
        cb = CircuitBreaker(name="ctx_test", config=config)

        # Open the circuit
        for _ in range(2):
            try:
                with cb:
                    raise ValueError("error")
            except ValueError:
                pass

        assert cb.state == CircuitState.OPEN

        # Next attempt should raise CircuitBreakerOpen
        with pytest.raises(CircuitBreakerOpen):
            with cb:
                pass


class TestCircuitBreakerRegistry:
    """Test CircuitBreakerRegistry"""

    def test_get_breaker_creates_new(self):
        """Test getting breaker creates new one if not exists"""
        registry = CircuitBreakerRegistry()

        breaker = registry.get_breaker("new_breaker")

        assert breaker is not None
        assert breaker.name == "new_breaker"

    def test_get_breaker_returns_existing(self):
        """Test getting breaker returns existing one"""
        registry = CircuitBreakerRegistry()

        breaker1 = registry.get_breaker("same_breaker")
        breaker2 = registry.get_breaker("same_breaker")

        assert breaker1 is breaker2

    def test_get_breaker_with_config(self):
        """Test getting breaker with custom config"""
        registry = CircuitBreakerRegistry()
        config = CircuitBreakerConfig(failure_threshold=10, timeout=120)

        breaker = registry.get_breaker("custom_breaker", config=config)

        assert breaker.config.failure_threshold == 10
        assert breaker.config.timeout == 120

    def test_get_all_states(self):
        """Test getting all breaker states"""
        registry = CircuitBreakerRegistry()

        registry.get_breaker("breaker_1")
        registry.get_breaker("breaker_2")

        states = registry.get_all_states()

        assert "breaker_1" in states
        assert "breaker_2" in states
        assert states["breaker_1"]["state"] == "closed"

    def test_reset_all(self):
        """Test resetting all breakers"""
        registry = CircuitBreakerRegistry()
        config = CircuitBreakerConfig(failure_threshold=2, timeout=60)

        breaker1 = registry.get_breaker("breaker_1", config)
        breaker2 = registry.get_breaker("breaker_2", config)

        # Open both breakers
        for _ in range(2):
            breaker1.record_failure()
            breaker2.record_failure()

        assert breaker1.state == CircuitState.OPEN
        assert breaker2.state == CircuitState.OPEN

        # Reset all
        registry.reset_all()

        assert breaker1.state == CircuitState.CLOSED
        assert breaker2.state == CircuitState.CLOSED


class TestGlobalFunctions:
    """Test global circuit breaker functions"""

    def test_get_circuit_breaker(self):
        """Test global get_circuit_breaker function"""
        breaker = get_circuit_breaker("global_test")

        assert breaker is not None
        assert breaker.name == "global_test"

    def test_get_circuit_breaker_with_config(self):
        """Test get_circuit_breaker with custom config"""
        config = CircuitBreakerConfig(failure_threshold=7, timeout=90)
        breaker = get_circuit_breaker("global_custom", config=config)

        assert breaker.config.failure_threshold == 7

    def test_get_all_circuit_breakers(self):
        """Test getting all circuit breaker states"""
        # Create some breakers
        get_circuit_breaker("all_test_1")
        get_circuit_breaker("all_test_2")

        states = get_all_circuit_breakers()

        assert "all_test_1" in states
        assert "all_test_2" in states
