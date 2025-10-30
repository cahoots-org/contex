"""
Example: Publishing project data to Contex

This example shows how to publish various types of project data
that agents can subscribe to.
"""

import asyncio
import httpx


CONTEX_URL = "http://localhost:8001"
PROJECT_ID = "my-app"


async def publish_project_data():
    """Publish various types of project data"""

    async with httpx.AsyncClient() as client:
        # 1. Publish coding standards
        print("Publishing coding standards...")
        response = await client.post(
            f"{CONTEX_URL}/data/publish",
            json={
                "project_id": PROJECT_ID,
                "data_key": "coding_standards",
                "data": {
                    "style": "PEP 8",
                    "max_line_length": 100,
                    "quotes": "double",
                    "imports": "sorted alphabetically",
                    "docstrings": "Google style",
                    "type_hints": "required for public APIs"
                }
            }
        )
        print(f"  ✓ Status: {response.status_code}")

        # 2. Publish API documentation
        print("\nPublishing API endpoints...")
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
                        }
                    ]
                }
            }
        )
        print(f"  ✓ Status: {response.status_code}")

        # 3. Publish database schema
        print("\nPublishing database schema...")
        response = await client.post(
            f"{CONTEX_URL}/data/publish",
            json={
                "project_id": PROJECT_ID,
                "data_key": "database_schema",
                "data": {
                    "database": "PostgreSQL 15",
                    "connection_pool": {
                        "min": 5,
                        "max": 20
                    },
                    "tables": {
                        "users": {
                            "id": "uuid PRIMARY KEY DEFAULT gen_random_uuid()",
                            "email": "varchar(255) UNIQUE NOT NULL",
                            "username": "varchar(50) UNIQUE NOT NULL",
                            "created_at": "timestamp DEFAULT CURRENT_TIMESTAMP",
                            "updated_at": "timestamp DEFAULT CURRENT_TIMESTAMP"
                        },
                        "posts": {
                            "id": "uuid PRIMARY KEY DEFAULT gen_random_uuid()",
                            "user_id": "uuid REFERENCES users(id) ON DELETE CASCADE",
                            "title": "varchar(200) NOT NULL",
                            "content": "text NOT NULL",
                            "status": "varchar(20) DEFAULT 'draft'",
                            "created_at": "timestamp DEFAULT CURRENT_TIMESTAMP",
                            "updated_at": "timestamp DEFAULT CURRENT_TIMESTAMP"
                        }
                    },
                    "indexes": [
                        "CREATE INDEX idx_posts_user_id ON posts(user_id)",
                        "CREATE INDEX idx_posts_status ON posts(status)",
                        "CREATE INDEX idx_posts_created_at ON posts(created_at)"
                    ]
                }
            }
        )
        print(f"  ✓ Status: {response.status_code}")

        # 4. Publish testing requirements
        print("\nPublishing testing requirements...")
        response = await client.post(
            f"{CONTEX_URL}/data/publish",
            json={
                "project_id": PROJECT_ID,
                "data_key": "testing_standards",
                "data": {
                    "framework": "pytest",
                    "coverage_minimum": 90,
                    "test_structure": {
                        "unit_tests": "tests/unit/",
                        "integration_tests": "tests/integration/",
                        "e2e_tests": "tests/e2e/"
                    },
                    "conventions": {
                        "naming": "test_<function_name>_<scenario>",
                        "fixtures": "Use pytest fixtures for setup/teardown",
                        "mocking": "Use unittest.mock or pytest-mock",
                        "assertions": "Use pytest assertions, not unittest"
                    },
                    "requirements": [
                        "All functions must have tests",
                        "Test edge cases and error conditions",
                        "Use descriptive test names",
                        "One assertion concept per test"
                    ]
                }
            }
        )
        print(f"  ✓ Status: {response.status_code}")

        # 5. Publish deployment configuration
        print("\nPublishing deployment configuration...")
        response = await client.post(
            f"{CONTEX_URL}/data/publish",
            json={
                "project_id": PROJECT_ID,
                "data_key": "deployment_config",
                "data": {
                    "platform": "AWS ECS",
                    "regions": ["us-east-1", "eu-west-1"],
                    "environments": {
                        "development": {
                            "url": "https://dev.example.com",
                            "replicas": 1,
                            "resources": {"memory": "512MB", "cpu": "0.5"}
                        },
                        "staging": {
                            "url": "https://staging.example.com",
                            "replicas": 2,
                            "resources": {"memory": "1GB", "cpu": "1.0"}
                        },
                        "production": {
                            "url": "https://api.example.com",
                            "replicas": 5,
                            "resources": {"memory": "2GB", "cpu": "2.0"}
                        }
                    },
                    "ci_cd": {
                        "tool": "GitHub Actions",
                        "deploy_on": "merge to main",
                        "smoke_tests": "required before promotion"
                    }
                }
            }
        )
        print(f"  ✓ Status: {response.status_code}")

    print("\n✓ All project data published successfully!")
    print(f"\nView published data:")
    print(f"  curl {CONTEX_URL}/projects/{PROJECT_ID}/data")


if __name__ == "__main__":
    print("=" * 60)
    print("Publishing Project Data to Contex")
    print("=" * 60)
    print()

    try:
        asyncio.run(publish_project_data())
    except httpx.ConnectError:
        print("\n✗ Error: Could not connect to Contex")
        print(f"  Make sure Contex is running at {CONTEX_URL}")
        print("  Run: docker compose up -d")
    except Exception as e:
        print(f"\n✗ Error: {e}")
