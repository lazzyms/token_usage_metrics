"""Minimal example - just 3 lines to get started."""

import asyncio
from token_usage_metrics import TokenUsageClient
from token_usage_metrics.config import Settings


async def main() -> None:
    # Initialize client with explicit Settings
    settings = Settings(backend="redis", redis_url="redis://localhost:6379/0")
    client = await TokenUsageClient.from_settings(settings)

    # Log token usage
    await client.log("my_app", "chat", input_tokens=100, output_tokens=50)

    # Query events
    events, _ = await client.query(project="my_app")
    print(f"Found {len(events)} events")

    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
