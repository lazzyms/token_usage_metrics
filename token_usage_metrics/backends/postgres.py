"""Postgres backend implementation for token usage metrics."""

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from token_usage_metrics.backends.base import Backend
from token_usage_metrics.errors import BackendError, ConnectionError as ConnError
from token_usage_metrics.logging import get_logger
from token_usage_metrics.models import (
    AggregateMetric,
    AggregateSpec,
    DeleteOptions,
    DeleteResult,
    GroupByDimension,
    SummaryRow,
    TimeBucket,
    UsageEvent,
    UsageFilter,
)

logger = get_logger(__name__)


class PostgresBackend(Backend):
    """Postgres backend with events table and daily aggregates."""

    def __init__(
        self,
        dsn: str,
        min_size: int = 2,
        max_size: int = 10,
        command_timeout: float = 60.0,
    ):
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self.command_timeout = command_timeout
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """Establish Postgres connection and create schema."""
        try:
            self.pool = await asyncpg.create_pool(
                self.dsn,
                min_size=self.min_size,
                max_size=self.max_size,
                command_timeout=self.command_timeout,
            )

            # Create tables if they don't exist
            await self._init_schema()

            logger.info("Postgres backend connected")

        except Exception as e:
            logger.error(f"Failed to connect to Postgres: {e}", extra={"error": str(e)})
            raise ConnError(f"Postgres connection failed: {e}") from e

    async def _init_schema(self) -> None:
        """Initialize database schema."""
        if not self.pool:
            raise BackendError("Pool not initialized")

        async with self.pool.acquire() as conn:
            # Events table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_events (
                    id TEXT PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL,
                    project_name TEXT NOT NULL,
                    request_type TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    request_count INTEGER NOT NULL,
                    metadata JSONB
                )
                """
            )

            # Indexes for efficient querying
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_usage_events_timestamp
                ON usage_events (timestamp)
                """
            )

            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_usage_events_project_ts
                ON usage_events (project_name, timestamp)
                """
            )

            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_usage_events_type_ts
                ON usage_events (request_type, timestamp)
                """
            )

            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_usage_events_project_type_ts
                ON usage_events (project_name, request_type, timestamp)
                """
            )

            # Daily aggregates table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_aggregates (
                    date DATE NOT NULL,
                    project_name TEXT,
                    request_type TEXT,
                    input_tokens BIGINT NOT NULL DEFAULT 0,
                    output_tokens BIGINT NOT NULL DEFAULT 0,
                    total_tokens BIGINT NOT NULL DEFAULT 0,
                    request_count BIGINT NOT NULL DEFAULT 0,
                    PRIMARY KEY (date, project_name, request_type)
                )
                """
            )

            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_daily_aggregates_date
                ON daily_aggregates (date)
                """
            )

            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_daily_aggregates_project_date
                ON daily_aggregates (project_name, date)
                """
            )

            logger.info("Postgres schema initialized")

    async def disconnect(self) -> None:
        """Close Postgres connection."""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("Postgres backend disconnected")

    async def health_check(self) -> bool:
        """Check Postgres health."""
        try:
            if not self.pool:
                return False
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False

    async def log_many(self, events: list[UsageEvent]) -> None:
        """Store multiple events."""
        if not self.pool:
            raise BackendError("Pool not initialized")

        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Insert events
                    await conn.executemany(
                        """
                        INSERT INTO usage_events
                        (id, timestamp, project_name, request_type, input_tokens,
                         output_tokens, total_tokens, request_count, metadata)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        [
                            (
                                e.id,
                                e.timestamp,
                                e.project_name,
                                e.request_type,
                                e.input_tokens,
                                e.output_tokens,
                                e.total_tokens or 0,
                                e.request_count,
                                json.dumps(e.metadata) if e.metadata else None,
                            )
                            for e in events
                        ],
                    )

                    # Update daily aggregates
                    for event in events:
                        await conn.execute(
                            """
                            INSERT INTO daily_aggregates
                            (date, project_name, request_type, input_tokens,
                             output_tokens, total_tokens, request_count)
                            VALUES ($1, $2, $3, $4, $5, $6, $7)
                            ON CONFLICT (date, project_name, request_type)
                            DO UPDATE SET
                                input_tokens = daily_aggregates.input_tokens + EXCLUDED.input_tokens,
                                output_tokens = daily_aggregates.output_tokens + EXCLUDED.output_tokens,
                                total_tokens = daily_aggregates.total_tokens + EXCLUDED.total_tokens,
                                request_count = daily_aggregates.request_count + EXCLUDED.request_count
                            """,
                            event.timestamp.date(),
                            event.project_name,
                            event.request_type,
                            event.input_tokens,
                            event.output_tokens,
                            event.total_tokens or 0,
                            event.request_count,
                        )

            logger.debug(
                f"Stored {len(events)} events in Postgres", extra={"count": len(events)}
            )

        except Exception as e:
            logger.error(f"Failed to store events: {e}", extra={"error": str(e)})
            raise BackendError(f"Failed to store events: {e}") from e

    async def fetch_raw(
        self, filters: UsageFilter
    ) -> tuple[list[UsageEvent], str | None]:
        """Fetch raw events with filters."""
        if not self.pool:
            raise BackendError("Pool not initialized")

        try:
            conditions = ["TRUE"]
            params: list[Any] = []
            param_idx = 1

            if filters.project_name:
                conditions.append(f"project_name = ${param_idx}")
                params.append(filters.project_name)
                param_idx += 1

            if filters.request_type:
                conditions.append(f"request_type = ${param_idx}")
                params.append(filters.request_type)
                param_idx += 1

            if filters.time_from:
                conditions.append(f"timestamp >= ${param_idx}")
                params.append(filters.time_from)
                param_idx += 1

            if filters.time_to:
                conditions.append(f"timestamp < ${param_idx}")
                params.append(filters.time_to)
                param_idx += 1

            query = f"""
                SELECT id, timestamp, project_name, request_type,
                       input_tokens, output_tokens, total_tokens,
                       request_count, metadata
                FROM usage_events
                WHERE {' AND '.join(conditions)}
                ORDER BY timestamp, id
                LIMIT ${param_idx}
            """
            params.append(filters.limit + 1)  # Fetch one extra for cursor

            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query, *params)

            events = []
            for row in rows[: filters.limit]:
                metadata = json.loads(row["metadata"]) if row["metadata"] else None
                event = UsageEvent(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    project_name=row["project_name"],
                    request_type=row["request_type"],
                    input_tokens=row["input_tokens"],
                    output_tokens=row["output_tokens"],
                    total_tokens=row["total_tokens"],
                    request_count=row["request_count"],
                    metadata=metadata,
                )
                events.append(event)

            # Generate cursor if more results exist
            next_cursor = None
            if len(rows) > filters.limit:
                last_event = rows[filters.limit - 1]
                next_cursor = (
                    f"{last_event['timestamp'].isoformat()}:{last_event['id']}"
                )

            return events, next_cursor

        except Exception as e:
            logger.error(f"Failed to fetch events: {e}", extra={"error": str(e)})
            raise BackendError(f"Failed to fetch events: {e}") from e

    async def summary_by_day(
        self, spec: AggregateSpec, filters: UsageFilter
    ) -> list[TimeBucket]:
        """Get time-bucketed aggregates by day."""
        if not self.pool:
            raise BackendError("Pool not initialized")

        try:
            conditions = ["TRUE"]
            params: list[Any] = []
            param_idx = 1

            if filters.project_name:
                conditions.append(f"project_name = ${param_idx}")
                params.append(filters.project_name)
                param_idx += 1

            if filters.request_type:
                conditions.append(f"request_type = ${param_idx}")
                params.append(filters.request_type)
                param_idx += 1

            if filters.time_from:
                conditions.append(f"date >= ${param_idx}")
                params.append(filters.time_from.date())
                param_idx += 1

            if filters.time_to:
                conditions.append(f"date < ${param_idx}")
                params.append(filters.time_to.date())
                param_idx += 1

            query = f"""
                SELECT date,
                       SUM(input_tokens) as input_tokens,
                       SUM(output_tokens) as output_tokens,
                       SUM(total_tokens) as total_tokens,
                       SUM(request_count) as request_count
                FROM daily_aggregates
                WHERE {' AND '.join(conditions)}
                GROUP BY date
                ORDER BY date
            """

            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query, *params)

            buckets = []
            for row in rows:
                start = datetime.combine(
                    row["date"], datetime.min.time(), tzinfo=timezone.utc
                )
                end = start + timedelta(days=1)

                metrics = self._compute_metrics(
                    {
                        "input_tokens": row["input_tokens"],
                        "output_tokens": row["output_tokens"],
                        "total_tokens": row["total_tokens"],
                        "request_count": row["request_count"],
                    },
                    spec.metrics,
                )

                buckets.append(TimeBucket(start=start, end=end, metrics=metrics))

            return buckets

        except Exception as e:
            logger.error(f"Failed to get daily summary: {e}", extra={"error": str(e)})
            raise BackendError(f"Failed to get daily summary: {e}") from e

    async def summary_by_project(
        self, spec: AggregateSpec, filters: UsageFilter
    ) -> list[SummaryRow]:
        """Get aggregated summary grouped by project."""
        if not self.pool:
            raise BackendError("Pool not initialized")

        try:
            conditions = ["TRUE"]
            params: list[Any] = []
            param_idx = 1

            if filters.project_name:
                conditions.append(f"project_name = ${param_idx}")
                params.append(filters.project_name)
                param_idx += 1

            if filters.request_type:
                conditions.append(f"request_type = ${param_idx}")
                params.append(filters.request_type)
                param_idx += 1

            if filters.time_from:
                conditions.append(f"date >= ${param_idx}")
                params.append(filters.time_from.date())
                param_idx += 1

            if filters.time_to:
                conditions.append(f"date < ${param_idx}")
                params.append(filters.time_to.date())
                param_idx += 1

            query = f"""
                SELECT project_name,
                       SUM(input_tokens) as input_tokens,
                       SUM(output_tokens) as output_tokens,
                       SUM(total_tokens) as total_tokens,
                       SUM(request_count) as request_count
                FROM daily_aggregates
                WHERE {' AND '.join(conditions)}
                GROUP BY project_name
                ORDER BY project_name
            """

            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query, *params)

            summaries = []
            for row in rows:
                metrics = self._compute_metrics(
                    {
                        "input_tokens": row["input_tokens"],
                        "output_tokens": row["output_tokens"],
                        "total_tokens": row["total_tokens"],
                        "request_count": row["request_count"],
                    },
                    spec.metrics,
                )

                summaries.append(
                    SummaryRow(
                        group_keys={"project_name": row["project_name"]},
                        metrics=metrics,
                    )
                )

            return summaries

        except Exception as e:
            logger.error(f"Failed to get project summary: {e}", extra={"error": str(e)})
            raise BackendError(f"Failed to get project summary: {e}") from e

    async def summary_by_request_type(
        self, spec: AggregateSpec, filters: UsageFilter
    ) -> list[SummaryRow]:
        """Get aggregated summary grouped by request type."""
        if not self.pool:
            raise BackendError("Pool not initialized")

        try:
            conditions = ["TRUE"]
            params: list[Any] = []
            param_idx = 1

            if filters.project_name:
                conditions.append(f"project_name = ${param_idx}")
                params.append(filters.project_name)
                param_idx += 1

            if filters.request_type:
                conditions.append(f"request_type = ${param_idx}")
                params.append(filters.request_type)
                param_idx += 1

            if filters.time_from:
                conditions.append(f"date >= ${param_idx}")
                params.append(filters.time_from.date())
                param_idx += 1

            if filters.time_to:
                conditions.append(f"date < ${param_idx}")
                params.append(filters.time_to.date())
                param_idx += 1

            query = f"""
                SELECT request_type,
                       SUM(input_tokens) as input_tokens,
                       SUM(output_tokens) as output_tokens,
                       SUM(total_tokens) as total_tokens,
                       SUM(request_count) as request_count
                FROM daily_aggregates
                WHERE {' AND '.join(conditions)}
                GROUP BY request_type
                ORDER BY request_type
            """

            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query, *params)

            summaries = []
            for row in rows:
                metrics = self._compute_metrics(
                    {
                        "input_tokens": row["input_tokens"],
                        "output_tokens": row["output_tokens"],
                        "total_tokens": row["total_tokens"],
                        "request_count": row["request_count"],
                    },
                    spec.metrics,
                )

                summaries.append(
                    SummaryRow(
                        group_keys={"request_type": row["request_type"]},
                        metrics=metrics,
                    )
                )

            return summaries

        except Exception as e:
            logger.error(
                f"Failed to get request type summary: {e}", extra={"error": str(e)}
            )
            raise BackendError(f"Failed to get request type summary: {e}") from e

    async def timeseries(
        self, spec: AggregateSpec, filters: UsageFilter
    ) -> list[TimeBucket]:
        """Get time-series data for graphing."""
        return await self.summary_by_day(spec, filters)

    async def delete_project(self, options: DeleteOptions) -> DeleteResult:
        """Delete usage data for a project."""
        if not self.pool:
            raise BackendError("Pool not initialized")

        try:
            conditions = ["project_name = $1"]
            params: list[Any] = [options.project_name]
            param_idx = 2

            if options.time_from:
                conditions.append(f"timestamp >= ${param_idx}")
                params.append(options.time_from)
                param_idx += 1

            if options.time_to:
                conditions.append(f"timestamp < ${param_idx}")
                params.append(options.time_to)
                param_idx += 1

            async with self.pool.acquire() as conn:
                if options.simulate:
                    # Count only
                    events_query = f"""
                        SELECT COUNT(*) FROM usage_events
                        WHERE {' AND '.join(conditions)}
                    """
                    events_deleted = await conn.fetchval(events_query, *params)

                    aggregates_deleted = 0
                    if options.include_aggregates:
                        agg_conditions = ["project_name = $1"]
                        agg_params: list[Any] = [options.project_name]
                        agg_idx = 2

                        if options.time_from:
                            agg_conditions.append(f"date >= ${agg_idx}")
                            agg_params.append(options.time_from.date())
                            agg_idx += 1

                        if options.time_to:
                            agg_conditions.append(f"date < ${agg_idx}")
                            agg_params.append(options.time_to.date())

                        agg_query = f"""
                            SELECT COUNT(*) FROM daily_aggregates
                            WHERE {' AND '.join(agg_conditions)}
                        """
                        aggregates_deleted = await conn.fetchval(agg_query, *agg_params)

                    return DeleteResult(
                        events_deleted=events_deleted,
                        aggregates_deleted=aggregates_deleted,
                        simulated=True,
                    )

                # Actual deletion
                async with conn.transaction():
                    events_query = f"""
                        DELETE FROM usage_events
                        WHERE {' AND '.join(conditions)}
                    """
                    result = await conn.execute(events_query, *params)
                    events_deleted = int(result.split()[-1])

                    aggregates_deleted = 0
                    if options.include_aggregates:
                        agg_conditions = ["project_name = $1"]
                        agg_params_list: list[Any] = [options.project_name]
                        agg_idx = 2

                        if options.time_from:
                            agg_conditions.append(f"date >= ${agg_idx}")
                            agg_params_list.append(options.time_from.date())
                            agg_idx += 1

                        if options.time_to:
                            agg_conditions.append(f"date < ${agg_idx}")
                            agg_params_list.append(options.time_to.date())

                        agg_query = f"""
                            DELETE FROM daily_aggregates
                            WHERE {' AND '.join(agg_conditions)}
                        """
                        agg_result = await conn.execute(agg_query, *agg_params_list)
                        aggregates_deleted = int(agg_result.split()[-1])

                logger.info(
                    f"Deleted project data: {options.project_name}",
                    extra={
                        "project": options.project_name,
                        "events": events_deleted,
                        "aggregates": aggregates_deleted,
                    },
                )

                return DeleteResult(
                    events_deleted=events_deleted,
                    aggregates_deleted=aggregates_deleted,
                    simulated=False,
                )

        except Exception as e:
            logger.error(f"Failed to delete project: {e}", extra={"error": str(e)})
            raise BackendError(f"Failed to delete project: {e}") from e

    def _compute_metrics(
        self, agg: dict[str, int], metrics: set[AggregateMetric]
    ) -> dict[str, int | float]:
        """Compute requested metrics from aggregate data."""
        result: dict[str, int | float] = {}

        if AggregateMetric.SUM_INPUT in metrics:
            result["sum_input"] = agg["input_tokens"]

        if AggregateMetric.SUM_OUTPUT in metrics:
            result["sum_output"] = agg["output_tokens"]

        if AggregateMetric.SUM_TOTAL in metrics:
            result["sum_total"] = agg["total_tokens"]

        if AggregateMetric.COUNT_REQUESTS in metrics:
            result["count_requests"] = agg["request_count"]

        if AggregateMetric.AVG_TOTAL_PER_REQUEST in metrics:
            result["avg_total_per_request"] = (
                agg["total_tokens"] / agg["request_count"]
                if agg["request_count"] > 0
                else 0.0
            )

        return result
