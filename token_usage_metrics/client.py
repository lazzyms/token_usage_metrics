"""Main client API for token usage metrics."""

from typing import Any

from token_usage_metrics.backends.base import Backend
from token_usage_metrics.backends.mongodb import MongoDBBackend
from token_usage_metrics.backends.postgres import PostgresBackend
from token_usage_metrics.backends.redis import RedisBackend
from token_usage_metrics.config import BackendType, Settings
from token_usage_metrics.errors import BackendError
from token_usage_metrics.logging import get_logger
from token_usage_metrics.models import (
    AggregateSpec,
    DeleteOptions,
    DeleteResult,
    SummaryRow,
    TimeBucket,
    UsageEvent,
    UsageFilter,
)
from token_usage_metrics.queue import AsyncEventQueue, CircuitBreaker

logger = get_logger(__name__)


class TokenUsageClient:
    """Main client for logging and querying token usage."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.backend: Backend | None = None
        self.queue: AsyncEventQueue | None = None
        self._started = False

    @classmethod
    async def from_settings(
        cls, settings: Settings | None = None
    ) -> "TokenUsageClient":
        """Create and start a client from settings."""
        client = cls(settings)
        await client.start()
        return client

    async def start(self) -> None:
        """Initialize backend and start async queue."""
        if self._started:
            return

        # Create backend
        self.backend = self._create_backend()

        # Connect to backend
        await self.backend.connect()

        # Create circuit breaker
        circuit_breaker = CircuitBreaker(
            threshold=self.settings.circuit_breaker_threshold,
            timeout=self.settings.circuit_breaker_timeout,
        )

        # Create async queue
        self.queue = AsyncEventQueue(
            flush_callback=self._flush_callback,
            buffer_size=self.settings.buffer_size,
            flush_interval=self.settings.flush_interval,
            flush_batch_size=self.settings.flush_batch_size,
            drop_policy=self.settings.drop_policy,
            circuit_breaker=circuit_breaker,
        )

        # Start background flusher
        await self.queue.start()

        self._started = True
        logger.info(
            "TokenUsageClient started",
            extra={"backend": self.settings.backend.value},
        )

    async def _flush_callback(self, events: list[UsageEvent]) -> None:
        """Callback for flushing events to backend."""
        if not self.backend:
            raise BackendError("Backend not initialized")

        await self.backend.log_many(events)

    def _create_backend(self) -> Backend:
        """Create backend based on settings."""
        if self.settings.backend == BackendType.REDIS:
            return RedisBackend(
                redis_url=self.settings.redis_url,
                pool_size=self.settings.redis_pool_size,
                socket_timeout=self.settings.redis_socket_timeout,
                socket_connect_timeout=self.settings.redis_socket_connect_timeout,
            )
        elif self.settings.backend == BackendType.POSTGRES:
            return PostgresBackend(
                dsn=self.settings.postgres_dsn,
                min_size=self.settings.postgres_pool_min_size,
                max_size=self.settings.postgres_pool_max_size,
                command_timeout=self.settings.postgres_command_timeout,
            )
        elif self.settings.backend == BackendType.MONGODB:
            return MongoDBBackend(
                url=self.settings.mongodb_url,
                database=self.settings.mongodb_database,
                max_pool_size=self.settings.mongodb_max_pool_size,
                timeout=self.settings.mongodb_timeout,
            )
        else:
            raise ValueError(f"Unknown backend type: {self.settings.backend}")

    async def log(self, event: UsageEvent) -> None:
        """Log a single usage event (async, non-blocking)."""
        if not self._started:
            raise BackendError("Client not started. Call start() first.")

        if not self.queue:
            raise BackendError("Queue not initialized")

        await self.queue.enqueue(event)

    async def log_many(self, events: list[UsageEvent]) -> None:
        """Log multiple usage events (async, non-blocking)."""
        if not self._started:
            raise BackendError("Client not started. Call start() first.")

        if not self.queue:
            raise BackendError("Queue not initialized")

        for event in events:
            await self.queue.enqueue(event)

    async def fetch_raw(
        self, filters: UsageFilter | None = None
    ) -> tuple[list[UsageEvent], str | None]:
        """Fetch raw usage events with filters."""
        if not self._started:
            raise BackendError("Client not started. Call start() first.")

        if not self.backend:
            raise BackendError("Backend not initialized")

        filters = filters or UsageFilter()
        return await self.backend.fetch_raw(filters)

    async def summary_by_day(
        self,
        spec: AggregateSpec | None = None,
        filters: UsageFilter | None = None,
    ) -> list[TimeBucket]:
        """Get time-bucketed aggregates by day."""
        if not self._started:
            raise BackendError("Client not started. Call start() first.")

        if not self.backend:
            raise BackendError("Backend not initialized")

        spec = spec or AggregateSpec()
        filters = filters or UsageFilter()
        return await self.backend.summary_by_day(spec, filters)

    async def summary_by_project(
        self,
        spec: AggregateSpec | None = None,
        filters: UsageFilter | None = None,
    ) -> list[SummaryRow]:
        """Get aggregated summary grouped by project."""
        if not self._started:
            raise BackendError("Client not started. Call start() first.")

        if not self.backend:
            raise BackendError("Backend not initialized")

        spec = spec or AggregateSpec()
        filters = filters or UsageFilter()
        return await self.backend.summary_by_project(spec, filters)

    async def summary_by_request_type(
        self,
        spec: AggregateSpec | None = None,
        filters: UsageFilter | None = None,
    ) -> list[SummaryRow]:
        """Get aggregated summary grouped by request type."""
        if not self._started:
            raise BackendError("Client not started. Call start() first.")

        if not self.backend:
            raise BackendError("Backend not initialized")

        spec = spec or AggregateSpec()
        filters = filters or UsageFilter()
        return await self.backend.summary_by_request_type(spec, filters)

    async def timeseries(
        self,
        spec: AggregateSpec | None = None,
        filters: UsageFilter | None = None,
    ) -> list[TimeBucket]:
        """Get time-series data for graphing."""
        if not self._started:
            raise BackendError("Client not started. Call start() first.")

        if not self.backend:
            raise BackendError("Backend not initialized")

        spec = spec or AggregateSpec()
        filters = filters or UsageFilter()
        return await self.backend.timeseries(spec, filters)

    async def delete_project(self, options: DeleteOptions) -> DeleteResult:
        """Delete usage data for a project."""
        if not self._started:
            raise BackendError("Client not started. Call start() first.")

        if not self.backend:
            raise BackendError("Backend not initialized")

        return await self.backend.delete_project(options)

    async def flush(self, timeout: float | None = None) -> int:
        """Flush all pending events."""
        if not self._started:
            raise BackendError("Client not started. Call start() first.")

        if not self.queue:
            raise BackendError("Queue not initialized")

        return await self.queue.flush(timeout=timeout)

    async def health_check(self) -> bool:
        """Check if backend is healthy."""
        if not self._started or not self.backend:
            return False

        return await self.backend.health_check()

    def get_stats(self) -> dict[str, Any]:
        """Get client statistics."""
        if not self.queue:
            return {"started": self._started, "backend": self.settings.backend.value}

        return {
            "started": self._started,
            "backend": self.settings.backend.value,
            **self.queue.get_stats(),
        }

    async def aclose(self) -> None:
        """Close client and flush remaining events."""
        if not self._started:
            return

        # Stop queue and flush
        if self.queue:
            await self.queue.stop(
                timeout=(
                    self.settings.command_timeout
                    if hasattr(self.settings, "command_timeout")
                    else 30.0
                )
            )

        # Disconnect backend
        if self.backend:
            await self.backend.disconnect()

        self._started = False
        logger.info("TokenUsageClient closed")

    async def __aenter__(self) -> "TokenUsageClient":
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.aclose()
