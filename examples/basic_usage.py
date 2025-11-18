"""Example usage of token-usage-metrics package - Simplified SDK style."""

import asyncio
from datetime import datetime, timedelta, timezone

from token_usage_metrics import TokenUsageClient


async def main():
    """Demonstrate the simplified token usage metrics SDK."""
    print("=== Token Usage Metrics Demo (Simplified SDK) ===\n")

    # Initialize with connection string - auto-starts the client
    client = await TokenUsageClient.init("redis://localhost:6379/0")

    try:
        print("✓ Client initialized and connected to Redis\n")

        # 1. Log usage events with direct parameters (no event objects needed)
        print("1. Logging usage events...")
        await client.log(
            "chatbot_app",
            "chat",
            input_tokens=120,
            output_tokens=80,
            metadata={"model": "gpt-4", "user": "alice"},
        )
        await client.log(
            "chatbot_app",
            "chat",
            input_tokens=95,
            output_tokens=65,
            metadata={"model": "gpt-4", "user": "bob"},
        )
        await client.log(
            "search_app",
            "embedding",
            input_tokens=50,
            output_tokens=0,
            metadata={"model": "text-embedding-ada-002"},
        )
        await client.log(
            "chatbot_app",
            "completion",
            input_tokens=200,
            output_tokens=150,
            metadata={"model": "gpt-3.5-turbo", "user": "charlie"},
        )
        print("   Logged 4 events (async, non-blocking)")

        # 2. Flush to ensure events are written
        print("\n2. Flushing pending events...")
        flushed = await client.flush(timeout=5.0)
        print(f"   Flushed {flushed} events to backend")

        # Wait a moment for backend writes
        await asyncio.sleep(0.5)

        # 3. Query events with simple parameters (no filter objects needed)
        print("\n3. Querying events for 'chatbot_app'...")
        events, cursor = await client.query(project="chatbot_app", limit=10)
        print(f"   Found {len(events)} events")

        for event in events:
            print(
                f"   - {event.request_type}: {event.input_tokens}→{event.output_tokens} tokens"
            )

        # 4. Get daily aggregates with simplified parameters
        print("\n4. Getting daily aggregates...")
        time_from = datetime.now(timezone.utc) - timedelta(days=1)
        time_to = datetime.now(timezone.utc) + timedelta(days=1)

        daily_buckets = await client.aggregate(
            group_by="day", time_from=time_from, time_to=time_to
        )
        print(f"   Daily aggregates: {len(daily_buckets)} day(s)")

        for bucket in daily_buckets:
            print(f"   - {bucket.start.date()}:")
            print(f"     Total tokens: {bucket.metrics.get('sum_total', 0)}")
            print(f"     Requests: {bucket.metrics.get('count_requests', 0)}")

        # 5. Get project-level aggregates
        print("\n5. Getting project-level aggregates...")
        project_summaries = await client.aggregate(group_by="project")

        for summary in project_summaries:
            project = summary.group_keys.get("project_name", "unknown")
            total = summary.metrics.get("sum_total", 0)
            count = summary.metrics.get("count_requests", 0)
            avg = summary.metrics.get("avg_total_per_request", 0)

            print(f"   - {project}:")
            print(f"     Total: {total} tokens, Requests: {count}, Avg: {avg:.1f}")

        # 6. Get request type aggregates
        print("\n6. Getting request type aggregates...")
        type_summaries = await client.aggregate(group_by="type")

        for summary in type_summaries:
            req_type = summary.group_keys.get("request_type", "unknown")
            total = summary.metrics.get("sum_total", 0)
            count = summary.metrics.get("count_requests", 0)

            print(f"   - {req_type}: {total} tokens across {count} requests")

        # 7. Check health
        print("\n7. Checking backend health...")
        health = await client.health_check()
        print(f"   Backend healthy: {health}")

        # 8. Get stats
        print("\n8. Client statistics...")
        stats = client.get_stats()
        print(f"   Queue size: {stats.get('queue_size', 0)}")
        print(f"   Dropped events: {stats.get('dropped_count', 0)}")
        print(f"   Circuit state: {stats.get('circuit_state', 'unknown')}")

        print("\n✓ Demo completed successfully!")

    finally:
        # Clean up
        await client.aclose()


async def alternative_initialization():
    """Show alternative initialization methods."""
    print("\n=== Alternative Initialization Methods ===\n")

    # Method 1: Connection string
    print("1. Using connection string:")
    client1 = await TokenUsageClient.init("redis://localhost:6379/0")
    print("   ✓ Redis client initialized")
    await client1.aclose()

    # Method 2: Individual parameters
    print("\n2. Using individual parameters:")
    client2 = await TokenUsageClient.init(
        backend="postgres",
        host="localhost",
        port=5432,
        username="user",
        password="pass",
        database="token_usage",
    )
    print("   ✓ Postgres client initialized")
    await client2.aclose()

    # Method 3: Connection string with additional config
    print("\n3. With advanced configuration:")
    client3 = await TokenUsageClient.init(
        "redis://localhost:6379/0", buffer_size=500, flush_interval=2.0
    )
    print("   ✓ Redis client with custom buffer settings")
    await client3.aclose()

    print("\n✓ All initialization methods work!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
        asyncio.run(alternative_initialization())
    except KeyboardInterrupt:
        print("\n\nDemo interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
