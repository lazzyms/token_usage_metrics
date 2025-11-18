"""Minimal example showing the simplified SDK-style API."""

import asyncio
from token_usage_metrics import TokenUsageClient


async def main():
    """Minimal 3-line example."""
    # 1. Initialize (auto-starts)
    client = await TokenUsageClient.init("redis://localhost:6379/0")

    try:
        # 2. Log usage
        await client.log("my_app", "chat", input_tokens=100, output_tokens=50)

        # 3. Query data
        events, _ = await client.query(project="my_app")
        print(f"✓ Found {len(events)} events")

    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
