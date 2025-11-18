"""Simple validation test for package imports and basic functionality."""

from datetime import datetime, timezone

from token_usage_metrics import (
    AggregateMetric,
    AggregateSpec,
    DeleteOptions,
    GroupByDimension,
    Settings,
    SummaryRow,
    TimeBucket,
    UsageEvent,
    UsageFilter,
)
from token_usage_metrics.config import BackendType


def test_imports():
    """Test that all core imports work."""
    assert UsageEvent is not None
    assert UsageFilter is not None
    assert AggregateSpec is not None
    assert Settings is not None
    assert DeleteOptions is not None


def test_usage_event_validation():
    """Test UsageEvent validation and auto-derivation."""
    # Test auto-derive total_tokens
    event = UsageEvent(
        project_name="test",
        request_type="chat",
        input_tokens=100,
        output_tokens=50,
    )

    assert event.total_tokens == 150
    assert event.request_count == 1
    assert event.timestamp.tzinfo is not None  # UTC


def test_usage_filter():
    """Test UsageFilter creation."""
    filters = UsageFilter(
        project_name="test_project",
        request_type="chat",
        limit=50,
    )

    assert filters.project_name == "test_project"
    assert filters.request_type == "chat"
    assert filters.limit == 50


def test_aggregate_spec():
    """Test AggregateSpec with metrics."""
    spec = AggregateSpec(
        metrics={AggregateMetric.SUM_TOTAL, AggregateMetric.COUNT_REQUESTS},
        group_by=GroupByDimension.PROJECT,
    )

    assert AggregateMetric.SUM_TOTAL in spec.metrics
    assert spec.group_by == GroupByDimension.PROJECT


def test_settings():
    """Test Settings configuration."""
    settings = Settings(
        backend=BackendType.REDIS,
        redis_url="redis://localhost:6379/0",
        buffer_size=500,
    )

    assert settings.backend == BackendType.REDIS
    assert settings.buffer_size == 500

    supabase_settings = Settings(
        backend=BackendType.SUPABASE,
        supabase_dsn="postgresql://localhost:5432/token_usage",
    )

    assert supabase_settings.backend == BackendType.SUPABASE
    assert supabase_settings.supabase_dsn.startswith("postgresql://")


def test_delete_options():
    """Test DeleteOptions."""
    options = DeleteOptions(
        project_name="test_project",
        simulate=True,
    )

    assert options.project_name == "test_project"
    assert options.simulate is True
    assert options.include_aggregates is True


def test_time_bucket():
    """Test TimeBucket model."""
    now = datetime.now(timezone.utc)
    bucket = TimeBucket(
        start=now,
        end=now,
        metrics={"sum_total": 1000, "count_requests": 10},
    )

    assert bucket.metrics["sum_total"] == 1000


def test_summary_row():
    """Test SummaryRow model."""
    row = SummaryRow(
        group_keys={"project_name": "test"},
        metrics={"sum_total": 500, "avg_total_per_request": 50.0},
    )

    assert row.group_keys["project_name"] == "test"
    assert row.metrics["avg_total_per_request"] == 50.0


def test_event_to_dict():
    """Test UsageEvent serialization."""
    event = UsageEvent(
        project_name="test",
        request_type="chat",
        input_tokens=100,
        output_tokens=50,
        metadata={"model": "gpt-4"},
    )

    data = event.to_dict()
    assert data["project_name"] == "test"
    assert data["input_tokens"] == 100
    assert data["metadata"]["model"] == "gpt-4"


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
