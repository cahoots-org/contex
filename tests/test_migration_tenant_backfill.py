import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from src.core.db_models import Subscription, APIKey, ServiceAccount, Event
from src.core.subscriptions import SubscriptionService
from src.core.tenant import DEFAULT_TENANT_ID


class _StubMatcher:
    async def match(self, project_id, needs, metadata=None, top_k=None, threshold=None):
        return {n: [{"data_key": "cfg", "similarity": 0.9, "data": {"x": 1}, "description": "auth"}] for n in needs}


@pytest.mark.asyncio
async def test_subscription_without_tenant_id_defaults_to_default(db):
    async with db.session() as session:
        session.add(Subscription(
            subscription_id="sub_test_default",
            project_id="p1",
            needs=["auth"],
        ))
        await session.commit()

    async with db.session() as session:
        row = (await session.execute(
            select(Subscription).where(Subscription.subscription_id == "sub_test_default")
        )).scalar_one()
        assert row.tenant_id == DEFAULT_TENANT_ID


@pytest.mark.asyncio
async def test_subscription_explicit_null_via_raw_sql_raises_integrity_error(db):
    async with db.session() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO subscriptions "
                    "(subscription_id, project_id, tenant_id, needs, bundle, bundle_updated_at, created_at) "
                    "VALUES (:sub_id, :proj_id, NULL, :needs, :bundle, NOW(), NOW())"
                ),
                {
                    "sub_id": "sub_test_null_raw",
                    "proj_id": "p1",
                    "needs": ["auth"],
                    "bundle": "{}",
                },
            )
            await session.flush()


@pytest.mark.asyncio
async def test_api_key_without_tenant_id_defaults_to_default(db):
    async with db.session() as session:
        session.add(APIKey(
            key_id="test_key_1",
            key_hash="testhash1",
            name="test_key",
            prefix="test_",
            scopes=[],
        ))
        await session.commit()

    async with db.session() as session:
        row = (await session.execute(
            select(APIKey).where(APIKey.key_id == "test_key_1")
        )).scalar_one()
        assert row.tenant_id == DEFAULT_TENANT_ID


@pytest.mark.asyncio
async def test_api_key_explicit_null_via_raw_sql_raises_integrity_error(db):
    async with db.session() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO api_keys "
                    "(key_id, key_hash, name, prefix, scopes, tenant_id) "
                    "VALUES (:key_id, :key_hash, :name, :prefix, :scopes, NULL)"
                ),
                {
                    "key_id": "test_key_2_raw",
                    "key_hash": "testhash2_raw",
                    "name": "test_key",
                    "prefix": "test_",
                    "scopes": ["read"],
                },
            )
            await session.flush()


@pytest.mark.asyncio
async def test_service_account_without_tenant_id_defaults_to_default(db):
    async with db.session() as session:
        session.add(ServiceAccount(
            account_id="sa_test_1",
            name="test_sa",
            account_type="service",
        ))
        await session.commit()

    async with db.session() as session:
        row = (await session.execute(
            select(ServiceAccount).where(ServiceAccount.account_id == "sa_test_1")
        )).scalar_one()
        assert row.tenant_id == DEFAULT_TENANT_ID


@pytest.mark.asyncio
async def test_service_account_explicit_null_via_raw_sql_raises_integrity_error(db):
    async with db.session() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO service_accounts "
                    "(account_id, name, account_type, tenant_id, role) "
                    "VALUES (:account_id, :name, :account_type, NULL, :role)"
                ),
                {
                    "account_id": "sa_test_2_raw",
                    "name": "test_sa",
                    "account_type": "service",
                    "role": "readonly",
                },
            )
            await session.flush()


@pytest.mark.asyncio
async def test_event_without_tenant_id_defaults_to_default(db):
    async with db.session() as session:
        session.add(Event(
            project_id="p1",
            event_type="test_event",
            data={"test": "data"},
            sequence=1,
        ))
        await session.commit()

    async with db.session() as session:
        row = (await session.execute(
            select(Event).where(Event.event_type == "test_event")
        )).scalar_one()
        assert row.tenant_id == DEFAULT_TENANT_ID


@pytest.mark.asyncio
async def test_event_explicit_null_via_raw_sql_raises_integrity_error(db):
    async with db.session() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO events "
                    "(project_id, event_type, data, sequence, tenant_id) "
                    "VALUES (:project_id, :event_type, :data, :sequence, NULL)"
                ),
                {
                    "project_id": "p1",
                    "event_type": "test_event_null",
                    "data": '{"test": "data"}',
                    "sequence": 1,
                },
            )
            await session.flush()


@pytest.mark.asyncio
async def test_subscription_service_create_defaults_to_default_tenant(db, redis):
    svc = SubscriptionService(db, _StubMatcher(), redis)
    sub_id = await svc.create("p1", ["auth config"])

    async with db.session() as session:
        row = (await session.execute(
            select(Subscription).where(Subscription.subscription_id == sub_id)
        )).scalar_one()
        assert row.tenant_id == DEFAULT_TENANT_ID


@pytest.mark.asyncio
async def test_backfill_null_cannot_be_inserted_after_migration(db):
    async with db.session() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO subscriptions "
                    "(subscription_id, project_id, tenant_id, needs, bundle, bundle_updated_at, created_at, updated_at) "
                    "VALUES (:sub_id, :proj_id, NULL, :needs, :bundle, NOW(), NOW(), NULL) "
                ),
                {
                    "sub_id": "sub_backfill_test",
                    "proj_id": "p_backfill",
                    "needs": ["auth"],
                    "bundle": "{}",
                },
            )
            await session.flush()
