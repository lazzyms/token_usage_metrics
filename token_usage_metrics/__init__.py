"""Async LLM/embedding token usage tracking with multi-backend support."""

from token_usage_metrics.client import TokenUsageClient

# Advanced imports (for power users)
from token_usage_metrics.config import Settings
from token_usage_metrics.models import (
    AggregateMetric,
    AggregateSpec,
    DeleteOptions,
    DeleteResult,
    GroupByDimension,
    SummaryRow,
    TimeBucket,
    TimeBucketType,
    UsageEvent,
    UsageFilter,
)

__version__ = "0.1.0"

# Primary API - simplified SDK interface
__all__ = [
    "TokenUsageClient",
]

# Advanced API - for users needing fine-grained control
__all_advanced__ = [
    "Settings",
    "UsageEvent",
    "UsageFilter",
    "AggregateSpec",
    "AggregateMetric",
    "GroupByDimension",
    "TimeBucket",
    "TimeBucketType",
    "SummaryRow",
    "DeleteOptions",
    "DeleteResult",
]
