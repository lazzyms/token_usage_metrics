"""Example usage of token-usage-metrics package."""

import asyncio
from datetime import datetime, timedelta, timezone

from token_usage_metrics import (
    AggregateSpec,
    DeleteOptions,
    Settings,
    TokenUsageClient,
    UsageEvent,
    UsageFilter,
)


async def main():
    """Demonstrate the token usage metrics package."""
    print("=== Token Usage Metrics Demo ===\n")

    # Configure with Redis backend
    settings = Settings(
        backend="redis",
        redis_url="redis://localhost:6379/0",
        buffer_size=100,
        flush_interval=0.5,
    )

    async with TokenUsageClient(settings) as client:
        print("✓ Client started and connected to Redis\n")

        # 1. Log some usage events
        print("1. Logging usage events...")
        events = [
            UsageEvent(
                project_name="chatbot_app",
                request_type="chat",
                input_tokens=120,
                output_tokens=80,
                metadata={"model": "gpt-4", "user": "alice"},
            ),
            UsageEvent(
                project_name="chatbot_app",
                request_type="chat",
                input_tokens=95,
                output_tokens=65,
                metadata={"model": "gpt-4", "user": "bob"},
            ),
            UsageEvent(
                project_name="search_app",
                request_type="embedding",
                input_tokens=50,
                output_tokens=0,
                metadata={"model": "text-embedding-ada-002"},
            ),
            UsageEvent(
                project_name="chatbot_app",
                request_type="completion",
                input_tokens=200,
                output_tokens=150,
                metadata={"model": "gpt-3.5-turbo", "user": "charlie"},
            ),
        ]

        for event in events:
            await client.log(event)

        print(f"   Logged {len(events)} events (async, non-blocking)")

        # 2. Flush to ensure events are written
        print("\n2. Flushing pending events...")
        flushed = await client.flush(timeout=5.0)
        print(f"   Flushed {flushed} events to backend")

        # Wait a moment for backend writes
        await asyncio.sleep(0.5)

        # 3. Fetch raw events
        print("\n3. Fetching raw events for 'chatbot_app'...")
        filters = UsageFilter(
            project_name="chatbot_app",
            limit=10,
        )

        raw_events, cursor = await client.fetch_raw(filters)
        print(f"   Found {len(raw_events)} events")

        for event in raw_events:
            print(
                f"   - {event.request_type}: {event.input_tokens}→{event.output_tokens} tokens"
            )

        # 4. Get daily summary
        print("\n4. Getting daily summary...")
        spec = AggregateSpec()
        filters = UsageFilter(
            time_from=datetime.now(timezone.utc) - timedelta(days=1),
            time_to=datetime.now(timezone.utc) + timedelta(days=1),
        )

        daily_buckets = await client.summary_by_day(spec, filters)
        print(f"   Daily aggregates: {len(daily_buckets)} day(s)")

        for bucket in daily_buckets:
            print(f"   - {bucket.start.date()}:")
            print(f"     Total tokens: {bucket.metrics.get('sum_total', 0)}")
            print(f"     Requests: {bucket.metrics.get('count_requests', 0)}")

        # 5. Get project summary
        print("\n5. Getting project-level summary...")
        project_summaries = await client.summary_by_project(spec, UsageFilter())

        for summary in project_summaries:
            project = summary.group_keys.get("project_name", "unknown")
            total = summary.metrics.get("sum_total", 0)
            count = summary.metrics.get("count_requests", 0)
            avg = summary.metrics.get("avg_total_per_request", 0)

            print(f"   - {project}:")
            print(f"     Total: {total} tokens, Requests: {count}, Avg: {avg:.1f}")

        # 6. Get request type summary
        print("\n6. Getting request type summary...")
        type_summaries = await client.summary_by_request_type(spec, UsageFilter())

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

        # 9. Simulate deletion
        print("\n9. Simulating project deletion...")
        delete_options = DeleteOptions(
            project_name="search_app",
            simulate=True,
        )

        result = await client.delete_project(delete_options)
        print(
            f"   Would delete {result.events_deleted} events, "
            f"{result.aggregates_deleted} aggregates"
        )

        print("\n✓ Demo completed successfully!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nDemo interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
