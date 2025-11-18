"""Core data models for token usage tracking."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class TimeBucketType(str, Enum):
    """Time bucket granularity for aggregations."""

    DAY = "day"
    HOUR = "hour"
    WEEK = "week"


class AggregateMetric(str, Enum):
    """Available aggregate metrics."""

    SUM_INPUT = "sum_input"
    SUM_OUTPUT = "sum_output"
    SUM_TOTAL = "sum_total"
    COUNT_REQUESTS = "count_requests"
    AVG_TOTAL_PER_REQUEST = "avg_total_per_request"


class GroupByDimension(str, Enum):
    """Available grouping dimensions."""

    NONE = "none"
    PROJECT = "project_name"
    REQUEST_TYPE = "request_type"
    PROJECT_AND_TYPE = "project_and_type"


class UsageEvent(BaseModel):
    """Token usage event with validation and auto-derivation."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_name: str = Field(..., min_length=1, max_length=128)
    request_type: str = Field(..., min_length=1, max_length=64)
    input_tokens: int = Field(..., ge=0)
    output_tokens: int = Field(..., ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    request_count: int = Field(default=1, ge=1)
    metadata: dict[str, Any] | None = Field(default=None)

    @field_validator("timestamp")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        """Ensure timestamp is timezone-aware UTC."""
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @model_validator(mode="after")
    def derive_total(self) -> "UsageEvent":
        """Auto-derive total_tokens if not provided."""
        if self.total_tokens is None:
            self.total_tokens = self.input_tokens + self.output_tokens
        return self

    @model_validator(mode="after")
    def validate_metadata_size(self) -> "UsageEvent":
        """Ensure metadata is not too large."""
        if self.metadata:
            import json

            size = len(json.dumps(self.metadata))
            if size > 4096:  # 4KB limit
                raise ValueError(f"metadata size {size} exceeds 4KB limit")
        return self

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for storage."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "project_name": self.project_name,
            "request_type": self.request_type,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens or 0,
            "request_count": self.request_count,
            "metadata": self.metadata,
        }


class UsageFilter(BaseModel):
    """Filter parameters for fetching usage data."""

    project_name: str | None = None
    request_type: str | None = None
    time_from: datetime | None = None
    time_to: datetime | None = None
    limit: int = Field(default=100, ge=1, le=10000)
    cursor: str | None = None

    @field_validator("time_from", "time_to")
    @classmethod
    def ensure_utc_filter(cls, v: datetime | None) -> datetime | None:
        """Ensure filter timestamps are UTC."""
        if v and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc) if v else None


class AggregateSpec(BaseModel):
    """Specification for aggregate queries."""

    metrics: set[AggregateMetric] = Field(
        default_factory=lambda: {
            AggregateMetric.SUM_INPUT,
            AggregateMetric.SUM_OUTPUT,
            AggregateMetric.SUM_TOTAL,
            AggregateMetric.COUNT_REQUESTS,
        }
    )
    group_by: GroupByDimension = Field(default=GroupByDimension.NONE)
    bucket: TimeBucketType = Field(default=TimeBucketType.DAY)


class TimeBucket(BaseModel):
    """Time-bucketed aggregate result."""

    start: datetime
    end: datetime
    metrics: dict[str, int | float]
    group_keys: dict[str, str] | None = None

    @field_validator("start", "end")
    @classmethod
    def ensure_utc_bucket(cls, v: datetime) -> datetime:
        """Ensure bucket timestamps are UTC."""
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


class SummaryRow(BaseModel):
    """Grouped aggregate summary row."""

    group_keys: dict[str, str]
    metrics: dict[str, int | float]


class DeleteOptions(BaseModel):
    """Options for project deletion."""

    project_name: str = Field(..., min_length=1)
    time_from: datetime | None = None
    time_to: datetime | None = None
    include_aggregates: bool = Field(default=True)
    simulate: bool = Field(default=False)

    @field_validator("time_from", "time_to")
    @classmethod
    def ensure_utc_delete(cls, v: datetime | None) -> datetime | None:
        """Ensure deletion timestamps are UTC."""
        if v and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc) if v else None


class DeleteResult(BaseModel):
    """Result of a deletion operation."""

    events_deleted: int
    aggregates_deleted: int
    simulated: bool
