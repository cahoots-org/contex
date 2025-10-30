"""
Example: Agent receiving updates via webhooks

This example shows how to create an agent that receives updates via HTTP webhooks
instead of Redis pub/sub. This is perfect for:
- Serverless functions (AWS Lambda, Cloud Functions, etc.)
- Microservices that don't want Redis dependencies
- Any HTTP-capable service

The agent runs a simple HTTP server that receives webhook POSTs from Contex.
"""

import asyncio
import hmac
import hashlib
import json
from fastapi import FastAPI, Request, HTTPException
import httpx
import uvicorn


# Configuration
CONTEX_URL = "http://localhost:8001"
PROJECT_ID = "my-app"
AGENT_ID = "webhook-demo-agent"
WEBHOOK_SECRET = "my-super-secret-key"  # Share this with Contex
WEBHOOK_PORT = 8002


# Create FastAPI app to receive webhooks
app = FastAPI(title="Webhook Agent Example")


def verify_signature(payload: bytes, signature_header: str) -> bool:
    """Verify HMAC signature from Contex"""
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_sig = signature_header.split("sha256=")[1]

    computed_sig = hmac.new(
        WEBHOOK_SECRET.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected_sig, computed_sig)


@app.post("/webhook")
async def handle_webhook(request: Request):
    """
    Receive webhook notifications from Contex.

    Contex will POST to this endpoint when:
    1. Agent is registered (initial_context)
    2. Data matching agent's needs is published (data_update)
    3. Events occur in the project (event)
    """
    # Get raw body for signature verification
    body = await request.body()

    # Verify signature if provided
    signature = request.headers.get("X-Contex-Signature")
    if signature:
        if not verify_signature(body, signature):
            print("⚠ Invalid signature!")
            raise HTTPException(status_code=401, detail="Invalid signature")
    else:
        print("⚠ No signature provided (not recommended for production)")

    # Parse payload
    payload = json.loads(body)

    # Handle different event types
    event_type = payload.get("type")

    if event_type == "initial_context":
        print("\n" + "=" * 60)
        print("📥 Received initial context")
        print("=" * 60)

        context = payload.get("context", {})
        for need, matches in context.items():
            print(f"\nNeed: '{need}'")
            print(f"Matches: {len(matches)}")
            for match in matches:
                print(f"  - {match['data_key']} (similarity: {match['similarity']:.3f})")

    elif event_type == "data_update":
        print("\n" + "=" * 60)
        print("🔄 Data update received")
        print("=" * 60)

        data_key = payload.get("data_key")
        sequence = payload.get("sequence")
        data = payload.get("data")

        print(f"Data Key: {data_key}")
        print(f"Sequence: {sequence}")
        print(f"Data: {json.dumps(data, indent=2)}")

        # Here you would process the update
        # For example:
        # - Update internal cache
        # - Trigger reprocessing
        # - Send notifications
        # - etc.

    elif event_type == "event":
        print("\n" + "=" * 60)
        print("📊 Event received")
        print("=" * 60)

        event_type_name = payload.get("event_type")
        sequence = payload.get("sequence")
        data = payload.get("data")

        print(f"Event Type: {event_type_name}")
        print(f"Sequence: {sequence}")
        print(f"Data: {json.dumps(data, indent=2)}")

    else:
        print(f"⚠ Unknown event type: {event_type}")

    return {"status": "received"}


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}


async def register_agent():
    """Register this agent with Contex using webhooks"""
    print("\n" + "=" * 60)
    print("Registering agent with Contex...")
    print("=" * 60)

    webhook_url = f"http://host.docker.internal:{WEBHOOK_PORT}/webhook"

    registration = {
        "agent_id": AGENT_ID,
        "project_id": PROJECT_ID,
        "data_needs": [
            "API documentation and endpoints",
            "authentication configuration",
            "database connection settings"
        ],
        "notification_method": "webhook",
        "webhook_url": webhook_url,
        "webhook_secret": WEBHOOK_SECRET
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{CONTEX_URL}/agents/register",
                json=registration,
                timeout=10.0
            )

            if response.status_code == 200:
                result = response.json()
                print(f"✓ Agent registered successfully!")
                print(f"  Agent ID: {result['agent_id']}")
                print(f"  Project: {result['project_id']}")
                print(f"  Matched needs: {result['matched_needs']}")
                print(f"  Caught up events: {result['caught_up_events']}")
                print(f"\nWebhook endpoint: {webhook_url}")
                print(f"Waiting for updates...")
            else:
                print(f"✗ Registration failed: {response.status_code}")
                print(f"  {response.text}")

        except Exception as e:
            print(f"✗ Failed to register: {e}")
            print(f"\nMake sure Contex is running at {CONTEX_URL}")
            raise


async def publish_sample_data():
    """Publish some sample data to trigger updates"""
    await asyncio.sleep(2)  # Wait for agent to be ready

    print("\n" + "=" * 60)
    print("Publishing sample data...")
    print("=" * 60)

    sample_data = [
        {
            "data_key": "api_endpoints",
            "data": {
                "endpoints": [
                    {"path": "/api/v1/users", "methods": ["GET", "POST"]},
                    {"path": "/api/v1/posts", "methods": ["GET", "POST", "PUT", "DELETE"]}
                ]
            }
        },
        {
            "data_key": "auth_config",
            "data": {
                "method": "JWT",
                "issuer": "https://auth.example.com",
                "audience": "api.example.com"
            }
        }
    ]

    async with httpx.AsyncClient() as client:
        for item in sample_data:
            try:
                response = await client.post(
                    f"{CONTEX_URL}/data/publish",
                    json={
                        "project_id": PROJECT_ID,
                        "data_key": item["data_key"],
                        "data": item["data"]
                    }
                )

                if response.status_code == 200:
                    print(f"✓ Published: {item['data_key']}")
                else:
                    print(f"✗ Failed to publish {item['data_key']}")

            except Exception as e:
                print(f"✗ Error publishing {item['data_key']}: {e}")

            await asyncio.sleep(0.5)


async def main():
    """Main entry point"""
    print("\n" + "=" * 60)
    print("Webhook Agent Example")
    print("=" * 60)
    print(f"\nThis agent will:")
    print(f"1. Start a webhook server on port {WEBHOOK_PORT}")
    print(f"2. Register with Contex at {CONTEX_URL}")
    print(f"3. Receive updates via HTTP POST")
    print(f"\nPress Ctrl+C to stop\n")

    # Register agent first
    await register_agent()

    # Start publishing sample data in background
    asyncio.create_task(publish_sample_data())

    # Run the webhook server
    # Note: In production, you'd run this with a proper ASGI server
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=WEBHOOK_PORT,
        log_level="info"
    )
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nShutting down...")
