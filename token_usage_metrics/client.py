"""Main client API for token usage metrics."""

from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from token_usage_metrics.backends.base import Backend
from token_usage_metrics.backends.mongodb import MongoDBBackend
from token_usage_metrics.backends.postgres import PostgresBackend
from token_usage_metrics.backends.redis import RedisBackend
from token_usage_metrics.backends.supabase import SupabaseBackend
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

    @classmethod
    async def init(
        cls,
        connection_string: str | None = None,
        *,
        backend: str | None = None,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        database: str | None = None,
        **kwargs: Any,
    ) -> "TokenUsageClient":
        """Create and start a client with simplified initialization.

        Args:
            connection_string: Full connection URL (e.g., 'redis://localhost:6379/0')
            backend: Backend type ('redis', 'postgres', 'mongodb')
            host: Database host
            port: Database port
            username: Database username
            password: Database password
            database: Database name or number
            **kwargs: Additional settings (buffer_size, flush_interval, etc.)

        Returns:
            Started TokenUsageClient instance

        Examples:
            # Using connection string
            client = await TokenUsageClient.init("redis://localhost:6379/0")

            # Using individual parameters
            client = await TokenUsageClient.init(
                backend="postgres",
                host="localhost",
                port=5432,
                username="user",
                password="pass",
                database="token_usage"
            )
        """
        settings_dict: dict[str, Any] = {}

        if connection_string:
            # Parse connection string
            parsed = urlparse(connection_string)
            backend = parsed.scheme

            if backend == "redis":
                settings_dict["backend"] = BackendType.REDIS
                settings_dict["redis_url"] = connection_string
            elif backend in ("postgresql", "postgres"):
                settings_dict["backend"] = BackendType.POSTGRES
                settings_dict["postgres_dsn"] = connection_string
            elif backend == "supabase":
                settings_dict["backend"] = BackendType.SUPABASE
                settings_dict["supabase_dsn"] = connection_string
            elif backend == "mongodb":
                settings_dict["backend"] = BackendType.MONGODB
                settings_dict["mongodb_url"] = connection_string
                # Extract database from path
                if parsed.path and len(parsed.path) > 1:
                    settings_dict["mongodb_database"] = parsed.path.lstrip("/")
            else:
                raise ValueError(f"Unsupported backend in connection string: {backend}")
        else:
            # Build connection string from parameters
            if not backend:
                raise ValueError("Either connection_string or backend must be provided")

            backend = backend.lower()

            if backend == "redis":
                settings_dict["backend"] = BackendType.REDIS
                host = host or "localhost"
                port = port or 6379
                database = database or "0"

                if username and password:
                    settings_dict["redis_url"] = (
                        f"redis://{username}:{password}@{host}:{port}/{database}"
                    )
                else:
                    settings_dict["redis_url"] = f"redis://{host}:{port}/{database}"

            elif backend in ("postgresql", "postgres"):
                settings_dict["backend"] = BackendType.POSTGRES
                host = host or "localhost"
                port = port or 5432
                database = database or "token_usage"

                if username and password:
                    settings_dict["postgres_dsn"] = (
                        f"postgresql://{username}:{password}@{host}:{port}/{database}"
                    )
                else:
                    settings_dict["postgres_dsn"] = (
                        f"postgresql://{host}:{port}/{database}"
                    )

            elif backend == "mongodb":
                settings_dict["backend"] = BackendType.MONGODB
                host = host or "localhost"
                port = port or 27017
                database = database or "token_usage"

                if username and password:
                    settings_dict["mongodb_url"] = (
                        f"mongodb://{username}:{password}@{host}:{port}"
                    )
                else:
                    settings_dict["mongodb_url"] = f"mongodb://{host}:{port}"
                settings_dict["mongodb_database"] = database
            elif backend == "supabase":
                settings_dict["backend"] = BackendType.SUPABASE
                host = host or "localhost"
                port = port or 5432
                database = database or "token_usage"

                if username and password:
                    settings_dict["supabase_dsn"] = (
                        f"postgresql://{username}:{password}@{host}:{port}/{database}"
                    )
                else:
                    settings_dict["supabase_dsn"] = (
                        f"postgresql://{host}:{port}/{database}"
                    )
            else:
                raise ValueError(f"Unsupported backend: {backend}")

        # Merge additional kwargs
        settings_dict.update(kwargs)

        # Create settings and client
        settings = Settings(**settings_dict)
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
        elif self.settings.backend == BackendType.SUPABASE:
            return SupabaseBackend(
                supabase_dsn=self.settings.supabase_dsn,
                min_size=self.settings.supabase_pool_min_size,
                max_size=self.settings.supabase_pool_max_size,
                command_timeout=self.settings.supabase_command_timeout,
            )
        else:
            raise ValueError(f"Unknown backend type: {self.settings.backend}")

    async def log(
        self,
        project_name: str | UsageEvent,
        request_type: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        *,
        metadata: dict[str, Any] | None = None,
        request_count: int = 1,
    ) -> None:
        """Log a single usage event (async, non-blocking).

        Args:
            project_name: Name of the project/application
            request_type: Type of request (e.g., 'chat', 'completion', 'embedding')
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            metadata: Optional additional metadata
            request_count: Number of requests (default: 1)
        """
        if not self._started:
            raise BackendError("Client not started. Call start() first.")

        if not self.queue:
            raise BackendError("Queue not initialized")

        if isinstance(project_name, UsageEvent):
            event = project_name
        else:
            if request_type is None or input_tokens is None or output_tokens is None:
                raise BackendError(
                    "Missing required params: request_type/input_tokens/output_tokens"
                )

            event = UsageEvent(
                project_name=project_name,
                request_type=request_type,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                metadata=metadata,
                request_count=request_count,
            )
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

    async def query(
        self,
        *,
        project: str | None = None,
        request_type: str | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[UsageEvent], str | None]:
        """Query usage events with simplified parameters.

        Args:
            project: Filter by project name
            request_type: Filter by request type
            time_from: Filter events from this timestamp
            time_to: Filter events until this timestamp
            limit: Maximum number of events to return
            cursor: Pagination cursor from previous query

        Returns:
            Tuple of (events, next_cursor)
        """
        if not self._started:
            raise BackendError("Client not started. Call start() first.")

        if not self.backend:
            raise BackendError("Backend not initialized")

        filters = UsageFilter(
            project_name=project,
            request_type=request_type,
            time_from=time_from,
            time_to=time_to,
            limit=limit,
            cursor=cursor,
        )
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

    async def aggregate(
        self,
        *,
        group_by: str | None = None,
        metrics: list[str] | None = None,
        project: str | None = None,
        request_type: str | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
    ) -> list[TimeBucket] | list[SummaryRow]:
        """Get aggregated metrics with simplified parameters.

        Args:
            group_by: Group results by 'day', 'project', or 'type' (None for overall)
            metrics: List of metrics like ['sum_total', 'count_requests', 'avg_total']
            project: Filter by project name
            request_type: Filter by request type
            time_from: Filter events from this timestamp
            time_to: Filter events until this timestamp

        Returns:
            List of TimeBucket (for time grouping) or SummaryRow (for other grouping)
        """
        if not self._started:
            raise BackendError("Client not started. Call start() first.")

        if not self.backend:
            raise BackendError("Backend not initialized")

        # Build AggregateSpec from string metrics
        from token_usage_metrics.models import (
            AggregateMetric,
            GroupByDimension,
            TimeBucketType,
        )

        metric_set = set()
        if metrics:
            metric_map = {
                "sum_total": AggregateMetric.SUM_TOTAL,
                "sum_input": AggregateMetric.SUM_INPUT,
                "sum_output": AggregateMetric.SUM_OUTPUT,
                "count_requests": AggregateMetric.COUNT_REQUESTS,
                "avg_total_per_request": AggregateMetric.AVG_TOTAL_PER_REQUEST,
            }
            for m in metrics:
                if m not in metric_map:
                    raise ValueError(
                        f"Unknown metric: {m}. Valid: {list(metric_map.keys())}"
                    )
                metric_set.add(metric_map[m])
        else:
            # Default metrics
            metric_set = {AggregateMetric.SUM_TOTAL, AggregateMetric.COUNT_REQUESTS}

        # Build UsageFilter
        filters = UsageFilter(
            project_name=project,
            request_type=request_type,
            time_from=time_from,
            time_to=time_to,
        )

        # Route to appropriate method based on group_by
        if group_by == "day":
            spec = AggregateSpec(
                metrics=metric_set,
                group_by=GroupByDimension.NONE,
                bucket=TimeBucketType.DAY,
            )
            return await self.backend.summary_by_day(spec, filters)
        elif group_by == "project":
            spec = AggregateSpec(
                metrics=metric_set,
                group_by=GroupByDimension.PROJECT,
            )
            return await self.backend.summary_by_project(spec, filters)
        elif group_by == "type":
            spec = AggregateSpec(
                metrics=metric_set,
                group_by=GroupByDimension.REQUEST_TYPE,
            )
            return await self.backend.summary_by_request_type(spec, filters)
        elif group_by is None:
            # Overall aggregate
            spec = AggregateSpec(
                metrics=metric_set,
                group_by=GroupByDimension.NONE,
            )
            return await self.backend.summary_by_project(spec, filters)
        else:
            raise ValueError(
                f"Invalid group_by: {group_by}. Valid: 'day', 'project', 'type', or None"
            )

    async def delete_project(self, options: DeleteOptions) -> DeleteResult:
        """Delete usage data for a project."""
        if not self._started:
            raise BackendError("Client not started. Call start() first.")

        if not self.backend:
            raise BackendError("Backend not initialized")

        return await self.backend.delete_project(options)

    async def delete(
        self,
        project: str,
        *,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        include_aggregates: bool = True,
    ) -> DeleteResult:
        """Delete usage data for a project with simplified parameters.

        Args:
            project: Project name to delete data for
            time_from: Delete events from this timestamp (None = no lower bound)
            time_to: Delete events until this timestamp (None = no upper bound)
            include_aggregates: Also delete aggregated data

        Returns:
            DeleteResult with counts of deleted records
        """
        if not self._started:
            raise BackendError("Client not started. Call start() first.")

        if not self.backend:
            raise BackendError("Backend not initialized")

        options = DeleteOptions(
            project_name=project,
            time_from=time_from,
            time_to=time_to,
            include_aggregates=include_aggregates,
            simulate=False,
        )
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
