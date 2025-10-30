"""
Example: Updating project data

This example shows how to update existing data.
Agents subscribed to this data will receive notifications.
"""

import asyncio
import httpx


CONTEX_URL = "http://localhost:8001"
PROJECT_ID = "my-app"


async def update_coding_standards():
    """Update coding standards - agents will be notified"""

    print("=" * 60)
    print("Updating Coding Standards")
    print("=" * 60)
    print()

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{CONTEX_URL}/data/publish",
                json={
                    "project_id": PROJECT_ID,
                    "data_key": "coding_standards",
                    "data": {
                        "style": "PEP 8",
                        "max_line_length": 120,  # Changed from 100!
                        "quotes": "double",
                        "imports": "sorted alphabetically with isort",
                        "docstrings": "Google style",
                        "type_hints": "required for all public APIs",
                        "formatting": "automated with black",  # New!
                        "linting": "flake8 + mypy"  # New!
                    }
                }
            )

            if response.status_code == 200:
                result = response.json()
                print("✓ Coding standards updated successfully!")
                print(f"  Project: {result['project_id']}")
                print(f"  Data key: {result['data_key']}")
                print(f"  Sequence: {result['sequence']}")
                print()
                print("📢 All subscribed agents have been notified!")
            else:
                print(f"✗ Update failed: {response.status_code}")
                print(f"  {response.text}")

        except httpx.ConnectError:
            print(f"✗ Could not connect to Contex at {CONTEX_URL}")
            print("  Make sure Contex is running: docker compose up -d")


async def update_api_endpoints():
    """Update API endpoints - add a new endpoint"""

    print()
    print("=" * 60)
    print("Updating API Endpoints (Adding New Endpoint)")
    print("=" * 60)
    print()

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{CONTEX_URL}/data/publish",
                json={
                    "project_id": PROJECT_ID,
                    "data_key": "api_endpoints",
                    "data": {
                        "base_url": "https://api.example.com/v1",
                        "authentication": {
                            "type": "Bearer token",
                            "header": "Authorization",
                            "format": "Bearer {token}"
                        },
                        "endpoints": [
                            {
                                "path": "/users",
                                "methods": ["GET", "POST"],
                                "description": "User management"
                            },
                            {
                                "path": "/users/{id}",
                                "methods": ["GET", "PUT", "DELETE"],
                                "description": "Single user operations"
                            },
                            {
                                "path": "/posts",
                                "methods": ["GET", "POST"],
                                "description": "Blog post management"
                            },
                            {
                                "path": "/posts/{id}",
                                "methods": ["GET", "PUT", "DELETE"],
                                "description": "Single post operations"
                            },
                            # NEW ENDPOINT!
                            {
                                "path": "/comments",
                                "methods": ["GET", "POST"],
                                "description": "Comment management"
                            },
                            {
                                "path": "/comments/{id}",
                                "methods": ["GET", "PUT", "DELETE"],
                                "description": "Single comment operations"
                            }
                        ]
                    }
                }
            )

            if response.status_code == 200:
                result = response.json()
                print("✓ API endpoints updated successfully!")
                print(f"  Added: /comments endpoints")
                print(f"  Sequence: {result['sequence']}")
                print()
                print("📢 All subscribed agents have been notified!")
            else:
                print(f"✗ Update failed: {response.status_code}")

        except httpx.ConnectError:
            print(f"✗ Could not connect to Contex at {CONTEX_URL}")


async def main():
    """Run all updates"""
    await update_coding_standards()
    await asyncio.sleep(1)  # Small delay for readability
    await update_api_endpoints()

    print()
    print("=" * 60)
    print("💡 Tip: Run agent_redis.py in another terminal")
    print("   to see it receive these updates in real-time!")
    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"\n✗ Error: {e}")
