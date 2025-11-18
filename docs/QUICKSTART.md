# Quick Start Guide

## Installation

```bash
# Install with all backends
uv add token-usage-metrics[all]

# Or just Redis (recommended for getting started)
uv add token-usage-metrics[redis]
```

## Start Redis (Docker)

```bash
docker run -d -p 6379:6379 redis:7-alpine
```

## Basic Usage

```python
import asyncio
from token_usage_metrics import TokenUsageClient, UsageEvent, Settings

async def main():
    settings = Settings(backend="redis", redis_url="redis://localhost:6379/0")

    async with TokenUsageClient(settings) as client:
        # Log usage
        event = UsageEvent(
            project_name="my_app",
            request_type="chat",
            input_tokens=100,
            output_tokens=50,
        )

        await client.log(event)
        await client.flush()

        # Query usage
        from token_usage_metrics import UsageFilter

        events, _ = await client.fetch_raw(UsageFilter(project_name="my_app"))
        print(f"Found {len(events)} events")

        # Get aggregates
        from token_usage_metrics import AggregateSpec

        daily = await client.summary_by_day(AggregateSpec(), UsageFilter())
        for bucket in daily:
            print(f"{bucket.start.date()}: {bucket.metrics}")

asyncio.run(main())
```

## Run the Example

```bash
# Make sure Redis is running, then:
uv run python examples/basic_usage.py
```

## Run Tests

```bash
uv run pytest tests/test_models.py -v
```

## Package Structure

```
token-usage-metrics/
├── token_usage_metrics/     # Main package
│   ├── client.py           # TokenUsageClient API
│   ├── models.py           # Data models
│   ├── config.py           # Settings
│   ├── queue.py            # Async buffering
│   ├── logging.py          # Structured logging
│   ├── errors.py           # Custom exceptions
│   └── backends/           # Storage backends
│       ├── base.py         # Abstract interface
│       ├── redis.py        # Redis implementation
│       ├── postgres.py     # PostgreSQL implementation
│       └── mongodb.py      # MongoDB implementation
├── tests/                   # Test suite
├── examples/                # Usage examples
├── docs/                    # Documentation
├── README.md                # Main documentation
└── pyproject.toml          # Package configuration
```

## Key Features

✅ **Async & Non-blocking**: Background flushing, never blocks your app  
✅ **Multi-backend**: Redis, Postgres, MongoDB with unified API  
✅ **Lifetime retention**: No enforced TTL, explicit deletion only  
✅ **Rich queries**: Raw events, daily/project/type aggregates, time-series  
✅ **Production-ready**: Circuit breakers, retries, structured logging

## Next Steps

1. Configure via environment variables (see [README.md](../README.md))
2. Choose your backend (Redis for speed, Postgres for SQL, Mongo for flexibility)
3. Implement in your LLM application
4. Build dashboards using the aggregation APIs

See [README.md](../README.md) for complete documentation and [API.md](API.md) for detailed API reference!
