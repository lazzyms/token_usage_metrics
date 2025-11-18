"""Example usage of token-usage-metrics package - Simplified SDK style."""

import asyncio
from datetime import datetime, timedelta, timezone

from token_usage_metrics import TokenUsageClient


async def main() -> None:
    """Demonstrate core features of the token usage metrics SDK."""
    print("Logging events...")

    # Initialize with connection string - auto-starts the client
    client = await TokenUsageClient.init("redis://localhost:6379/0")

    try:
        # Log usage events
        await client.log(
            "chatbot_app",
            "chat",
            input_tokens=120,
            output_tokens=80,
            metadata={"model": "gpt-4"},
        )
        await client.log("chatbot_app", "chat", input_tokens=95, output_tokens=65)
        await client.log("search_app", "embedding", input_tokens=50, output_tokens=0)

        # Flush events to backend
        await client.flush(timeout=5.0)
        await asyncio.sleep(0.3)  # Brief wait for writes

        # Query events
        print("\nQuerying events...")
        events, _ = await client.query(project="chatbot_app")
        print(f"Found {len(events)} events")
        for e in events[:3]:
            print(f"  {e.request_type}: {e.input_tokens}→{e.output_tokens} tokens")

        # Daily aggregates
        print("\nDaily aggregates...")
        time_from = datetime.now(timezone.utc) - timedelta(days=1)
        time_to = datetime.now(timezone.utc) + timedelta(days=1)
        daily = await client.aggregate(
            group_by="day", time_from=time_from, time_to=time_to
        )
        for bucket in daily:
            total = bucket.metrics.get("sum_total", 0)
            count = bucket.metrics.get("count_requests", 0)
            print(f"  {bucket.start.date()}: {total} tokens, {count} requests")

        # Project summaries
        print("\nProject summaries...")
        projects = await client.aggregate(group_by="project")
        for p in projects:
            keys = p.group_keys or {}
            name = keys.get("project_name", "unknown")
            total = p.metrics.get("sum_total", 0)
            count = p.metrics.get("count_requests", 0)
            print(f"  {name}: {total} tokens, {count} requests")

        # Request type summaries
        print("\nRequest type summaries...")
        types = await client.aggregate(group_by="type")
        for t in types:
            keys = t.group_keys or {}
            req_type = keys.get("request_type", "unknown")
            total = t.metrics.get("sum_total", 0)
            print(f"  {req_type}: {total} tokens")

        print("\n✓ Done!")

    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
