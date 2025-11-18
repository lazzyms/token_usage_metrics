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

## Basic Usage - Simplified SDK Style

The token-usage-metrics package provides a minimal SDK-style API. Just 3 lines to get started!

```python
import asyncio
from token_usage_metrics import TokenUsageClient

async def main():
    # 1. Initialize with connection string (auto-starts)
    client = await TokenUsageClient.init("redis://localhost:6379/0")

    try:
        # 2. Log usage with direct parameters (no event objects!)
        await client.log("my_app", "chat", input_tokens=100, output_tokens=50)
        await client.flush()

        # 3. Query with simple parameters (no filter objects!)
        events, _ = await client.query(project="my_app")
        print(f"Found {len(events)} events")

        # Get daily aggregates
        daily = await client.aggregate(group_by="day")
        for bucket in daily:
            print(f"{bucket.start.date()}: {bucket.metrics}")

    finally:
        await client.aclose()

asyncio.run(main())
```

## Initialization Methods

### Method 1: Connection String (Recommended)

```python
# Redis
client = await TokenUsageClient.init("redis://localhost:6379/0")

# PostgreSQL
client = await TokenUsageClient.init("postgresql://user:pass@localhost:5432/token_usage")

# MongoDB
client = await TokenUsageClient.init("mongodb://localhost:27017/token_usage")
```

### Method 2: Individual Parameters

```python
# Redis with parameters
client = await TokenUsageClient.init(
    backend="redis",
    host="localhost",
    port=6379,
    database="0"
)

# PostgreSQL with authentication
client = await TokenUsageClient.init(
    backend="postgres",
    host="localhost",
    port=5432,
    username="user",
    password="pass",
    database="token_usage"
)

# MongoDB with authentication
client = await TokenUsageClient.init(
    backend="mongodb",
    host="localhost",
    port=27017,
    username="user",
    password="pass",
    database="token_usage"
)
```

### Method 3: With Advanced Configuration

```python
# Add buffer size, flush interval, and other settings
client = await TokenUsageClient.init(
    "redis://localhost:6379/0",
    buffer_size=500,
    flush_interval=2.0,
    max_retries=5
)
```

## Logging Usage

```python
# Simple logging
await client.log("my_app", "chat", input_tokens=100, output_tokens=50)

# With metadata
await client.log(
    "my_app", "chat",
    input_tokens=100, output_tokens=50,
    metadata={"model": "gpt-4", "user": "alice"}
)

# With request count
await client.log(
    "my_app", "batch_processing",
    input_tokens=5000, output_tokens=2000,
    request_count=10  # Track multiple requests as one event
)
```

## Querying Data

```python
# Get all events for a project
events, cursor = await client.query(project="my_app")

# Filter by request type
events, cursor = await client.query(
    project="my_app",
    request_type="chat"
)

# Time range filtering
from datetime import datetime, timedelta, timezone

time_from = datetime.now(timezone.utc) - timedelta(days=7)
events, cursor = await client.query(
    project="my_app",
    time_from=time_from,
    limit=100
)

# Pagination
events, cursor = await client.query(project="my_app", limit=50)
if cursor:
    more_events, next_cursor = await client.query(
        project="my_app",
        cursor=cursor,
        limit=50
    )
```

## Aggregations

```python
# Daily aggregates
daily = await client.aggregate(group_by="day")

# Project-level aggregates
projects = await client.aggregate(group_by="project")

# Request type aggregates
types = await client.aggregate(group_by="type")

# Overall aggregate (no grouping)
overall = await client.aggregate()

# With custom metrics
daily = await client.aggregate(
    group_by="day",
    metrics=["sum_total", "sum_input", "sum_output", "count_requests"]
)

# With filters
from datetime import datetime, timedelta, timezone

time_from = datetime.now(timezone.utc) - timedelta(days=30)
daily = await client.aggregate(
    group_by="day",
    project="my_app",
    time_from=time_from
)
```

## Deletion

```python
# Delete all data for a project
result = await client.delete("old_project")
print(f"Deleted {result.events_deleted} events")

# Delete with time range
from datetime import datetime, timedelta, timezone

old_date = datetime.now(timezone.utc) - timedelta(days=365)
result = await client.delete(
    "my_app",
    time_to=old_date,
    include_aggregates=True
)
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

## Advanced Usage (Power Users)

If you need fine-grained control, you can still import the detailed classes:

```python
from token_usage_metrics import TokenUsageClient
from token_usage_metrics.config import Settings
from token_usage_metrics.models import (
    UsageEvent,
    UsageFilter,
    AggregateSpec,
    AggregateMetric,
    DeleteOptions
)

# Use Settings object for detailed configuration
settings = Settings(
    backend="redis",
    redis_url="redis://localhost:6379/0",
    buffer_size=1000,
    flush_interval=1.0,
    max_retries=3
)

client = TokenUsageClient(settings)
await client.start()

# Create UsageEvent objects manually
event = UsageEvent(
    project_name="my_app",
    request_type="chat",
    input_tokens=100,
    output_tokens=50
)
await client.log(event)

# Use detailed filter objects
filters = UsageFilter(project_name="my_app", limit=100)
events, cursor = await client.fetch_raw(filters)
```

## Package Structure

```
token-usage-metrics/
├── token_usage_metrics/     # Main package
│   ├── client.py           # TokenUsageClient API (simplified + advanced)
│   ├── models.py           # Data models (for power users)
│   ├── config.py           # Settings (for power users)
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

✅ **Minimal SDK**: 3-line setup, no object boilerplate  
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
