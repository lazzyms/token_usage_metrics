"""Abstract backend interface for token usage storage."""

from abc import ABC, abstractmethod
from typing import Protocol

from token_usage_metrics.models import (
    AggregateSpec,
    DeleteOptions,
    DeleteResult,
    SummaryRow,
    TimeBucket,
    UsageEvent,
    UsageFilter,
)


class Backend(ABC):
    """Abstract backend interface for storing and retrieving token usage data."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the backend."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the backend."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if backend is healthy and accessible."""
        pass

    @abstractmethod
    async def log_many(self, events: list[UsageEvent]) -> None:
        """Store multiple usage events."""
        pass

    @abstractmethod
    async def fetch_raw(
        self, filters: UsageFilter
    ) -> tuple[list[UsageEvent], str | None]:
        """
        Fetch raw usage events with filters.

        Returns:
            Tuple of (events list, next cursor or None)
        """
        pass

    @abstractmethod
    async def summary_by_day(
        self, spec: AggregateSpec, filters: UsageFilter
    ) -> list[TimeBucket]:
        """Get time-bucketed aggregates by day."""
        pass

    @abstractmethod
    async def summary_by_project(
        self, spec: AggregateSpec, filters: UsageFilter
    ) -> list[SummaryRow]:
        """Get aggregated summary grouped by project."""
        pass

    @abstractmethod
    async def summary_by_request_type(
        self, spec: AggregateSpec, filters: UsageFilter
    ) -> list[SummaryRow]:
        """Get aggregated summary grouped by request type."""
        pass

    @abstractmethod
    async def timeseries(
        self, spec: AggregateSpec, filters: UsageFilter
    ) -> list[TimeBucket]:
        """Get time-series data for graphing."""
        pass

    @abstractmethod
    async def delete_project(self, options: DeleteOptions) -> DeleteResult:
        """Delete usage data for a project with optional time range."""
        pass
