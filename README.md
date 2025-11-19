# Token Usage Metrics

A production-ready, async-first Python package for tracking LLM and embedding token usage with multi-backend support (Redis, PostgreSQL/Supabase, MongoDB). It provides lifetime retention, project-based deletion, rich aggregations, and non-blocking async operations for high-throughput environments.

## Features

- **Multi-Backend Support:** Redis, PostgreSQL, Supabase, and MongoDB backends with a unified API.
- **Rich Aggregations:** Daily summaries, project/type grouping, and time-series for dashboards.
- **Async-First:** Non-blocking operations with background flushing and circuit breakers.
- **Lifetime Retention:** No enforced TTL; explicit project-based deletion with date ranges.
- **Flexible Queries:** Raw event fetching with filters and cursor-based pagination.
- **Production-Ready:** Structured logging, retry logic, graceful fallbacks, and health checks.

---

# Documentation

## 1. Quick Start and Setup

### Installation

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

# Supabase only
uv add token-usage-metrics[supabase]
```

### Basic Example with Redis

1. Start Redis (using Docker):

```bash
docker run -d -p 6379:6379 redis:7-alpine
```

2. Create a simple script:

```python
import asyncio
from token_usage_metrics import TokenUsageClient, Settings

async def main():
    # Initialize client with Redis
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

3. Run it:

```bash
python your_script.py
# Output: Found 1 events
```

## 2. Supported Databases

### 2.1 Redis

Docker:

```bash
docker run -d -p 6379:6379 redis:7-alpine
```

**Schema:** Hash-per-event + day-partitioned ZSETs + daily aggregate hashes. Optimized for fast writes and efficient date-range queries.

**Performance:** ~10k writes/sec (pipelined batches), ~5k reads/sec (optimized ZSETs)

### 2.2 PostgreSQL

Docker:

```bash
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=pass -e POSTGRES_DB=token_usage postgres:16-alpine
```

**Schema:** `usage_events` table + `daily_aggregates` table with indexes on `(project, timestamp)` and `(type, timestamp)`.

**Performance:** ~2k writes/sec (bulk inserts), ~10k reads/sec (indexed queries)

Configuration:

```python
from token_usage_metrics import Settings

settings = Settings(
    backend="postgres",
    postgres_dsn="postgresql://user:pass@localhost:5432/token_usage"
)
```

### 2.3 MongoDB

Docker:

```bash
docker run -d -p 27017:27017 mongo:7
```

**Schema:** `usage_events` collection + `daily_aggregates` collection with compound indexes.

**Performance:** ~5k writes/sec (batched inserts), ~8k reads/sec (indexed scans)

Configuration:

```python
from token_usage_metrics import Settings

settings = Settings(
    backend="mongodb",
    mongodb_url="mongodb://localhost:27017",
    mongodb_database="token_usage"
)
```

### 2.4 Supabase

Supabase exposes the same Postgres-compatible `usage_events` and `daily_aggregates` schema.

Configuration:

```bash
export TUM_BACKEND=supabase
export TUM_SUPABASE_DSN="postgresql://postgres:service_role_key@db.supabase.co:5432/postgres"
```

## 3. Configuration

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

## 4. API Reference

### Methods and Models

#### TokenUsageClient

Main client for interacting with the token usage tracking system.

**Initialization:**

```python
client = await TokenUsageClient.from_settings(settings)
```

#### Logging Events

```python
# Single event (async, non-blocking)
await client.log(
    project: str,
    request_type: str,
    input_tokens: int,
    output_tokens: int,
    metadata: dict | None = None
)

# Multiple events
await client.log_many(events: list[UsageEvent])

# Force flush pending events
flushed_count = await client.flush(timeout: float = 5.0)
```

**Arguments:**

- `project` (str): Project identifier
- `request_type` (str): Type of request (e.g., "chat", "embedding")
- `input_tokens` (int): Number of input tokens
- `output_tokens` (int): Number of output tokens
- `metadata` (dict, optional): Additional context metadata

#### Fetching Raw Events

```python
from token_usage_metrics import UsageFilter

events, next_cursor = await client.fetch_raw(
    filters: UsageFilter
)
```

**UsageFilter Arguments:**

- `project_name` (str, optional): Filter by project
- `request_type` (str, optional): Filter by request type
- `time_from` (datetime, optional): Start time
- `time_to` (datetime, optional): End time
- `limit` (int): Maximum number of events (default: 100)
- `cursor` (str, optional): Pagination cursor

#### Aggregations & Summaries

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

**AggregateMetric Options:**

- `SUM_TOTAL`: Sum of all tokens
- `COUNT_REQUESTS`: Number of requests
- `AVG_TOTAL_PER_REQUEST`: Average tokens per request
- `SUM_INPUT_TOKENS`: Sum of input tokens
- `SUM_OUTPUT_TOKENS`: Sum of output tokens

#### Deleting Project Data

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

**DeleteOptions Arguments:**

- `project_name` (str): Project to delete
- `time_from` (datetime): Start of deletion range
- `time_to` (datetime): End of deletion range
- `include_aggregates` (bool): Also delete aggregates
- `simulate` (bool): Dry run without actual deletion

## 5. Architecture

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

## 6. Testing

Run tests with `pytest`:

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=token_usage_metrics

# Run a specific test
uv run pytest tests/test_redis_backend.py -v
```

## 7. Development

```bash
# Install dev dependencies
uv add --dev ruff mypy pytest pytest-asyncio fakeredis

# Lint and format
uv run ruff check .
uv run ruff format .

# Type checking
uv run mypy token_usage_metrics
```

## 8. Performance

- **Redis:** ~10k writes/sec (pipelined batches), ~5k reads/sec (optimized ZSETs)
- **Postgres:** ~2k writes/sec (bulk inserts), ~10k reads/sec (indexed queries)
- **MongoDB:** ~5k writes/sec (batched inserts), ~8k reads/sec (indexed scans)

Benchmarks on single-instance deployments. Scale horizontally for higher throughput.

## 9. Contribution Guidelines

We welcome contributions! Please follow these guidelines to ensure a smooth process:

### Getting Started

1. **Fork the Repository:** Click the fork button on GitHub.
2. **Clone Your Fork:**
   ```bash
   git clone https://github.com/your-username/token-usage-metrics.git
   cd token-usage-metrics
   ```
3. **Create a Branch:**
   ```bash
   git checkout -b feature/your-feature-name
   ```

### Development Setup

1. **Install Dependencies:**
   ```bash
   uv sync
   uv add --dev ruff mypy pytest pytest-asyncio fakeredis
   ```
2. **Run Tests:**
   ```bash
   uv run pytest
   ```

### Code Standards

- **Format Code:** Use ruff for formatting
  ```bash
  uv run ruff format .
  ```
- **Lint Code:** Check for issues
  ```bash
  uv run ruff check .
  ```
- **Type Checking:** Ensure type safety
  ```bash
  uv run mypy token_usage_metrics
  ```

### Submitting Changes

1. **Commit Your Changes:**
   ```bash
   git add .
   git commit -m "feat: add your feature description"
   ```
2. **Push to Your Fork:**
   ```bash
   git push origin feature/your-feature-name
   ```
3. **Open a Pull Request:** Go to GitHub and create a PR with a clear description of your changes.

### PR Guidelines

- Ensure all tests pass: `uv run pytest`
- Include test coverage for new features
- Update documentation if needed
- Follow the commit message format: `feat:`, `fix:`, `docs:`, `test:`, etc.

### Reporting Issues

Please use GitHub Issues to report bugs or suggest features. Include:

- Clear description of the issue
- Steps to reproduce (for bugs)
- Expected vs. actual behavior
- Environment details (Python version, backend used, etc.)

Thank you for contributing! 🎉

---

---

Built with ❤️ by lazzyms
