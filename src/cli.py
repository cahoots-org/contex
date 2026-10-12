"""Admin CLI: python -m src.cli keys create|list|revoke."""
import argparse
import asyncio

from sqlalchemy import select

from src.core.auth import create_api_key, revoke_api_key
from src.core.database import DatabaseManager, init_database
from src.core.db_models import APIKey, APIKeyRole
from src.core.rbac import Role, assign_role
from src.core.tenant import DEFAULT_TENANT_ID, TenantManager


async def create_key(db: DatabaseManager, name: str, role: Role, tenant: str, projects: list[str]) -> str:
    """Create a key with a role, tenant and optional project scope; return the raw key."""
    await TenantManager(db).ensure_tenant(tenant)
    raw_key, key = await create_api_key(db, name, tenant_id=tenant)
    await assign_role(db, key.key_id, role, projects=projects)
    return raw_key


async def list_keys(db: DatabaseManager) -> list[tuple]:
    """Return (key_id, name, prefix, tenant, role, projects) for every key."""
    async with db.session() as session:
        rows = await session.execute(
            select(APIKey, APIKeyRole)
            .outerjoin(APIKeyRole, APIKeyRole.key_id == APIKey.key_id)
            .order_by(APIKey.created_at)
        )
        return [
            (k.key_id, k.name, k.prefix, k.tenant_id,
             r.role if r else "-", ",".join(r.projects) if r and r.projects else "*")
            for k, r in rows
        ]


async def _run(args: argparse.Namespace) -> int:
    db = await init_database()
    try:
        await db.migrate_to_head()
        if args.action == "create":
            raw_key = await create_key(db, args.name, Role(args.role), args.tenant, args.project)
            print(raw_key)
        elif args.action == "list":
            for row in await list_keys(db):
                print("\t".join(row))
        elif not await revoke_api_key(db, args.key_id):
            print(f"No key with id {args.key_id}")
            return 1
        return 0
    finally:
        await db.disconnect()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.cli")
    keys = parser.add_subparsers(dest="command", required=True).add_parser(
        "keys", help="Manage API keys"
    ).add_subparsers(dest="action", required=True)

    create = keys.add_parser("create", help="Create a key and print it once")
    create.add_argument("--name", required=True)
    create.add_argument("--role", required=True, choices=[r.value for r in Role])
    create.add_argument("--tenant", default=DEFAULT_TENANT_ID)
    create.add_argument("--project", action="append", default=[],
                        help="Limit the key to this project (repeatable; default all)")

    keys.add_parser("list", help="List keys (id, name, prefix, tenant, role, projects)")

    revoke = keys.add_parser("revoke", help="Delete a key")
    revoke.add_argument("key_id")

    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
