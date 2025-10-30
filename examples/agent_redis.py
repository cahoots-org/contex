"""
Example: Agent receiving updates via Redis pub/sub

This example shows how to create an agent that subscribes to
context updates using Redis pub/sub (the default method).
"""

import asyncio
import json
import httpx
import redis.asyncio as redis


CONTEX_URL = "http://localhost:8001"
PROJECT_ID = "my-app"
AGENT_ID = "code-generator"


async def run_agent():
    """Register an agent and listen for updates via Redis"""

    print("=" * 60)
    print(f"Starting Agent: {AGENT_ID}")
    print("=" * 60)
    print()

    # Step 1: Register the agent with its data needs
    print("Registering agent with Contex...")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{CONTEX_URL}/agents/register",
                json={
                    "agent_id": AGENT_ID,
                    "project_id": PROJECT_ID,
                    "data_needs": [
                        "code style guidelines and formatting rules",
                        "API endpoints and authentication methods",
                        "database tables and schema information",
                        "testing requirements and conventions"
                    ],
                    "notification_method": "redis"
                }
            )

            if response.status_code != 200:
                print(f"✗ Registration failed: {response.text}")
                return

            result = response.json()
            print(f"✓ Agent registered successfully!")
            print(f"  Agent ID: {result['agent_id']}")
            print(f"  Project: {result['project_id']}")
            print(f"  Notification channel: {result['notification_channel']}")
            print()
            print("Matched needs:")
            for need, count in result['matched_needs'].items():
                print(f"  - '{need}': {count} matches")

            channel = result['notification_channel']

        except httpx.ConnectError:
            print(f"✗ Could not connect to Contex at {CONTEX_URL}")
            print("  Make sure Contex is running: docker compose up -d")
            return

    # Step 2: Subscribe to updates via Redis
    print()
    print("=" * 60)
    print(f"👂 Listening for updates on {channel}")
    print("=" * 60)
    print("(Press Ctrl+C to stop)")
    print()

    try:
        r = await redis.from_url("redis://localhost:6379")
        pubsub = r.pubsub()
        await pubsub.subscribe(channel)

        async for message in pubsub.listen():
            if message["type"] == "message":
                update = json.loads(message["data"])
                handle_update(update)

    except redis.ConnectionError:
        print("\n✗ Could not connect to Redis at localhost:6379")
        print("  Make sure Redis is running")
    finally:
        await r.aclose()


def handle_update(update: dict):
    """Handle different types of updates"""

    if update["type"] == "initial_context":
        print("📥 Received Initial Context")
        print("=" * 60)
        print()

        context = update.get("context", {})
        for need, matches in context.items():
            print(f"Need: '{need}'")
            if matches:
                for match in matches:
                    print(f"  ✓ {match['data_key']}")
                    print(f"    Similarity: {match['similarity']:.2f}")
                    print(f"    Data: {json.dumps(match['data'], indent=6)}")
                    print()
            else:
                print("  (no matches)")
                print()

    elif update["type"] == "data_update":
        print("🔄 Data Update Received")
        print("=" * 60)
        print()
        print(f"Data Key: {update['data_key']}")
        print(f"Sequence: {update['sequence']}")
        print(f"Data:")
        print(json.dumps(update['data'], indent=2))
        print()

        # This is where you would typically:
        # - Update your internal cache
        # - Trigger reprocessing with new data
        # - Notify other components
        # - Log the change

        print("💡 Agent would now process this update...")
        print()

    elif update["type"] == "event":
        print("📊 Event Received")
        print("=" * 60)
        print()
        print(f"Event Type: {update.get('event_type')}")
        print(f"Sequence: {update['sequence']}")
        print(f"Data: {json.dumps(update['data'], indent=2)}")
        print()


if __name__ == "__main__":
    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        print("\n\n" + "=" * 60)
        print("👋 Agent stopped by user")
        print("=" * 60)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
