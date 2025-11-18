# Token Usage Metrics — Quick Start & Setup

## Overview

Token Usage Metrics is a production-ready, async-first Python package for tracking LLM and embedding token usage with multi-backend support (Redis, PostgreSQL/Supabase, MongoDB). It provides lifetime retention, project-based deletion, rich aggregations, and non-blocking async operations for high-throughput environments.

## Features

- **Multi-Backend Support:** Redis, PostgreSQL, Supabase, and MongoDB backends with a unified API.
- **Rich Aggregations:** Daily summaries, project/type grouping, and time-series for dashboards.
- **Async-First:** Non-blocking operations with background flushing and circuit breakers.
- **Lifetime Retention:** No enforced TTL; explicit project-based deletion with date ranges.
- **Flexible Queries:** Raw event fetching with filters and cursor-based pagination.
- **Production-Ready:** Structured logging, retry logic, graceful fallbacks, and health checks.

## Installation

Install with Redis support (recommended for getting started):

```bash
uv add token-usage-metrics[redis]
```

Other installation options:

```bash
# All backends
uv add token-usage-metrics[all]

# PostgreSQL only
uv add token-usage-metrics[postgres]

# MongoDB only
uv add token-usage-metrics[mongo]
```

## Quick Start with Redis

1. Start Redis (using Docker):

```bash
docker run -d -p 6379:6379 redis:7-alpine
```

1. Create a simple script:

```python
import asyncio
from token_usage_metrics import TokenUsageClient

async def main():
    # Initialize client
    from token_usage_metrics import Settings
    settings = Settings(backend="redis", redis_url="redis://localhost:6379/0")
    client = await TokenUsageClient.from_settings(settings)

    # Log token usage
    await client.log("my_app", "chat", input_tokens=100, output_tokens=50)

    # Query events
    events, _ = await client.query(project="my_app")
    print(f"Found {len(events)} events")

    await client.aclose()

asyncio.run(main())
```

1. Run it:

```bash
python your_script.py
# Output: Found 1 events
```

## Complete Example

```python
import asyncio
from datetime import datetime, timedelta, timezone
from token_usage_metrics import TokenUsageClient

async def main():
    # Initialize client
    from token_usage_metrics import Settings
    settings = Settings(backend="redis", redis_url="redis://localhost:6379/0")
    client = await TokenUsageClient.from_settings(settings)

    # Log multiple events with metadata
    await client.log(
        "chatbot", "chat",
        input_tokens=100, output_tokens=50,
        metadata={"model": "gpt-4", "user": "alice"}

    ## Testing

    Run tests with `pytest`:
        metadata={"model": "text-embedding-ada-002"}
    )

    # Query all events for the project
    events, _ = await client.query(project="chatbot")
    print(f"Total events: {len(events)}")

    # Get daily aggregates for the last 7 days
    daily = await client.aggregate(
        group_by="day",
        time_from=datetime.now(timezone.utc) - timedelta(days=7)
    )

    print("\nDaily Usage:")
    for bucket in daily:
        print(f"{bucket.start.date()}: {bucket.metrics}")

    # Clean up
    await client.aclose()

asyncio.run(main())
```

## Configuration

Configure via environment variables (prefix: `TUM_`) or the `Settings` object:

```python
from token_usage_metrics import Settings

settings = Settings(
    backend="redis",  # redis | postgres | supabase | mongodb
    redis_url="redis://localhost:6379/0",
    redis_pool_size=10,
    postgres_dsn="postgresql://user:pass@localhost:5432/token_usage",
    supabase_dsn="postgresql://postgres:service_role_key@db.supabase.co:5432/postgres",
    mongodb_url="mongodb://localhost:27017",
    mongodb_database="token_usage",
    buffer_size=1000,
    flush_interval=1.0,  # seconds
    flush_batch_size=200,
    max_retries=3,
    circuit_breaker_threshold=5,
)
```

Or via `.env`:

```env
TUM_BACKEND=redis
TUM_REDIS_URL=redis://localhost:6379/0
TUM_BUFFER_SIZE=1000
TUM_FLUSH_INTERVAL=1.0
```

## Backend Setup

### Redis

Docker:

```bash
docker run -d -p 6379:6379 redis:7-alpine
```

Schema: Hash-per-event + day-partitioned ZSETs + daily aggregate hashes. Optimized for fast writes and efficient date-range queries.

### PostgreSQL

Docker:

```bash
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=pass -e POSTGRES_DB=token_usage postgres:16-alpine
```

Schema: `usage_events` table + `daily_aggregates` table with indexes on `(project, timestamp)` and `(type, timestamp)`.

### Supabase

Supabase exposes the same Postgres-compatible `usage_events` and `daily_aggregates` schema. Provide the Postgres connection string (including the service role key) via `supabase_dsn`.

```bash
export TUM_BACKEND=supabase
export TUM_SUPABASE_DSN="postgresql://postgres:service_role_key@db.supabase.co:5432/postgres"
```

### MongoDB

Docker:

```bash
docker run -d -p 27017:27017 mongo:7
```

Schema: `usage_events` collection + `daily_aggregates` collection with compound indexes.

## API Reference

### Logging Events

```python
# Single event (async, non-blocking)
await client.log(event)

# Multiple events
await client.log_many([event1, event2, event3])

# Force flush pending events
flushed_count = await client.flush(timeout=5.0)
```

### Fetching Raw Events

```python
from token_usage_metrics import UsageFilter

filters = UsageFilter(
    project_name="my_app",
    request_type="chat",
    time_from=datetime(...),
    time_to=datetime(...),
    limit=100,
    cursor=None
)

events, next_cursor = await client.fetch_raw(filters)
```

### Aggregations & Summaries

```python
from token_usage_metrics import AggregateSpec, AggregateMetric

spec = AggregateSpec(
    metrics={
        AggregateMetric.SUM_TOTAL,
        AggregateMetric.COUNT_REQUESTS,
        AggregateMetric.AVG_TOTAL_PER_REQUEST
    }
)

daily_buckets = await client.summary_by_day(spec, filters)
```

### Deleting Project Data

```python
from token_usage_metrics import DeleteOptions

options = DeleteOptions(
    project_name="my_app",
    time_from=datetime(...),
    time_to=datetime(...),
    include_aggregates=True,
    simulate=True
)

result = await client.delete_project(options)
```

## Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                     TokenUsageClient                        │
│  (Async API: log, fetch_raw, summary_*, delete_project)     │
└────────────────┬────────────────────────────────────────────┘
                 │
        ┌────────┴────────┐
        │ AsyncEventQueue │  (Background flusher, circuit breaker)
        └────────┬─────────┘
                 │
     ┌───────────┴───────────┐
     │   Backend Interface    │
     └───────────┬───────────┘
                 │
    ┌────────────┼────────────┼────────────┐
    │            │            │            │
┌───▼────┐  ┌───▼─────┐  ┌───▼────┐  ┌──▼──────┐
│ Redis  │  │Postgres │  │Supabase │  │ MongoDB │
└────────┘  └─────────┘  └─────────┘  └─────────┘
```

- **Async Queue:** Buffers events in memory, flushes batches periodically.
- **Circuit Breaker:** Auto-recovery when backend is unhealthy.
- **Retry Logic:** Exponential backoff with jitter for transient errors.
- **Lifetime Retention:** No enforced TTL (configurable per-backend if needed).

## Testing

Run tests with `pytest`:

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=token_usage_metrics

# Run a specific test
uv run pytest tests/test_redis_backend.py -v
```

## Development

```bash
# Install dev dependencies
uv add --dev ruff mypy pytest pytest-asyncio fakeredis

# Lint and format
uv run ruff check .
uv run ruff format .

# Type checking
uv run mypy token_usage_metrics
```

## Performance

- **Redis:** ~10k writes/sec (pipelined batches), ~5k reads/sec (optimized ZSETs)
- **Postgres:** ~2k writes/sec (bulk inserts), ~10k reads/sec (indexed queries)
- **MongoDB:** ~5k writes/sec (batched inserts), ~8k reads/sec (indexed scans)

Benchmarks on single-instance deployments. Scale horizontally for higher throughput.

## License

MIT

## Contributing

Contributions welcome! Please open an issue or PR on GitHub.

---

Built with ❤️ by lazzyms
