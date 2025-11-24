"""
Error handling example for Contex Python SDK
"""

import asyncio
from contex import (
    ContexAsyncClient,
    AuthenticationError,
    RateLimitError,
    ValidationError,
    NotFoundError,
    ServerError,
    NetworkError,
)


async def main():
    client = ContexAsyncClient(
        url="http://localhost:8001",
        api_key="your-api-key-here"
    )
    
    # Example 1: Handle authentication errors
    print("Example 1: Authentication Error")
    try:
        bad_client = ContexAsyncClient(url="http://localhost:8001", api_key="invalid-key")
        await bad_client.publish(
            project_id="test",
            data_key="test",
            data={}
        )
    except AuthenticationError as e:
        print(f"❌ Authentication failed: {e}")
    
    # Example 2: Handle validation errors
    print("\nExample 2: Validation Error")
    try:
        await client.publish(
            project_id="",  # Empty project_id will fail validation
            data_key="test",
            data={}
        )
    except ValidationError as e:
        print(f"❌ Validation error: {e}")
    
    # Example 3: Handle rate limiting
    print("\nExample 3: Rate Limit Handling")
    try:
        # Simulate many requests
        for i in range(150):  # More than the limit
            await client.publish(
                project_id="test",
                data_key=f"test-{i}",
                data={"index": i}
            )
    except RateLimitError as e:
        print(f"❌ Rate limited! Retry after {e.retry_after} seconds")
        if e.retry_after:
            print(f"   Waiting {e.retry_after} seconds...")
            await asyncio.sleep(e.retry_after)
            print("   Retrying...")
    
    # Example 4: Handle not found errors
    print("\nExample 4: Not Found Error")
    try:
        await client.unregister_agent("non-existent-agent")
    except NotFoundError as e:
        print(f"❌ Not found: {e}")
    
    # Example 5: Handle network errors
    print("\nExample 5: Network Error")
    try:
        bad_url_client = ContexAsyncClient(
            url="http://localhost:9999",  # Wrong port
            api_key="test"
        )
        await bad_url_client.health()
    except NetworkError as e:
        print(f"❌ Network error: {e}")
    
    # Example 6: Generic error handling
    print("\nExample 6: Generic Error Handling")
    try:
        await client.publish(
            project_id="test",
            data_key="test",
            data={"test": "data"}
        )
        print("✅ Success!")
    except AuthenticationError:
        print("❌ Authentication failed - check your API key")
    except RateLimitError as e:
        print(f"❌ Rate limited - wait {e.retry_after}s")
    except ValidationError as e:
        print(f"❌ Invalid request - {e}")
    except NotFoundError:
        print("❌ Resource not found")
    except ServerError:
        print("❌ Server error - try again later")
    except NetworkError:
        print("❌ Network error - check connection")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
