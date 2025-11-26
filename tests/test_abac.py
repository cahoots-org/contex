"""Tests for Attribute-Based Access Control (ABAC)"""

import pytest
import pytest_asyncio
from fakeredis import FakeAsyncRedis

from src.core.abac import (
    ABACEngine,
    Policy,
    PolicyCondition,
    PolicyEffect,
    AccessRequest,
    AccessDecision,
    Action,
    ResourceType,
    ConditionOperator,
    create_default_policies,
    init_abac_engine,
    get_abac_engine,
)


class TestABACModels:
    """Test ABAC model validation"""

    def test_action_enum(self):
        """Test Action enum values"""
        assert Action.CREATE == "create"
        assert Action.READ == "read"
        assert Action.UPDATE == "update"
        assert Action.DELETE == "delete"
        assert Action.ADMIN == "admin"

    def test_resource_type_enum(self):
        """Test ResourceType enum values"""
        assert ResourceType.PROJECT == "project"
        assert ResourceType.DATA == "data"
        assert ResourceType.AGENT == "agent"
        assert ResourceType.AUDIT == "audit"

    def test_condition_operator_enum(self):
        """Test ConditionOperator enum values"""
        assert ConditionOperator.EQUALS == "eq"
        assert ConditionOperator.IN == "in"
        assert ConditionOperator.CONTAINS == "contains"
        assert ConditionOperator.MATCHES == "matches"

    def test_policy_condition_model(self):
        """Test PolicyCondition model"""
        condition = PolicyCondition(
            attribute="subject.role",
            operator=ConditionOperator.EQUALS,
            value="admin"
        )
        assert condition.attribute == "subject.role"
        assert condition.operator == ConditionOperator.EQUALS
        assert condition.value == "admin"

    def test_policy_model(self):
        """Test Policy model"""
        policy = Policy(
            policy_id="test_policy",
            name="Test Policy",
            description="A test policy",
            subjects=["*"],
            resources=[ResourceType.DATA],
            actions=[Action.READ],
            effect=PolicyEffect.ALLOW,
            priority=100,
        )
        assert policy.policy_id == "test_policy"
        assert policy.effect == PolicyEffect.ALLOW
        assert policy.is_active is True

    def test_access_request_model(self):
        """Test AccessRequest model"""
        request = AccessRequest(
            subject_id="user123",
            subject_type="api_key",
            subject_role="writer",
            resource_type=ResourceType.DATA,
            resource_id="data123",
            action=Action.READ,
        )
        assert request.subject_id == "user123"
        assert request.action == Action.READ


class TestDefaultPolicies:
    """Test default policy creation"""

    def test_create_default_policies(self):
        """Test default policies are created correctly"""
        policies = create_default_policies()

        assert len(policies) >= 4

        # Check admin policy exists
        admin_policy = next(
            (p for p in policies if p.policy_id == "default_admin_full_access"),
            None
        )
        assert admin_policy is not None
        assert admin_policy.effect == PolicyEffect.ALLOW
        assert admin_policy.priority == 1000

        # Check deny audit policy exists
        deny_audit = next(
            (p for p in policies if p.policy_id == "default_deny_audit_non_admin"),
            None
        )
        assert deny_audit is not None
        assert deny_audit.effect == PolicyEffect.DENY


class TestABACEngine:
    """Test ABAC engine functionality"""

    @pytest_asyncio.fixture
    async def redis(self):
        """Create a fake Redis client"""
        client = FakeAsyncRedis(decode_responses=False)
        yield client
        await client.flushall()
        await client.aclose()

    @pytest_asyncio.fixture
    async def engine(self, redis):
        """Create an ABAC engine"""
        return ABACEngine(redis)

    @pytest.mark.asyncio
    async def test_create_policy(self, engine):
        """Test creating a policy"""
        policy = Policy(
            policy_id="test_create",
            name="Test Create Policy",
            subjects=["*"],
            resources=[ResourceType.DATA],
            actions=[Action.READ],
            effect=PolicyEffect.ALLOW,
        )

        created = await engine.create_policy(policy)

        assert created.policy_id == "test_create"
        assert created.name == "Test Create Policy"

    @pytest.mark.asyncio
    async def test_get_policy(self, engine):
        """Test retrieving a policy"""
        policy = Policy(
            policy_id="test_get",
            name="Test Get Policy",
            subjects=["*"],
            resources=[ResourceType.DATA],
            actions=[Action.READ],
            effect=PolicyEffect.ALLOW,
        )
        await engine.create_policy(policy)

        retrieved = await engine.get_policy("test_get")

        assert retrieved is not None
        assert retrieved.policy_id == "test_get"

    @pytest.mark.asyncio
    async def test_get_nonexistent_policy(self, engine):
        """Test getting a policy that doesn't exist"""
        result = await engine.get_policy("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_policy(self, engine):
        """Test updating a policy"""
        policy = Policy(
            policy_id="test_update",
            name="Original Name",
            subjects=["*"],
            resources=[ResourceType.DATA],
            actions=[Action.READ],
            effect=PolicyEffect.ALLOW,
        )
        await engine.create_policy(policy)

        updated = await engine.update_policy("test_update", name="Updated Name")

        assert updated.name == "Updated Name"
        assert updated.updated_at is not None

    @pytest.mark.asyncio
    async def test_delete_policy(self, engine):
        """Test deleting a policy"""
        policy = Policy(
            policy_id="test_delete",
            name="Test Delete Policy",
            subjects=["*"],
            resources=[ResourceType.DATA],
            actions=[Action.READ],
            effect=PolicyEffect.ALLOW,
        )
        await engine.create_policy(policy)

        result = await engine.delete_policy("test_delete")
        assert result is True

        retrieved = await engine.get_policy("test_delete")
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_list_policies(self, engine):
        """Test listing policies"""
        for i in range(3):
            policy = Policy(
                policy_id=f"test_list_{i}",
                name=f"Test Policy {i}",
                subjects=["*"],
                resources=[ResourceType.DATA],
                actions=[Action.READ],
                effect=PolicyEffect.ALLOW,
            )
            await engine.create_policy(policy)

        policies = await engine.list_policies()

        assert len(policies) >= 3


class TestABACEvaluation:
    """Test ABAC policy evaluation"""

    @pytest_asyncio.fixture
    async def redis(self):
        """Create a fake Redis client"""
        client = FakeAsyncRedis(decode_responses=False)
        yield client
        await client.flushall()
        await client.aclose()

    @pytest_asyncio.fixture
    async def engine(self, redis):
        """Create an ABAC engine"""
        return ABACEngine(redis)

    @pytest.mark.asyncio
    async def test_evaluate_allow(self, engine):
        """Test evaluation resulting in allow"""
        # Create allow policy
        policy = Policy(
            policy_id="allow_read",
            name="Allow Read",
            subjects=["*"],
            resources=[ResourceType.DATA],
            actions=[Action.READ],
            effect=PolicyEffect.ALLOW,
        )
        await engine.create_policy(policy)

        request = AccessRequest(
            subject_id="user1",
            subject_type="api_key",
            resource_type=ResourceType.DATA,
            action=Action.READ,
        )

        decision = await engine.evaluate(request)

        assert decision.allowed is True
        assert "allow_read" in decision.matched_policies

    @pytest.mark.asyncio
    async def test_evaluate_deny(self, engine):
        """Test evaluation resulting in deny"""
        # Create deny policy
        policy = Policy(
            policy_id="deny_delete",
            name="Deny Delete",
            subjects=["*"],
            resources=[ResourceType.DATA],
            actions=[Action.DELETE],
            effect=PolicyEffect.DENY,
        )
        await engine.create_policy(policy)

        request = AccessRequest(
            subject_id="user1",
            subject_type="api_key",
            resource_type=ResourceType.DATA,
            action=Action.DELETE,
        )

        decision = await engine.evaluate(request)

        assert decision.allowed is False

    @pytest.mark.asyncio
    async def test_evaluate_deny_override(self, engine):
        """Test that deny policies override allow policies"""
        # Create allow policy
        allow_policy = Policy(
            policy_id="allow_all",
            name="Allow All",
            subjects=["*"],
            resources=[ResourceType.DATA],
            actions=[Action.DELETE],
            effect=PolicyEffect.ALLOW,
            priority=100,
        )
        await engine.create_policy(allow_policy)

        # Create deny policy with higher priority
        deny_policy = Policy(
            policy_id="deny_delete",
            name="Deny Delete",
            subjects=["*"],
            resources=[ResourceType.DATA],
            actions=[Action.DELETE],
            effect=PolicyEffect.DENY,
            priority=200,
        )
        await engine.create_policy(deny_policy)

        request = AccessRequest(
            subject_id="user1",
            subject_type="api_key",
            resource_type=ResourceType.DATA,
            action=Action.DELETE,
        )

        decision = await engine.evaluate(request)

        # Deny should override allow
        assert decision.allowed is False

    @pytest.mark.asyncio
    async def test_evaluate_no_matching_policy(self, engine):
        """Test evaluation with no matching policies (default deny)"""
        request = AccessRequest(
            subject_id="user1",
            subject_type="api_key",
            resource_type=ResourceType.DATA,
            action=Action.ADMIN,
        )

        decision = await engine.evaluate(request)

        assert decision.allowed is False
        assert "default deny" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_evaluate_with_condition_equals(self, engine):
        """Test evaluation with equals condition"""
        policy = Policy(
            policy_id="admin_only",
            name="Admin Only",
            subjects=["*"],
            resources=[ResourceType.CONFIG],
            actions=[Action.UPDATE],
            conditions=[
                PolicyCondition(
                    attribute="subject.role",
                    operator=ConditionOperator.EQUALS,
                    value="admin"
                )
            ],
            effect=PolicyEffect.ALLOW,
        )
        await engine.create_policy(policy)

        # Admin request should be allowed
        admin_request = AccessRequest(
            subject_id="user1",
            subject_type="api_key",
            subject_role="admin",
            resource_type=ResourceType.CONFIG,
            action=Action.UPDATE,
        )
        admin_decision = await engine.evaluate(admin_request)
        assert admin_decision.allowed is True

        # Non-admin request should be denied
        user_request = AccessRequest(
            subject_id="user2",
            subject_type="api_key",
            subject_role="reader",
            resource_type=ResourceType.CONFIG,
            action=Action.UPDATE,
        )
        user_decision = await engine.evaluate(user_request)
        assert user_decision.allowed is False

    @pytest.mark.asyncio
    async def test_evaluate_with_condition_in(self, engine):
        """Test evaluation with IN condition"""
        policy = Policy(
            policy_id="writers_can_edit",
            name="Writers Can Edit",
            subjects=["*"],
            resources=[ResourceType.DATA],
            actions=[Action.UPDATE],
            conditions=[
                PolicyCondition(
                    attribute="subject.role",
                    operator=ConditionOperator.IN,
                    value=["admin", "writer"]
                )
            ],
            effect=PolicyEffect.ALLOW,
        )
        await engine.create_policy(policy)

        # Writer should be allowed
        writer_request = AccessRequest(
            subject_id="user1",
            subject_type="api_key",
            subject_role="writer",
            resource_type=ResourceType.DATA,
            action=Action.UPDATE,
        )
        assert (await engine.evaluate(writer_request)).allowed is True

        # Reader should be denied
        reader_request = AccessRequest(
            subject_id="user2",
            subject_type="api_key",
            subject_role="reader",
            resource_type=ResourceType.DATA,
            action=Action.UPDATE,
        )
        assert (await engine.evaluate(reader_request)).allowed is False

    @pytest.mark.asyncio
    async def test_evaluate_with_tenant_isolation(self, engine):
        """Test tenant isolation in policy evaluation"""
        policy = Policy(
            policy_id="tenant_a_policy",
            name="Tenant A Policy",
            subjects=["*"],
            resources=[ResourceType.DATA],
            actions=[Action.READ],
            effect=PolicyEffect.ALLOW,
            tenant_id="tenant_a",
        )
        await engine.create_policy(policy)

        # Request from tenant A should match
        tenant_a_request = AccessRequest(
            subject_id="user1",
            subject_type="api_key",
            subject_tenant="tenant_a",
            resource_type=ResourceType.DATA,
            action=Action.READ,
        )
        assert (await engine.evaluate(tenant_a_request)).allowed is True

        # Request from tenant B should not match tenant-specific policy
        tenant_b_request = AccessRequest(
            subject_id="user2",
            subject_type="api_key",
            subject_tenant="tenant_b",
            resource_type=ResourceType.DATA,
            action=Action.READ,
        )
        # No matching policy = default deny
        assert (await engine.evaluate(tenant_b_request)).allowed is False


class TestABACConditionOperators:
    """Test individual condition operators"""

    @pytest_asyncio.fixture
    async def redis(self):
        """Create a fake Redis client"""
        client = FakeAsyncRedis(decode_responses=False)
        yield client
        await client.flushall()
        await client.aclose()

    @pytest_asyncio.fixture
    async def engine(self, redis):
        """Create an ABAC engine"""
        return ABACEngine(redis)

    @pytest.mark.asyncio
    async def test_condition_not_equals(self, engine):
        """Test NOT_EQUALS condition"""
        policy = Policy(
            policy_id="not_reader",
            name="Not Reader",
            subjects=["*"],
            resources=[ResourceType.DATA],
            actions=[Action.DELETE],
            conditions=[
                PolicyCondition(
                    attribute="subject.role",
                    operator=ConditionOperator.NOT_EQUALS,
                    value="reader"
                )
            ],
            effect=PolicyEffect.ALLOW,
        )
        await engine.create_policy(policy)

        # Admin should be allowed (not reader)
        admin_request = AccessRequest(
            subject_id="user1",
            subject_type="api_key",
            subject_role="admin",
            resource_type=ResourceType.DATA,
            action=Action.DELETE,
        )
        assert (await engine.evaluate(admin_request)).allowed is True

        # Reader should be denied
        reader_request = AccessRequest(
            subject_id="user2",
            subject_type="api_key",
            subject_role="reader",
            resource_type=ResourceType.DATA,
            action=Action.DELETE,
        )
        assert (await engine.evaluate(reader_request)).allowed is False

    @pytest.mark.asyncio
    async def test_condition_exists(self, engine):
        """Test EXISTS condition"""
        policy = Policy(
            policy_id="has_project",
            name="Has Project",
            subjects=["*"],
            resources=[ResourceType.DATA],
            actions=[Action.READ],
            conditions=[
                PolicyCondition(
                    attribute="resource.project",
                    operator=ConditionOperator.EXISTS,
                )
            ],
            effect=PolicyEffect.ALLOW,
        )
        await engine.create_policy(policy)

        # Request with project should be allowed
        with_project = AccessRequest(
            subject_id="user1",
            subject_type="api_key",
            resource_type=ResourceType.DATA,
            resource_project="proj123",
            action=Action.READ,
        )
        assert (await engine.evaluate(with_project)).allowed is True

        # Request without project should be denied
        without_project = AccessRequest(
            subject_id="user1",
            subject_type="api_key",
            resource_type=ResourceType.DATA,
            action=Action.READ,
        )
        assert (await engine.evaluate(without_project)).allowed is False

    @pytest.mark.asyncio
    async def test_condition_starts_with(self, engine):
        """Test STARTS_WITH condition"""
        policy = Policy(
            policy_id="prod_only",
            name="Production Only",
            subjects=["*"],
            resources=[ResourceType.DATA],
            actions=[Action.READ],
            conditions=[
                PolicyCondition(
                    attribute="resource.project",
                    operator=ConditionOperator.STARTS_WITH,
                    value="prod_"
                )
            ],
            effect=PolicyEffect.ALLOW,
        )
        await engine.create_policy(policy)

        # Production project should be allowed
        prod_request = AccessRequest(
            subject_id="user1",
            subject_type="api_key",
            resource_type=ResourceType.DATA,
            resource_project="prod_api",
            action=Action.READ,
        )
        assert (await engine.evaluate(prod_request)).allowed is True

        # Dev project should be denied
        dev_request = AccessRequest(
            subject_id="user1",
            subject_type="api_key",
            resource_type=ResourceType.DATA,
            resource_project="dev_api",
            action=Action.READ,
        )
        assert (await engine.evaluate(dev_request)).allowed is False


class TestABACGlobalInstance:
    """Test global instance management"""

    @pytest_asyncio.fixture
    async def redis(self):
        """Create a fake Redis client"""
        client = FakeAsyncRedis(decode_responses=False)
        yield client
        await client.flushall()
        await client.aclose()

    @pytest.mark.asyncio
    async def test_init_abac_engine(self, redis):
        """Test initializing global ABAC engine"""
        engine = await init_abac_engine(redis, load_defaults=False)

        assert engine is not None
        assert get_abac_engine() is engine

    @pytest.mark.asyncio
    async def test_init_with_defaults(self, redis):
        """Test initializing with default policies"""
        engine = await init_abac_engine(redis, load_defaults=True)

        policies = await engine.list_policies()
        assert len(policies) >= 4  # Should have default policies
