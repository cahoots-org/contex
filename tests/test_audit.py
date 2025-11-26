"""Tests for Audit Logging"""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, UTC
from fakeredis import FakeAsyncRedis

from src.core.audit import (
    AuditLogger,
    AuditEvent,
    AuditEventType,
    AuditEventSeverity,
    init_audit_logger,
    get_audit_logger,
    audit_log,
)


class TestAuditModels:
    """Test audit model validation"""

    def test_audit_event_type_enum(self):
        """Test AuditEventType enum values"""
        assert AuditEventType.AUTH_LOGIN_SUCCESS == "auth.login.success"
        assert AuditEventType.AUTH_LOGIN_FAILURE == "auth.login.failure"
        assert AuditEventType.DATA_PUBLISHED == "data.published"
        assert AuditEventType.AUTH_API_KEY_CREATED == "auth.api_key.created"

    def test_audit_event_severity_enum(self):
        """Test AuditEventSeverity enum values"""
        assert AuditEventSeverity.INFO == "info"
        assert AuditEventSeverity.WARNING == "warning"
        assert AuditEventSeverity.ERROR == "error"
        assert AuditEventSeverity.CRITICAL == "critical"

    def test_audit_event_model(self):
        """Test AuditEvent model"""
        event = AuditEvent(
            event_type=AuditEventType.DATA_PUBLISHED,
            action="Published data 'config'",
            actor_id="key_123",
            actor_type="api_key",
            actor_ip="192.168.1.1",
            resource_type="data",
            resource_id="config",
            project_id="proj1",
            result="success",
            severity=AuditEventSeverity.INFO,
        )

        assert event.event_id is not None
        assert event.event_type == AuditEventType.DATA_PUBLISHED
        assert event.severity == AuditEventSeverity.INFO

    def test_audit_event_with_details(self):
        """Test AuditEvent with custom details"""
        event = AuditEvent(
            event_type=AuditEventType.CONFIG_UPDATED,
            action="Updated config",
            details={"changed_fields": ["name", "value"], "old_value": "x"},
        )

        assert event.details["changed_fields"] == ["name", "value"]

    def test_audit_event_default_values(self):
        """Test AuditEvent default values"""
        event = AuditEvent(
            event_type=AuditEventType.DATA_PUBLISHED,
            action="Test action",
        )

        assert event.event_id is not None
        assert event.timestamp is not None
        assert event.severity == AuditEventSeverity.INFO
        assert event.result == "success"
        assert event.details == {}


class TestAuditLogger:
    """Test AuditLogger functionality"""

    @pytest_asyncio.fixture
    async def redis(self):
        """Create a fake Redis client"""
        client = FakeAsyncRedis(decode_responses=False)
        yield client
        await client.flushall()
        await client.aclose()

    @pytest_asyncio.fixture
    async def logger(self, redis):
        """Create an audit logger"""
        return AuditLogger(redis, retention_days=30)

    @pytest.mark.asyncio
    async def test_log_event(self, logger):
        """Test logging an audit event"""
        event = AuditEvent(
            event_type=AuditEventType.DATA_PUBLISHED,
            action="Published config data",
            actor_id="key_123",
            actor_type="api_key",
            resource_type="data",
            resource_id="config",
        )

        event_id = await logger.log(event)

        assert event_id is not None
        assert event_id == event.event_id

    @pytest.mark.asyncio
    async def test_log_with_all_fields(self, logger):
        """Test logging event with all fields"""
        event = AuditEvent(
            event_type=AuditEventType.AUTH_LOGIN_FAILURE,
            action="Authentication failed",
            actor_id="unknown",
            actor_type="api_key",
            actor_ip="10.0.0.1",
            resource_type="auth",
            resource_id="login",
            project_id="proj1",
            tenant_id="tenant1",
            result="failure",
            severity=AuditEventSeverity.WARNING,
            details={"reason": "invalid_key"},
            request_id="req_123",
            endpoint="/api/v1/data",
            method="POST",
        )

        event_id = await logger.log(event)

        assert event_id is not None

    @pytest.mark.asyncio
    async def test_get_event(self, logger):
        """Test retrieving a logged event"""
        event = AuditEvent(
            event_type=AuditEventType.AUTH_API_KEY_CREATED,
            action="Created API key",
            actor_id="admin_key",
            actor_type="api_key",
            resource_type="api_key",
            resource_id="new_key_123",
        )

        event_id = await logger.log(event)
        retrieved = await logger.get_event(event_id)

        assert retrieved is not None
        assert retrieved.event_type == AuditEventType.AUTH_API_KEY_CREATED
        assert retrieved.resource_id == "new_key_123"

    @pytest.mark.asyncio
    async def test_get_nonexistent_event(self, logger):
        """Test getting event that doesn't exist"""
        result = await logger.get_event("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_query_by_event_type(self, logger):
        """Test querying events by type"""
        # Log different event types
        await logger.log(AuditEvent(
            event_type=AuditEventType.AUTH_LOGIN_SUCCESS,
            action="Auth success 1",
        ))
        await logger.log(AuditEvent(
            event_type=AuditEventType.AUTH_LOGIN_FAILURE,
            action="Auth failure 1",
        ))
        await logger.log(AuditEvent(
            event_type=AuditEventType.AUTH_LOGIN_SUCCESS,
            action="Auth success 2",
        ))

        results = await logger.query_events(
            event_type=AuditEventType.AUTH_LOGIN_SUCCESS
        )

        assert len(results) == 2
        for event in results:
            assert event.event_type == AuditEventType.AUTH_LOGIN_SUCCESS

    @pytest.mark.asyncio
    async def test_query_by_actor(self, logger):
        """Test querying events by actor"""
        await logger.log(AuditEvent(
            event_type=AuditEventType.DATA_PUBLISHED,
            action="Action 1",
            actor_id="user_a",
        ))
        await logger.log(AuditEvent(
            event_type=AuditEventType.DATA_PUBLISHED,
            action="Action 2",
            actor_id="user_b",
        ))
        await logger.log(AuditEvent(
            event_type=AuditEventType.DATA_PUBLISHED,
            action="Action 3",
            actor_id="user_a",
        ))

        results = await logger.query_events(actor_id="user_a")

        assert len(results) == 2
        for event in results:
            assert event.actor_id == "user_a"

    @pytest.mark.asyncio
    async def test_query_by_tenant(self, logger):
        """Test querying events by tenant"""
        await logger.log(AuditEvent(
            event_type=AuditEventType.DATA_PUBLISHED,
            action="Action 1",
            tenant_id="tenant_a",
        ))
        await logger.log(AuditEvent(
            event_type=AuditEventType.DATA_PUBLISHED,
            action="Action 2",
            tenant_id="tenant_b",
        ))

        results = await logger.query_events(tenant_id="tenant_a")

        assert len(results) == 1
        assert results[0].tenant_id == "tenant_a"

    @pytest.mark.asyncio
    async def test_query_limit(self, logger):
        """Test query result limiting"""
        # Log 10 events
        for i in range(10):
            await logger.log(AuditEvent(
                event_type=AuditEventType.DATA_PUBLISHED,
                action=f"Action {i}",
            ))

        results = await logger.query_events(limit=5)

        assert len(results) <= 5

    @pytest.mark.asyncio
    async def test_export_events(self, logger):
        """Test exporting events"""
        for i in range(3):
            await logger.log(AuditEvent(
                event_type=AuditEventType.DATA_PUBLISHED,
                action=f"Action {i}",
                tenant_id="export_test",
            ))

        exported = await logger.export_events(tenant_id="export_test")

        assert len(exported) == 3
        assert all(isinstance(e, dict) for e in exported)


class TestAuditEventTypes:
    """Test specific audit event type scenarios"""

    @pytest_asyncio.fixture
    async def redis(self):
        """Create a fake Redis client"""
        client = FakeAsyncRedis(decode_responses=False)
        yield client
        await client.flushall()
        await client.aclose()

    @pytest_asyncio.fixture
    async def logger(self, redis):
        """Create an audit logger"""
        return AuditLogger(redis)

    @pytest.mark.asyncio
    async def test_log_auth_events(self, logger):
        """Test logging authentication events"""
        # Success
        success_event = AuditEvent(
            event_type=AuditEventType.AUTH_LOGIN_SUCCESS,
            action="API key authentication successful",
            actor_id="key_123",
            actor_type="api_key",
            actor_ip="192.168.1.100",
        )
        success_id = await logger.log(success_event)

        # Failure
        failure_event = AuditEvent(
            event_type=AuditEventType.AUTH_LOGIN_FAILURE,
            action="Invalid API key",
            actor_ip="10.0.0.50",
            severity=AuditEventSeverity.WARNING,
            details={"reason": "key_not_found"},
        )
        failure_id = await logger.log(failure_event)

        success = await logger.get_event(success_id)
        failure = await logger.get_event(failure_id)

        assert success.event_type == AuditEventType.AUTH_LOGIN_SUCCESS
        assert failure.event_type == AuditEventType.AUTH_LOGIN_FAILURE
        assert failure.severity == AuditEventSeverity.WARNING

    @pytest.mark.asyncio
    async def test_log_data_events(self, logger):
        """Test logging data operation events"""
        event = AuditEvent(
            event_type=AuditEventType.DATA_PUBLISHED,
            action="Published configuration data",
            actor_id="key_456",
            actor_type="api_key",
            resource_type="data",
            resource_id="app_config",
            project_id="project_123",
            details={
                "data_format": "json",
                "size_bytes": 1024,
            },
        )
        event_id = await logger.log(event)

        retrieved = await logger.get_event(event_id)

        assert retrieved.project_id == "project_123"
        assert retrieved.details["size_bytes"] == 1024

    @pytest.mark.asyncio
    async def test_log_security_events(self, logger):
        """Test logging security-related events"""
        # Rate limiting
        rate_event = AuditEvent(
            event_type=AuditEventType.SECURITY_RATE_LIMITED,
            action="Request rate limited",
            actor_id="key_789",
            actor_ip="203.0.113.50",
            severity=AuditEventSeverity.WARNING,
            details={"limit": 100, "window": 60},
        )
        rate_id = await logger.log(rate_event)

        # Permission denied
        perm_event = AuditEvent(
            event_type=AuditEventType.AUTHZ_PERMISSION_DENIED,
            action="Access denied to resource",
            actor_id="key_reader",
            resource_type="admin",
            severity=AuditEventSeverity.WARNING,
            details={"required_role": "admin", "actual_role": "reader"},
        )
        perm_id = await logger.log(perm_event)

        rate = await logger.get_event(rate_id)
        perm = await logger.get_event(perm_id)

        assert rate.event_type == AuditEventType.SECURITY_RATE_LIMITED
        assert perm.details["required_role"] == "admin"

    @pytest.mark.asyncio
    async def test_log_tenant_events(self, logger):
        """Test logging tenant operation events"""
        event = AuditEvent(
            event_type=AuditEventType.TENANT_CREATED,
            action="Created new tenant",
            actor_id="system",
            actor_type="system",
            resource_type="tenant",
            resource_id="new_tenant_123",
            details={"plan": "pro", "owner": "admin@example.com"},
        )
        event_id = await logger.log(event)

        retrieved = await logger.get_event(event_id)

        assert retrieved.event_type == AuditEventType.TENANT_CREATED
        assert retrieved.details["plan"] == "pro"


class TestAuditGlobalInstance:
    """Test global instance management"""

    @pytest_asyncio.fixture
    async def redis(self):
        """Create a fake Redis client"""
        client = FakeAsyncRedis(decode_responses=False)
        yield client
        await client.flushall()
        await client.aclose()

    def test_init_audit_logger(self, redis):
        """Test initializing global audit logger"""
        logger = init_audit_logger(redis, retention_days=60)

        assert logger is not None
        assert logger.retention_days == 60
        assert get_audit_logger() is logger


class TestAuditConvenienceFunction:
    """Test the audit_log convenience function"""

    @pytest_asyncio.fixture
    async def redis(self):
        """Create a fake Redis client"""
        client = FakeAsyncRedis(decode_responses=False)
        yield client
        await client.flushall()
        await client.aclose()

    @pytest.mark.asyncio
    async def test_audit_log_function(self, redis):
        """Test the audit_log convenience function"""
        # Initialize global logger
        init_audit_logger(redis)

        # Use convenience function
        event_id = await audit_log(
            event_type=AuditEventType.AGENT_REGISTERED,
            action="Agent registered",
            resource_type="agent",
            resource_id="agent_123",
        )

        # Verify it was logged
        logger = get_audit_logger()
        event = await logger.get_event(event_id)

        assert event is not None
        assert event.resource_id == "agent_123"

    @pytest.mark.asyncio
    async def test_audit_log_no_logger(self, redis):
        """Test audit_log when logger not initialized"""
        # Reset global logger
        import src.core.audit
        src.core.audit._audit_logger = None

        # Should not raise, returns None
        result = await audit_log(
            event_type=AuditEventType.DATA_PUBLISHED,
            action="Test action",
        )

        assert result is None


class TestAuditEventDiff:
    """Test audit events with before/after diffs"""

    @pytest_asyncio.fixture
    async def redis(self):
        """Create a fake Redis client"""
        client = FakeAsyncRedis(decode_responses=False)
        yield client
        await client.flushall()
        await client.aclose()

    @pytest_asyncio.fixture
    async def logger(self, redis):
        """Create an audit logger"""
        return AuditLogger(redis)

    @pytest.mark.asyncio
    async def test_log_update_with_diff(self, logger):
        """Test logging update with before/after"""
        event = AuditEvent(
            event_type=AuditEventType.DATA_UPDATED,
            action="Updated user settings",
            resource_type="settings",
            resource_id="user_settings",
            before={"theme": "light", "notifications": True},
            after={"theme": "dark", "notifications": True},
        )

        event_id = await logger.log(event)
        retrieved = await logger.get_event(event_id)

        assert retrieved.before["theme"] == "light"
        assert retrieved.after["theme"] == "dark"
