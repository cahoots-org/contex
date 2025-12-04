"""
Basic usage example for Contex Python SDK
"""

import asyncio
from contex import ContexAsyncClient


async def main():
    # Initialize client
    async with ContexAsyncClient(
        url="http://localhost:8001",
        api_key="your-api-key-here"  # Replace with your actual API key
    ) as client:
        
        # 1. Publish some data
        print("📤 Publishing data...")
        await client.publish(
            project_id="demo-app",
            data_key="coding_standards",
            data={
                "language": "Python",
                "style_guide": "PEP 8",
                "max_line_length": 100,
                "quotes": "double",
                "indentation": 4
            }
        )
        print("✅ Data published successfully")
        
        # 2. Publish more data
        await client.publish(
            project_id="demo-app",
            data_key="testing_requirements",
            data={
                "framework": "pytest",
                "coverage_minimum": 80,
                "test_structure": "tests/ directory",
                "naming": "test_*.py"
            }
        )
        
        # 3. Register an agent
        print("\n🤖 Registering agent...")
        response = await client.register_agent(
            agent_id="code-reviewer",
            project_id="demo-app",
            data_needs=[
                "coding standards and style guidelines",
                "testing requirements and best practices"
            ]
        )
        
        print(f"✅ Agent registered!")
        print(f"  • Matched needs: {response.matched_needs}")
        print(f"  • Notification channel: {response.notification_channel}")
        
        # 4. Query for data
        print("\n🔍 Querying for data...")
        results = await client.query(
            project_id="demo-app",
            query="python code style and formatting rules"
        )
        
        print(f"Found {results.total} results:")
        for result in results.results:
            print(f"  • {result.data_key}: {result.similarity_score:.2f}")
            print(f"    {result.data}")
        
        # 5. Check health
        print("\n❤️  Checking health...")
        health = await client.health()
        print(f"Status: {health['status']}")
        
        # 6. Check rate limit
        rate_limit = await client.rate_limit_status()
        print(f"\n📊 Rate limit: {rate_limit.remaining}/{rate_limit.limit} remaining")


if __name__ == "__main__":
    asyncio.run(main())
