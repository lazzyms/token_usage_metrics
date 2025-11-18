"""Integration tests for token usage metrics with Redis backend."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fakeredis import FakeAsyncRedis

from token_usage_metrics import (
    AggregateSpec,
    DeleteOptions,
    Settings,
    TokenUsageClient,
    UsageEvent,
    UsageFilter,
)
from token_usage_metrics.backends.redis import RedisBackend
from token_usage_metrics.config import BackendType


@pytest.fixture
async def redis_backend():
    """Create a fake Redis backend for testing."""
    fake_redis = FakeAsyncRedis(decode_responses=False)

    backend = RedisBackend(redis_url="redis://localhost:6379/0")
    backend.client = fake_redis

    yield backend

    await fake_redis.aclose()


@pytest.mark.asyncio
async def test_store_and_fetch_events(redis_backend):
    """Test storing and fetching events."""
    # Create test events
    events = [
        UsageEvent(
            project_name="test_project",
            request_type="chat",
            input_tokens=100,
            output_tokens=50,
            timestamp=datetime.now(timezone.utc),
        ),
        UsageEvent(
            project_name="test_project",
            request_type="embedding",
            input_tokens=200,
            output_tokens=0,
            timestamp=datetime.now(timezone.utc),
        ),
    ]

    # Store events
    await redis_backend.log_many(events)

    # Fetch events
    filters = UsageFilter(project_name="test_project")
    fetched_events, cursor = await redis_backend.fetch_raw(filters)

    assert len(fetched_events) == 2
    assert fetched_events[0].project_name == "test_project"


@pytest.mark.asyncio
async def test_daily_aggregates(redis_backend):
    """Test daily aggregate summaries."""
    # Create events across two days
    today = datetime.now(timezone.utc).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    yesterday = today - timedelta(days=1)

    events = [
        UsageEvent(
            project_name="test_project",
            request_type="chat",
            input_tokens=100,
            output_tokens=50,
            timestamp=today,
        ),
        UsageEvent(
            project_name="test_project",
            request_type="chat",
            input_tokens=200,
            output_tokens=100,
            timestamp=yesterday,
        ),
    ]

    await redis_backend.log_many(events)

    # Get daily summary
    spec = AggregateSpec()
    filters = UsageFilter(
        project_name="test_project",
        time_from=yesterday - timedelta(days=1),
        time_to=today + timedelta(days=1),
    )

    buckets = await redis_backend.summary_by_day(spec, filters)

    assert len(buckets) == 2
    assert buckets[0].metrics["sum_total"] == 300  # yesterday
    assert buckets[1].metrics["sum_total"] == 150  # today


@pytest.mark.asyncio
async def test_project_summary(redis_backend):
    """Test project-level summaries."""
    events = [
        UsageEvent(
            project_name="project_a",
            request_type="chat",
            input_tokens=100,
            output_tokens=50,
        ),
        UsageEvent(
            project_name="project_b",
            request_type="chat",
            input_tokens=200,
            output_tokens=100,
        ),
        UsageEvent(
            project_name="project_a",
            request_type="embedding",
            input_tokens=50,
            output_tokens=0,
        ),
    ]

    await redis_backend.log_many(events)

    # Get project summary
    spec = AggregateSpec()
    filters = UsageFilter()

    summaries = await redis_backend.summary_by_project(spec, filters)

    assert len(summaries) >= 2

    project_a = next(
        s for s in summaries if s.group_keys["project_name"] == "project_a"
    )
    assert project_a.metrics["sum_total"] == 200  # 150 + 50


@pytest.mark.asyncio
async def test_delete_project(redis_backend):
    """Test project deletion."""
    events = [
        UsageEvent(
            project_name="to_delete",
            request_type="chat",
            input_tokens=100,
            output_tokens=50,
        ),
        UsageEvent(
            project_name="to_keep",
            request_type="chat",
            input_tokens=200,
            output_tokens=100,
        ),
    ]

    await redis_backend.log_many(events)

    # Delete project
    options = DeleteOptions(project_name="to_delete", simulate=False)
    result = await redis_backend.delete_project(options)

    assert result.events_deleted >= 1

    # Verify deletion
    filters = UsageFilter(project_name="to_delete")
    fetched, _ = await redis_backend.fetch_raw(filters)
    assert len(fetched) == 0

    # Verify other project still exists
    filters = UsageFilter(project_name="to_keep")
    fetched, _ = await redis_backend.fetch_raw(filters)
    assert len(fetched) == 1


@pytest.mark.asyncio
async def test_client_lifecycle():
    """Test client lifecycle and async operations."""
    settings = Settings(backend=BackendType.REDIS, redis_url="redis://localhost:6379/0")

    # Create client with fake Redis
    client = TokenUsageClient(settings)
    client.backend = RedisBackend(redis_url=settings.redis_url)
    client.backend.client = FakeAsyncRedis(decode_responses=False)

    await client.start()

    # Log event
    event = UsageEvent(
        project_name="test",
        request_type="chat",
        input_tokens=100,
        output_tokens=50,
    )

    await client.log(event)

    # Flush
    flushed = await client.flush(timeout=5.0)
    assert flushed >= 0

    # Check health
    health = await client.health_check()
    assert health is True

    # Get stats
    stats = client.get_stats()
    assert stats["started"] is True

    await client.aclose()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
