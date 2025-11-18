# API Reference

## TokenUsageClient

Main client for logging and querying token usage metrics.

### Initialization

```python
from token_usage_metrics import TokenUsageClient, Settings

# Method 1: Direct initialization
client = TokenUsageClient(settings)
await client.start()

# Method 2: Factory method
client = await TokenUsageClient.from_settings(settings)

# Method 3: Context manager (recommended)
async with TokenUsageClient(settings) as client:
    # Use client
    pass
```

### Methods

#### `async def log(event: UsageEvent) -> None`

Log a single usage event (async, non-blocking).

```python
event = UsageEvent(
    project_name="my_app",
    request_type="chat",
    input_tokens=100,
    output_tokens=50,
)
await client.log(event)
```

#### `async def log_many(events: list[UsageEvent]) -> None`

Log multiple usage events in batch.

```python
events = [event1, event2, event3]
await client.log_many(events)
```

#### `async def fetch_raw(filters: UsageFilter) -> tuple[list[UsageEvent], str | None]`

Fetch raw usage events with filters. Returns events and optional cursor for pagination.

```python
filters = UsageFilter(
    project_name="my_app",
    request_type="chat",
    time_from=datetime(...),
    time_to=datetime(...),
    limit=100,
)

events, cursor = await client.fetch_raw(filters)

# Pagination
if cursor:
    more_events, next_cursor = await client.fetch_raw(
        UsageFilter(cursor=cursor, limit=100)
    )
```

#### `async def summary_by_day(spec: AggregateSpec, filters: UsageFilter) -> list[TimeBucket]`

Get time-bucketed aggregates by day for time-series graphs.

```python
spec = AggregateSpec(
    metrics={
        AggregateMetric.SUM_TOTAL,
        AggregateMetric.COUNT_REQUESTS,
        AggregateMetric.AVG_TOTAL_PER_REQUEST,
    }
)

buckets = await client.summary_by_day(spec, filters)

for bucket in buckets:
    print(f"{bucket.start.date()}: {bucket.metrics}")
```

#### `async def summary_by_project(spec: AggregateSpec, filters: UsageFilter) -> list[SummaryRow]`

Get aggregated summary grouped by project.

```python
summaries = await client.summary_by_project(spec, filters)

for summary in summaries:
    project = summary.group_keys["project_name"]
    total = summary.metrics["sum_total"]
    print(f"{project}: {total} tokens")
```

#### `async def summary_by_request_type(spec: AggregateSpec, filters: UsageFilter) -> list[SummaryRow]`

Get aggregated summary grouped by request type.

```python
summaries = await client.summary_by_request_type(spec, filters)

for summary in summaries:
    req_type = summary.group_keys["request_type"]
    count = summary.metrics["count_requests"]
    print(f"{req_type}: {count} requests")
```

#### `async def timeseries(spec: AggregateSpec, filters: UsageFilter) -> list[TimeBucket]`

Get time-series data (alias for `summary_by_day`).

#### `async def delete_project(options: DeleteOptions) -> DeleteResult`

Delete usage data for a project.

```python
# Simulate deletion (dry-run)
options = DeleteOptions(
    project_name="old_app",
    time_from=datetime(...),  # Optional
    time_to=datetime(...),    # Optional
    include_aggregates=True,
    simulate=True,
)

result = await client.delete_project(options)
print(f"Would delete: {result.events_deleted} events")

# Actual deletion
options.simulate = False
result = await client.delete_project(options)
```

#### `async def flush(timeout: float | None = None) -> int`

Flush all pending events to backend. Returns number of events flushed.

```python
flushed = await client.flush(timeout=5.0)
print(f"Flushed {flushed} events")
```

#### `async def health_check() -> bool`

Check if backend is healthy and accessible.

```python
if await client.health_check():
    print("Backend is healthy")
```

#### `def get_stats() -> dict`

Get client statistics (queue size, drops, circuit state).

```python
stats = client.get_stats()
print(f"Queue size: {stats['queue_size']}")
print(f"Dropped: {stats['dropped_count']}")
print(f"Circuit: {stats['circuit_state']}")
```

#### `async def aclose() -> None`

Close client and flush remaining events.

```python
await client.aclose()
```

## Data Models

### UsageEvent

Token usage event with validation.

```python
from token_usage_metrics import UsageEvent

event = UsageEvent(
    id="optional-id",              # Auto-generated if omitted
    timestamp=datetime.now(UTC),   # Auto-generated if omitted
    project_name="my_app",         # Required, 1-128 chars
    request_type="chat",           # Required, 1-64 chars
    input_tokens=100,              # Required, >= 0
    output_tokens=50,              # Required, >= 0
    total_tokens=None,             # Optional, auto-derived
    request_count=1,               # Default: 1, >= 1
    metadata={"key": "value"},     # Optional, max 4KB
)
```

**Auto-validations:**

- `timestamp` converted to UTC if not timezone-aware
- `total_tokens` auto-calculated as `input_tokens + output_tokens` if None
- `metadata` size validated (max 4KB JSON)

### UsageFilter

Filter parameters for querying events.

```python
from token_usage_metrics import UsageFilter

filters = UsageFilter(
    project_name=None,      # Optional filter by project
    request_type=None,      # Optional filter by type
    time_from=None,         # Optional start datetime (UTC)
    time_to=None,           # Optional end datetime (UTC)
    limit=100,              # Max results (1-10000)
    cursor=None,            # Pagination cursor
)
```

### AggregateSpec

Specification for aggregate queries.

```python
from token_usage_metrics import AggregateSpec, AggregateMetric

spec = AggregateSpec(
    metrics={
        AggregateMetric.SUM_INPUT,
        AggregateMetric.SUM_OUTPUT,
        AggregateMetric.SUM_TOTAL,
        AggregateMetric.COUNT_REQUESTS,
        AggregateMetric.AVG_TOTAL_PER_REQUEST,
    },
    group_by=GroupByDimension.NONE,  # NONE | PROJECT | REQUEST_TYPE
    bucket=TimeBucketType.DAY,       # DAY (hour/week future)
)
```

### DeleteOptions

Options for project deletion.

```python
from token_usage_metrics import DeleteOptions

options = DeleteOptions(
    project_name="my_app",      # Required
    time_from=None,             # Optional date range start
    time_to=None,               # Optional date range end
    include_aggregates=True,    # Delete aggregates too?
    simulate=False,             # Dry-run mode
)
```

### TimeBucket

Time-bucketed aggregate result.

```python
bucket = TimeBucket(
    start=datetime(...),
    end=datetime(...),
    metrics={
        "sum_total": 1000,
        "count_requests": 10,
        "avg_total_per_request": 100.0,
    },
    group_keys=None,  # Optional grouping keys
)
```

### SummaryRow

Grouped aggregate summary row.

```python
row = SummaryRow(
    group_keys={"project_name": "my_app"},
    metrics={
        "sum_total": 5000,
        "count_requests": 50,
    },
)
```

### DeleteResult

Result of deletion operation.

```python
result = DeleteResult(
    events_deleted=100,
    aggregates_deleted=30,
    simulated=False,
)
```

## Settings

Configuration via environment variables or Settings object.

```python
from token_usage_metrics import Settings

settings = Settings(
    # Backend selection
    backend="redis",  # redis | postgres | mongodb

    # Redis
    redis_url="redis://localhost:6379/0",
    redis_pool_size=10,
    redis_socket_timeout=5.0,

    # Postgres
    postgres_dsn="postgresql://user:pass@localhost/db",
    postgres_pool_min_size=2,
    postgres_pool_max_size=10,

    # MongoDB
    mongodb_url="mongodb://localhost:27017",
    mongodb_database="token_usage",
    mongodb_max_pool_size=10,

    # Queue/buffering
    buffer_size=1000,
    flush_interval=1.0,
    flush_batch_size=200,
    drop_policy="oldest",  # oldest | newest

    # Resilience
    max_retries=3,
    retry_backoff_base=0.5,
    retry_backoff_max=10.0,
    circuit_breaker_threshold=5,
    circuit_breaker_timeout=60.0,

    # Retention
    default_ttl_days=0,  # 0 = lifetime retention

    # Logging
    log_level="INFO",
    structured_logging=True,
)
```

**Environment Variables:**

Prefix all with `TUM_`:

- `TUM_BACKEND=redis`
- `TUM_REDIS_URL=redis://...`
- `TUM_BUFFER_SIZE=1000`
- etc.

## Enums

### AggregateMetric

```python
class AggregateMetric(str, Enum):
    SUM_INPUT = "sum_input"
    SUM_OUTPUT = "sum_output"
    SUM_TOTAL = "sum_total"
    COUNT_REQUESTS = "count_requests"
    AVG_TOTAL_PER_REQUEST = "avg_total_per_request"
```

### GroupByDimension

```python
class GroupByDimension(str, Enum):
    NONE = "none"
    PROJECT = "project_name"
    REQUEST_TYPE = "request_type"
    PROJECT_AND_TYPE = "project_and_type"
```

### TimeBucketType

```python
class TimeBucketType(str, Enum):
    DAY = "day"
    HOUR = "hour"      # Future
    WEEK = "week"      # Future
```

### BackendType

```python
class BackendType(str, Enum):
    REDIS = "redis"
    POSTGRES = "postgres"
    MONGODB = "mongodb"
```

## Error Handling

```python
from token_usage_metrics.errors import (
    BackendError,
    BackendUnavailable,
    CircuitBreakerOpen,
    BufferFullError,
    DroppedEventError,
    DeletionError,
)

try:
    await client.log(event)
except CircuitBreakerOpen:
    # Backend is unavailable, circuit breaker open
    pass
except BufferFullError:
    # Buffer full and drop policy prevented enqueue
    pass
except BackendError as e:
    # General backend error
    pass
```

## Examples

See the [examples/](../examples/) directory for complete working examples:

- `basic_usage.py` - Complete demo with all features
- More examples coming soon!
