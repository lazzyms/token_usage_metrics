# Token Usage Metrics

A lightweight, async-first Python package for tracking LLM and embedding token usage with multi-backend support (Redis, PostgreSQL/Supabase, MongoDB). Built for production with lifetime retention, project-based deletion, comprehensive aggregations, and non-blocking async operations.

## Features

✨ **Multi-Backend Support**: Redis, PostgreSQL, Supabase, and MongoDB backends with unified API
📊 **Rich Aggregations**: Daily summaries, project/type grouping, and time-series for dashboards
🚀 **Async-First**: Non-blocking operations with background flushing and circuit breakers
💾 **Lifetime Retention**: No enforced TTL; explicit project-based deletion with date ranges
🔍 **Flexible Queries**: Raw event fetching with filters and cursor-based pagination
🛡️ **Production-Ready**: Structured logging, retry logic, graceful fallbacks, and health checks

## Installation

```bash
# Install with Redis support (recommended for getting started)
uv add token-usage-metrics[redis]

# Or with all backends
uv add token-usage-metrics[all]

# Individual backends
uv add token-usage-metrics[postgres]  # PostgreSQL
uv add token-usage-metrics[mongo]     # MongoDB
```

## Quick Start

**Simple 3-line example:**

```python
import asyncio
from token_usage_metrics import TokenUsageClient

async def main():
    client = await TokenUsageClient.init("redis://localhost:6379/0")
    await client.log("my_app", "chat", input_tokens=100, output_tokens=50)

    events, _ = await client.query(project="my_app")
    print(f"Found {len(events)} events")
    await client.aclose()

asyncio.run(main())
```

**With more features:**

```python
from datetime import datetime, timedelta, timezone

async def main():
    client = await TokenUsageClient.init("redis://localhost:6379/0")

    # Log usage
    await client.log(
        "chatbot", "chat",
        input_tokens=100, output_tokens=50,
        metadata={"model": "gpt-4"}
    )

    # Query events
    events, _ = await client.query(project="chatbot")

    # Get daily aggregates
    daily = await client.aggregate(
        group_by="day",
        time_from=datetime.now(timezone.utc) - timedelta(days=7)
    )

    await client.aclose()
```

### Supabase example

```python
settings = Settings(
    backend="supabase",
    supabase_dsn="postgresql://postgres:service_role_key@db.supabase.co:5432/postgres"
)
```

## Configuration

Configure via environment variables (prefix: `TUM_`) or Settings object:

```python
from token_usage_metrics import Settings

settings = Settings(
    # Backend selection
    backend="redis",  # redis | postgres | supabase | mongodb

    # Redis settings
    redis_url="redis://localhost:6379/0",
    redis_pool_size=10,

    # Postgres settings
    postgres_dsn="postgresql://user:pass@localhost:5432/token_usage",

    # Supabase settings
    supabase_dsn="postgresql://postgres:service_role_key@db.supabase.co:5432/postgres",

    # MongoDB settings
    mongodb_url="mongodb://localhost:27017",
    mongodb_database="token_usage",

    # Queue/buffering
    buffer_size=1000,
    flush_interval=1.0,  # seconds
    flush_batch_size=200,

    # Resilience
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

```bash
# Docker
docker run -d -p 6379:6379 redis:7-alpine

# Or use managed Redis (AWS ElastiCache, Redis Cloud, etc.)
```

**Schema**: Hash-per-event + day-partitioned ZSETs + daily aggregate hashes. Optimized for fast writes and efficient date-range queries.

### PostgreSQL

```bash
# Docker
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=pass -e POSTGRES_DB=token_usage postgres:16-alpine

# Tables auto-created on first connection
```

**Schema**: `usage_events` table + `daily_aggregates` table with indexes on `(project, timestamp)`, `(type, timestamp)`.

### Supabase

Supabase exposes the same Postgres-compatible `usage_events` and `daily_aggregates` schema. Provide the Postgres connection string (including the service role key) via `supabase_dsn` so the client can connect securely.

```bash
# Example Supabase credentials
export TUM_BACKEND=supabase
export TUM_SUPABASE_DSN="postgresql://postgres:service_role_key@db.supabase.co:5432/postgres"
```

### MongoDB

```bash
# Docker
docker run -d -p 27017:27017 mongo:7

# Collections auto-created with indexes
```

**Schema**: `usage_events` collection + `daily_aggregates` collection with compound indexes.

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
    cursor=None  # For pagination
)

events, next_cursor = await client.fetch_raw(filters)

# Pagination
if next_cursor:
    more_events, cursor = await client.fetch_raw(
        UsageFilter(cursor=next_cursor, limit=100)
    )
```

### Aggregations & Summaries

```python
from token_usage_metrics import AggregateSpec, AggregateMetric

# Daily time-series (for graphs)
spec = AggregateSpec(
    metrics={
        AggregateMetric.SUM_TOTAL,
        AggregateMetric.COUNT_REQUESTS,
        AggregateMetric.AVG_TOTAL_PER_REQUEST
    }
)

daily_buckets = await client.summary_by_day(spec, filters)

# Grouped by project
project_summaries = await client.summary_by_project(spec, filters)
for summary in project_summaries:
    print(f"{summary.group_keys['project_name']}: {summary.metrics}")

# Grouped by request type
type_summaries = await client.summary_by_request_type(spec, filters)
```

### Deleting Project Data

```python
from token_usage_metrics import DeleteOptions

# Simulate deletion (dry-run)
options = DeleteOptions(
    project_name="my_app",
    time_from=datetime(...),  # Optional
    time_to=datetime(...),    # Optional
    include_aggregates=True,
    simulate=True
)

result = await client.delete_project(options)
print(f"Would delete {result.events_deleted} events and {result.aggregates_deleted} aggregates")

# Actual deletion
options.simulate = False
result = await client.delete_project(options)
```

## Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                     TokenUsageClient                        │
│  (Async API: log, fetch_raw, summary_*, delete_project)    │
└────────────────┬────────────────────────────────────────────┘
                 │
        ┌────────┴────────┐
        │ AsyncEventQueue │  (Background flusher, circuit breaker)
        └────────┬─────────┘
                 │
     ┌───────────┴───────────┐
     │   Backend Interface   │
     └───────────┬───────────┘
                 │
    ┌────────────┼────────────┼────────────┐
    │            │            │            │
┌───▼────┐  ┌───▼─────┐  ┌───▼────┐  ┌──▼──────┐
│ Redis  │  │Postgres │  │Supabase │  │ MongoDB │
└────────┘  └─────────┘  └─────────┘  └─────────┘
```

- **Async Queue**: Buffers events in memory, flushes batches periodically
- **Circuit Breaker**: Auto-recovery when backend is unhealthy
- **Retry Logic**: Exponential backoff with jitter for transient errors
- **Lifetime Retention**: No enforced TTL (configurable per-backend if needed)

## Testing

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=token_usage_metrics

# Run specific test
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

- **Redis**: ~10k writes/sec (pipelined batches), ~5k reads/sec (optimized ZSETs)
- **Postgres**: ~2k writes/sec (bulk inserts), ~10k reads/sec (indexed queries)
- **MongoDB**: ~5k writes/sec (batched inserts), ~8k reads/sec (indexed scans)

_Benchmarks on single-instance deployments. Scale horizontally for higher throughput._

## License

MIT

## Contributing

Contributions welcome! Please open an issue or PR on GitHub.

---

Built with ❤️ by lazzyms
