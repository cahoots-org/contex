"""
Example: Ad-hoc querying of project data

This example demonstrates how to query project data without registering an agent.
Perfect for one-off queries, CLI tools, or interactive exploration.
"""

import asyncio
import httpx


async def main():
    """Demonstrate ad-hoc query functionality"""

    base_url = "http://localhost:8001"
    project_id = "my-app"

    print("=" * 60)
    print("Contex - Ad-hoc Query Example")
    print("=" * 60)
    print()

    # Step 1: Publish some data to the project
    print("[1] Publishing project data...")

    data_sources = [
        {
            "data_key": "api_authentication",
            "data": {
                "method": "OAuth2",
                "provider": "Google",
                "scopes": ["openid", "profile", "email"],
                "redirect_uri": "https://myapp.com/auth/callback"
            }
        },
        {
            "data_key": "database_config",
            "data": {
                "type": "PostgreSQL",
                "host": "localhost",
                "port": 5432,
                "database": "myapp_prod",
                "ssl_mode": "require"
            }
        },
        {
            "data_key": "api_endpoints",
            "data": {
                "endpoints": [
                    {"path": "/api/v1/users", "methods": ["GET", "POST"]},
                    {"path": "/api/v1/users/{id}", "methods": ["GET", "PUT", "DELETE"]},
                    {"path": "/api/v1/posts", "methods": ["GET", "POST"]},
                    {"path": "/api/v1/auth/login", "methods": ["POST"]}
                ]
            }
        },
        {
            "data_key": "coding_standards",
            "data": {
                "style_guide": "PEP 8",
                "max_line_length": 100,
                "formatter": "black",
                "linter": "ruff",
                "type_checking": "mypy"
            }
        }
    ]

    async with httpx.AsyncClient() as client:
        for source in data_sources:
            response = await client.post(
                f"{base_url}/data/publish",
                json={
                    "project_id": project_id,
                    "data_key": source["data_key"],
                    "data": source["data"]
                }
            )
            print(f"  ✓ Published: {source['data_key']}")

    print()

    # Step 2: Perform ad-hoc queries
    print("[2] Querying project data...")
    print()

    queries = [
        "What authentication method are we using?",
        "How do I connect to the database?",
        "What API endpoints are available?",
        "What are the coding standards?",
        "Where is the user management API?"
    ]

    async with httpx.AsyncClient() as client:
        for query_text in queries:
            print(f"Query: \"{query_text}\"")

            response = await client.post(
                f"{base_url}/projects/{project_id}/query",
                json={
                    "query": query_text,
                    "top_k": 3
                }
            )

            result = response.json()

            if result["total_matches"] == 0:
                print("  No matches found")
            else:
                print(f"  Found {result['total_matches']} match(es):")
                for i, match in enumerate(result["matches"], 1):
                    print(f"    {i}. {match['data_key']} (similarity: {match['similarity']:.3f})")
                    # Show a preview of the data
                    data_preview = str(match['data'])[:80]
                    if len(str(match['data'])) > 80:
                        data_preview += "..."
                    print(f"       Data: {data_preview}")

            print()

    print("=" * 60)
    print("Example complete!")
    print("=" * 60)
    print()
    print("Try it yourself:")
    print(f"  curl -X POST {base_url}/projects/{project_id}/query \\")
    print('    -H "Content-Type: application/json" \\')
    print('    -d \'{"query": "your question here", "top_k": 5}\'')
    print()


if __name__ == "__main__":
    asyncio.run(main())
