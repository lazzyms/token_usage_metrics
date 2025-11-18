# Architecture

## System Overview

```text
┌─────────────────────────────────────────────────────────────┐
│                    Your Application                         │
│                                                             │
│  await client.log(UsageEvent(...))  ← Non-blocking         │
└────────────────┬────────────────────────────────────────────┘
                 │
        ┌────────▼────────┐
        │ TokenUsageClient│
        │  (Main API)     │
        └────────┬─────────┘
                 │
        ┌────────▼─────────┐
        │ AsyncEventQueue  │  ← Bounded buffer (1000 events)
        │ + CircuitBreaker │  ← Auto-recovery mechanism
        │                  │
        │ Background       │  ← Flushes every 1s
        │ Flusher Task     │
        └────────┬──────────┘
                 │
     ┌───────────▼───────────┐
     │   Backend Interface   │  ← Abstract base class
     │       (ABC)           │
     └───────────┬───────────┘
                 │
   ┌────────────┼────────────┼────────────┐
   │            │            │            │
┌───▼────┐  ┌───▼─────┐  ┌───▼────┐  ┌──▼──────┐
│ Redis  │  │Postgres │  │Supabase │  │ MongoDB │
└────────┘  └─────────┘  └─────────┘
```

## Components

### 1. TokenUsageClient

**Responsibility:** High-level API for users

**Features:**

- Lifecycle management (start/stop)
- Async context manager support
- Backend factory and initialization
- Unified interface across all backends

**Key Methods:**

- `log()`, `log_many()` - Enqueue events
- `fetch_raw()` - Query raw events
- `summary_*()` - Query aggregates
- `delete_project()` - Delete data
- `flush()`, `health_check()`, `get_stats()` - Utilities

### 2. AsyncEventQueue

**Responsibility:** Non-blocking event buffering with resilience

**Features:**

- Bounded in-memory queue (configurable size)
- Background flush task (periodic batching)
- Drop policies (oldest/newest when full)
- Circuit breaker integration
- Retry logic with exponential backoff

**Flow:**

```text
Event → Enqueue → Buffer → Background Flush → Backend
          ↓
       Drop if full (logged)
```

### 3. CircuitBreaker

**Responsibility:** Prevent cascading failures

**States:**

- **Closed:** Normal operation
- **Open:** Too many failures, reject requests
- **Half-Open:** Test recovery after timeout

**Transitions:**

```text
Closed ─[threshold failures]→ Open
   ↑                            │
   │                            │
   └─[success]─ Half-Open ←─[timeout]
```

### 4. Backend Interface

**Responsibility:** Abstract storage operations

**Required Methods:**

- `connect()`, `disconnect()` - Lifecycle
- `health_check()` - Health probe
- `log_many()` - Batch write
- `fetch_raw()` - Query with filters
- `summary_by_day/project/type()` - Aggregations
- `delete_project()` - Deletion

## Backend Implementations

### Redis Backend

**Schema:**

```text
Event Storage:
   tum:e:{id} → Hash {
      id, ts, project, type,
      input, output, total, count, metadata
   }

Indexes (Day-Partitioned ZSETs):
   tum:ts:{YYYYMMDD} → {id: timestamp_score}
   tum:proj:{project}:{YYYYMMDD} → {id: timestamp_score}
   tum:type:{type}:{YYYYMMDD} → {id: timestamp_score}

Daily Aggregates (Hashes):
   tum:agg:{YYYYMMDD} → {input_tokens, output_tokens, ...}
   tum:agg:{YYYYMMDD}:proj:{project} → {...}
   tum:agg:{YYYYMMDD}:type:{type} → {...}
   tum:agg:{YYYYMMDD}:proj:{p}:type:{t} → {...}
```

**Write Path:**

1. Pipeline: HSET event + ZADD to 3 indexes + HINCRBY to 4 aggregates
2. ~10 Redis ops per event (pipelined)

**Read Path:**

- Raw: ZRANGEBYSCORE on narrowest index → HMGET events
- Aggregates: HGETALL aggregate keys across date range
- Intersections: ZINTERSTORE for multi-filter queries

**Pros:**

- Extremely fast writes (pipelined)
- Efficient range queries (ZSETs)
- Precomputed aggregates

**Cons:**

- Memory-intensive for large datasets
- Complex key management

### PostgreSQL Backend

**Schema:**

```sql
CREATE TABLE usage_events (
    id TEXT PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    project_name TEXT NOT NULL,
    request_type TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    request_count INTEGER NOT NULL,
    metadata JSONB
);

CREATE TABLE daily_aggregates (
    date DATE NOT NULL,
    project_name TEXT,
    request_type TEXT,
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    total_tokens BIGINT NOT NULL DEFAULT 0,
    request_count BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (date, project_name, request_type)
);

-- Indexes
CREATE INDEX idx_usage_events_timestamp ON usage_events (timestamp);
CREATE INDEX idx_usage_events_project_ts ON usage_events (project_name, timestamp);
CREATE INDEX idx_usage_events_type_ts ON usage_events (request_type, timestamp);
CREATE INDEX idx_usage_events_project_type_ts
    ON usage_events (project_name, request_type, timestamp);
```

**Write Path:**

1. Batch INSERT events (executemany)
2. UPSERT aggregates (ON CONFLICT DO UPDATE)

**Read Path:**

- Raw: SELECT with WHERE + indexes
- Aggregates: GROUP BY queries on daily_aggregates

**Pros:**

- ACID transactions
- Powerful SQL queries
- Long-term storage (disk-based)

**Cons:**

- Slower writes than Redis
- Index maintenance overhead

### Supabase Backend

Supabase exposes a managed Postgres database, so the implementation mirrors the Postgres backend. Configure `supabase_dsn` with the Supabase Postgres connection string (service role key for writes) to reuse the same `usage_events` and `daily_aggregates` tables.

### MongoDB Backend

**Schema:**

```javascript
// usage_events collection
{
    _id: "event_id",
    timestamp: ISODate("..."),
    project_name: "...",
    request_type: "...",
    input_tokens: 100,
    output_tokens: 50,
    total_tokens: 150,
    request_count: 1,
    metadata: {...}
}

// daily_aggregates collection
{
    date: ISODate("..."),
    project_name: "...",
    request_type: "...",
    input_tokens: 1000,
    output_tokens: 500,
    total_tokens: 1500,
    request_count: 10
}

// Indexes
db.usage_events.createIndex({ timestamp: 1 })
db.usage_events.createIndex({ project_name: 1, timestamp: 1 })
db.usage_events.createIndex({ request_type: 1, timestamp: 1 })
db.usage_events.createIndex({ project_name: 1, request_type: 1, timestamp: 1 })

db.daily_aggregates.createIndex(
    { date: 1, project_name: 1, request_type: 1 },
    { unique: true }
)
```

**Write Path:**

1. Bulk insert events
2. Update/upsert aggregates ($inc operators)

**Read Path:**

- Raw: find() with filters
- Aggregates: Aggregation pipeline with $group

**Pros:**

- Flexible schema (JSONB-like)
- Good aggregation framework
- Horizontal scaling

**Cons:**

- Eventual consistency (depending on config)
- Memory usage for indexes

## Data Flow

### Logging Event

```text
1. User calls client.log(event)
   ↓
2. Validate event (Pydantic)
   ↓
3. Enqueue to AsyncEventQueue
   ↓ (non-blocking return)
4. Background flusher collects batch
   ↓
5. Circuit breaker check
   ↓
6. Backend.log_many(batch)
   ↓
7. Retry on transient failures
   ↓
8. Success → circuit closed
   OR
   Failure → increment circuit failures
```

### Querying Events

```text
1. User calls client.fetch_raw(filters)
   ↓
2. Backend.fetch_raw(filters)
   ↓
3. Build query based on filters
   ↓
4. Execute against backend
   ↓
5. Deserialize results
   ↓
6. Return (events, cursor)
```

### Aggregation

```text
1. User calls client.summary_by_day(spec, filters)
   ↓
2. Backend.summary_by_day(spec, filters)
   ↓
3. Read precomputed daily aggregates
   ↓
4. Filter by date range
   ↓
5. Compute derived metrics (avg, rate)
   ↓
6. Return list[TimeBucket]
```

## Resilience Patterns

### 1. Circuit Breaker

Prevents overwhelming unhealthy backends.

**Configuration:**

- Threshold: 5 failures
- Timeout: 60 seconds
- State: closed → open → half-open → closed

### 2. Retry with Backoff

Handles transient failures.

**Configuration:**

- Max retries: 3
- Base backoff: 0.5s
- Max backoff: 10s
- Jitter: randomized

### 3. Buffered Writes

Prevents blocking on backend slowness.

**Configuration:**

- Buffer size: 1000 events
- Flush interval: 1 second
- Batch size: 200 events
- Drop policy: oldest/newest

### 4. Graceful Degradation

- Buffer full? Drop with logging
- Circuit open? Reject with error
- Flush timeout? Partial flush
- Backend down? Health check fails

## Performance Characteristics

### Throughput (Single Instance)

| Backend  | Writes/sec | Reads/sec | Latency (p99) |
| -------- | ---------- | --------- | ------------- |
| Redis    | ~10,000    | ~5,000    | <5ms          |
| Postgres | ~2,000     | ~10,000   | <20ms         |
| MongoDB  | ~5,000     | ~8,000    | <10ms         |

> Note: Depends on hardware, network, and load

### Memory Usage

- **Client:** ~10MB base + (buffer_size × 1KB per event)
- **Redis:** ~1KB per event + ~500B per aggregate
- **Postgres:** Minimal (disk-based)
- **MongoDB:** ~1KB per document

### Scaling Strategies

**Vertical:**

- Increase buffer size
- Increase flush batch size
- Tune backend connection pools

**Horizontal:**

- Multiple client instances (independent buffers)
- Backend sharding (Redis Cluster, Postgres partitioning, Mongo sharding)
- Read replicas for queries

## Configuration Best Practices

### Development

```python
Settings(
    backend="redis",
    buffer_size=100,
    flush_interval=0.1,  # Fast feedback
    log_level="DEBUG",
)
```

### Production

```python
Settings(
    backend="redis",  # or postgres for long-term
    buffer_size=1000,
    flush_interval=1.0,
    flush_batch_size=200,
    max_retries=3,
    circuit_breaker_threshold=5,
    log_level="INFO",
)
```

### High-Throughput

```python
Settings(
    backend="redis",
    buffer_size=5000,
    flush_interval=2.0,
    flush_batch_size=1000,
    redis_pool_size=20,
)
```

## Extension Points

### Adding a New Backend

1. Subclass `Backend` ABC
2. Implement all abstract methods
3. Add to `BackendType` enum
4. Update `TokenUsageClient._create_backend()`
5. Add dependencies to `pyproject.toml`

Example:

```python
class ClickHouseBackend(Backend):
    async def connect(self) -> None: ...
    async def log_many(self, events: list[UsageEvent]) -> None: ...
    # ... implement all methods
```

### Custom Aggregations

Extend `AggregateMetric` and update backends' `_compute_metrics()`:

```python
class AggregateMetric(str, Enum):
    # ... existing
    P95_TOKENS = "p95_tokens"  # New metric
```

### Custom Drop Policies

Extend `AsyncEventQueue` with new policy:

```python
if self.drop_policy == "priority":
    # Drop based on custom priority logic
    pass
```
