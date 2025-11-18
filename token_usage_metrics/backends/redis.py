"""Redis backend implementation for token usage metrics."""

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from redis import asyncio as aioredis

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
    TimeBucketType,
    UsageEvent,
    UsageFilter,
)

logger = get_logger(__name__)


class RedisBackend(Backend):
    """Redis backend with hash-per-event, day-partitioned ZSETs, and daily aggregates."""

    def __init__(
        self,
        redis_url: str,
        pool_size: int = 10,
        socket_timeout: float = 5.0,
        socket_connect_timeout: float = 5.0,
    ):
        self.redis_url = redis_url
        self.pool_size = pool_size
        self.socket_timeout = socket_timeout
        self.socket_connect_timeout = socket_connect_timeout
        self.client: aioredis.Redis | None = None

    async def connect(self) -> None:
        """Establish Redis connection."""
        try:
            self.client = await aioredis.from_url(
                self.redis_url,
                max_connections=self.pool_size,
                socket_timeout=self.socket_timeout,
                socket_connect_timeout=self.socket_connect_timeout,
                decode_responses=False,  # We handle encoding
            )
            await self.client.ping()
            logger.info("Redis backend connected", extra={"url": self.redis_url})
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}", extra={"error": str(e)})
            raise ConnError(f"Redis connection failed: {e}") from e

    async def disconnect(self) -> None:
        """Close Redis connection."""
        if self.client:
            await self.client.aclose()
            self.client = None
            logger.info("Redis backend disconnected")

    async def health_check(self) -> bool:
        """Check Redis health."""
        try:
            if not self.client:
                return False
            await self.client.ping()
            return True
        except Exception:
            return False

    async def log_many(self, events: list[UsageEvent]) -> None:
        """Store multiple events using pipeline for efficiency."""
        if not self.client:
            raise BackendError("Redis client not connected")

        try:
            async with self.client.pipeline(transaction=False) as pipe:
                for event in events:
                    await self._store_event(pipe, event)
                await pipe.execute()

            logger.debug(
                f"Stored {len(events)} events in Redis",
                extra={"count": len(events)},
            )

        except Exception as e:
            logger.error(f"Failed to store events: {e}", extra={"error": str(e)})
            raise BackendError(f"Failed to store events: {e}") from e

    async def _store_event(
        self, pipe: aioredis.client.Pipeline, event: UsageEvent
    ) -> None:
        """Store a single event with indexes and aggregates."""
        # Hash key for event data
        event_key = f"tum:e:{event.id}"

        # Serialize event
        event_data = {
            b"id": event.id.encode(),
            b"ts": event.timestamp.isoformat().encode(),
            b"project": event.project_name.encode(),
            b"type": event.request_type.encode(),
            b"input": str(event.input_tokens).encode(),
            b"output": str(event.output_tokens).encode(),
            b"total": str(event.total_tokens or 0).encode(),
            b"count": str(event.request_count).encode(),
        }

        if event.metadata:
            event_data[b"metadata"] = json.dumps(event.metadata).encode()

        # Store event hash
        await pipe.hset(event_key, mapping=event_data)  # type: ignore

        # Get day key
        day_key = event.timestamp.strftime("%Y%m%d")
        score = event.timestamp.timestamp()

        # Add to time-based ZSET
        await pipe.zadd(f"tum:ts:{day_key}", {event.id.encode(): score})

        # Add to project ZSET
        await pipe.zadd(
            f"tum:proj:{event.project_name}:{day_key}",
            {event.id.encode(): score},
        )

        # Add to type ZSET
        await pipe.zadd(
            f"tum:type:{event.request_type}:{day_key}",
            {event.id.encode(): score},
        )

        # Update daily aggregates
        agg_day = f"tum:agg:{day_key}"
        await pipe.hincrby(agg_day, b"input_tokens", event.input_tokens)
        await pipe.hincrby(agg_day, b"output_tokens", event.output_tokens)
        await pipe.hincrby(agg_day, b"total_tokens", event.total_tokens or 0)
        await pipe.hincrby(agg_day, b"request_count", event.request_count)

        # Aggregates by project
        agg_proj = f"tum:agg:{day_key}:proj:{event.project_name}"
        await pipe.hincrby(agg_proj, b"input_tokens", event.input_tokens)
        await pipe.hincrby(agg_proj, b"output_tokens", event.output_tokens)
        await pipe.hincrby(agg_proj, b"total_tokens", event.total_tokens or 0)
        await pipe.hincrby(agg_proj, b"request_count", event.request_count)

        # Aggregates by type
        agg_type = f"tum:agg:{day_key}:type:{event.request_type}"
        await pipe.hincrby(agg_type, b"input_tokens", event.input_tokens)
        await pipe.hincrby(agg_type, b"output_tokens", event.output_tokens)
        await pipe.hincrby(agg_type, b"total_tokens", event.total_tokens or 0)
        await pipe.hincrby(agg_type, b"request_count", event.request_count)

        # Aggregates by project+type
        agg_both = (
            f"tum:agg:{day_key}:proj:{event.project_name}:type:{event.request_type}"
        )
        await pipe.hincrby(agg_both, b"input_tokens", event.input_tokens)
        await pipe.hincrby(agg_both, b"output_tokens", event.output_tokens)
        await pipe.hincrby(agg_both, b"total_tokens", event.total_tokens or 0)
        await pipe.hincrby(agg_both, b"request_count", event.request_count)

    async def fetch_raw(
        self, filters: UsageFilter
    ) -> tuple[list[UsageEvent], str | None]:
        """Fetch raw events with filters and cursor pagination."""
        if not self.client:
            raise BackendError("Redis client not connected")

        events: list[UsageEvent] = []
        cursor_parts = self._parse_cursor(filters.cursor)
        current_day = (filters.time_from or datetime.now(timezone.utc)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_day = (filters.time_to or datetime.now(timezone.utc)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)

        try:
            # Iterate through days
            collected = 0
            day = current_day if not cursor_parts else cursor_parts["day"]

            while day <= end_day and collected < filters.limit:
                day_key = day.strftime("%Y%m%d")

                # Determine which index to use
                if filters.project_name and filters.request_type:
                    # Use intersection (if both filters)
                    zset_key = await self._intersect_zsets(
                        day_key, filters.project_name, filters.request_type
                    )
                elif filters.project_name:
                    zset_key = f"tum:proj:{filters.project_name}:{day_key}"
                elif filters.request_type:
                    zset_key = f"tum:type:{filters.request_type}:{day_key}"
                else:
                    zset_key = f"tum:ts:{day_key}"

                # Get IDs from ZSET
                # Use full day range: midnight -> next midnight
                min_score = day.replace(
                    hour=0, minute=0, second=0, microsecond=0
                ).timestamp()
                max_score = (
                    (day + timedelta(days=1))
                    .replace(hour=0, minute=0, second=0, microsecond=0)
                    .timestamp()
                )

                ids = await self.client.zrangebyscore(
                    zset_key,
                    min_score,
                    max_score,
                    start=0,
                    num=filters.limit - collected,
                )

                # Fetch event data
                for event_id in ids:
                    event_key = f"tum:e:{event_id.decode()}"
                    event_data = await self.client.hgetall(event_key)

                    if event_data:
                        event = self._deserialize_event(event_data)
                        events.append(event)
                        collected += 1

                        if collected >= filters.limit:
                            break

                day += timedelta(days=1)

            # Generate next cursor
            next_cursor = None
            if collected >= filters.limit and day <= end_day:
                next_cursor = self._generate_cursor(day, events[-1] if events else None)

            return events, next_cursor

        except Exception as e:
            logger.error(f"Failed to fetch events: {e}", extra={"error": str(e)})
            raise BackendError(f"Failed to fetch events: {e}") from e

    async def _intersect_zsets(
        self, day_key: str, project: str, request_type: str
    ) -> str:
        """Create temporary intersection of project and type ZSETs."""
        if not self.client:
            raise BackendError("Redis client not connected")

        temp_key = f"tum:temp:{day_key}:{project}:{request_type}"
        proj_key = f"tum:proj:{project}:{day_key}"
        type_key = f"tum:type:{request_type}:{day_key}"

        await self.client.zinterstore(temp_key, [proj_key, type_key])
        await self.client.expire(temp_key, 60)  # Expire in 60 seconds

        return temp_key

    def _deserialize_event(self, data: dict[bytes, bytes]) -> UsageEvent:
        """Deserialize event from Redis hash."""
        metadata = None
        if b"metadata" in data:
            metadata = json.loads(data[b"metadata"].decode())

        return UsageEvent(
            id=data[b"id"].decode(),
            timestamp=datetime.fromisoformat(data[b"ts"].decode()),
            project_name=data[b"project"].decode(),
            request_type=data[b"type"].decode(),
            input_tokens=int(data[b"input"].decode()),
            output_tokens=int(data[b"output"].decode()),
            total_tokens=int(data[b"total"].decode()),
            request_count=int(data[b"count"].decode()),
            metadata=metadata,
        )

    def _parse_cursor(self, cursor: str | None) -> dict[str, Any] | None:
        """Parse cursor string."""
        if not cursor:
            return None
        try:
            parts = cursor.split(":")
            return {"day": datetime.fromisoformat(parts[0])}
        except Exception:
            return None

    def _generate_cursor(self, day: datetime, last_event: UsageEvent | None) -> str:
        """Generate cursor string."""
        return day.isoformat()

    async def summary_by_day(
        self, spec: AggregateSpec, filters: UsageFilter
    ) -> list[TimeBucket]:
        """Get time-bucketed aggregates by day."""
        if not self.client:
            raise BackendError("Redis client not connected")

        buckets: list[TimeBucket] = []
        current_day = filters.time_from or datetime.now(timezone.utc) - timedelta(
            days=30
        )
        end_day = filters.time_to or datetime.now(timezone.utc)

        try:
            day = current_day.replace(hour=0, minute=0, second=0, microsecond=0)

            while day <= end_day:
                day_key = day.strftime("%Y%m%d")

                # Get appropriate aggregate key
                if spec.group_by == GroupByDimension.PROJECT and filters.project_name:
                    agg_key = f"tum:agg:{day_key}:proj:{filters.project_name}"
                elif (
                    spec.group_by == GroupByDimension.REQUEST_TYPE
                    and filters.request_type
                ):
                    agg_key = f"tum:agg:{day_key}:type:{filters.request_type}"
                elif (
                    spec.group_by == GroupByDimension.PROJECT_AND_TYPE
                    and filters.project_name
                    and filters.request_type
                ):
                    agg_key = f"tum:agg:{day_key}:proj:{filters.project_name}:type:{filters.request_type}"
                else:
                    agg_key = f"tum:agg:{day_key}"

                agg_data = await self.client.hgetall(agg_key)

                if agg_data:
                    metrics = self._compute_metrics(agg_data, spec.metrics)
                    bucket = TimeBucket(
                        start=day,
                        end=day + timedelta(days=1),
                        metrics=metrics,
                    )
                    buckets.append(bucket)

                day += timedelta(days=1)

            return buckets

        except Exception as e:
            logger.error(f"Failed to get daily summary: {e}", extra={"error": str(e)})
            raise BackendError(f"Failed to get daily summary: {e}") from e

    async def summary_by_project(
        self, spec: AggregateSpec, filters: UsageFilter
    ) -> list[SummaryRow]:
        """Get aggregated summary grouped by project."""
        if not self.client:
            raise BackendError("Redis client not connected")

        try:
            summaries: dict[str, dict[str, int]] = {}
            current_day = filters.time_from or datetime.now(timezone.utc) - timedelta(
                days=30
            )
            end_day = filters.time_to or datetime.now(timezone.utc)

            # Collect all project keys across date range
            day = current_day.replace(hour=0, minute=0, second=0, microsecond=0)

            while day <= end_day:
                day_key = day.strftime("%Y%m%d")

                # Scan for project aggregate keys for this day
                pattern = f"tum:agg:{day_key}:proj:*"
                if filters.request_type:
                    pattern = f"tum:agg:{day_key}:proj:*:type:{filters.request_type}"

                async for key in self.client.scan_iter(match=pattern):
                    key_str = key.decode()
                    # If we're scanning proj:* without a request_type filter, skip per-type keys to avoid double-counting
                    if not filters.request_type and ":type:" in key_str:
                        continue

                    # Extract project name from key (format: tum:agg:YYYYMMDD:proj:PROJECT_NAME)
                    parts = key_str.split(":")
                    project_name = parts[4]  # project_name is the 5th element

                    if filters.project_name and project_name != filters.project_name:
                        continue

                    agg_data = await self.client.hgetall(key)

                    if project_name not in summaries:
                        summaries[project_name] = {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0,
                            "request_count": 0,
                        }

                    summaries[project_name]["input_tokens"] += int(
                        agg_data.get(b"input_tokens", b"0")
                    )
                    summaries[project_name]["output_tokens"] += int(
                        agg_data.get(b"output_tokens", b"0")
                    )
                    summaries[project_name]["total_tokens"] += int(
                        agg_data.get(b"total_tokens", b"0")
                    )
                    summaries[project_name]["request_count"] += int(
                        agg_data.get(b"request_count", b"0")
                    )

                day += timedelta(days=1)

            # Convert to SummaryRow objects
            rows = []
            for project, agg in summaries.items():
                metrics = self._compute_metrics_from_dict(agg, spec.metrics)
                rows.append(
                    SummaryRow(
                        group_keys={"project_name": project},
                        metrics=metrics,
                    )
                )

            return rows

        except Exception as e:
            logger.error(f"Failed to get project summary: {e}", extra={"error": str(e)})
            raise BackendError(f"Failed to get project summary: {e}") from e

    async def summary_by_request_type(
        self, spec: AggregateSpec, filters: UsageFilter
    ) -> list[SummaryRow]:
        """Get aggregated summary grouped by request type."""
        if not self.client:
            raise BackendError("Redis client not connected")

        try:
            summaries: dict[str, dict[str, int]] = {}
            current_day = filters.time_from or datetime.now(timezone.utc) - timedelta(
                days=30
            )
            end_day = filters.time_to or datetime.now(timezone.utc)

            day = current_day.replace(hour=0, minute=0, second=0, microsecond=0)

            while day <= end_day:
                day_key = day.strftime("%Y%m%d")

                # Scan for type aggregate keys
                pattern = f"tum:agg:{day_key}:type:*"
                if filters.project_name:
                    pattern = f"tum:agg:{day_key}:proj:{filters.project_name}:type:*"

                async for key in self.client.scan_iter(match=pattern):
                    key_str = key.decode()
                    parts = key_str.split(":")

                    # Extract request type
                    if filters.project_name:
                        # Key format: tum:agg:YYYYMMDD:proj:PROJECT_NAME:type:REQUEST_TYPE
                        request_type = parts[6]  # request_type is the 7th element
                    else:
                        request_type = parts[
                            4
                        ]  # Key format: tum:agg:YYYYMMDD:type:REQUEST_TYPE

                    if filters.request_type and request_type != filters.request_type:
                        continue

                    agg_data = await self.client.hgetall(key)

                    if request_type not in summaries:
                        summaries[request_type] = {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0,
                            "request_count": 0,
                        }

                    summaries[request_type]["input_tokens"] += int(
                        agg_data.get(b"input_tokens", b"0")
                    )
                    summaries[request_type]["output_tokens"] += int(
                        agg_data.get(b"output_tokens", b"0")
                    )
                    summaries[request_type]["total_tokens"] += int(
                        agg_data.get(b"total_tokens", b"0")
                    )
                    summaries[request_type]["request_count"] += int(
                        agg_data.get(b"request_count", b"0")
                    )

                day += timedelta(days=1)

            # Convert to SummaryRow objects
            rows = []
            for req_type, agg in summaries.items():
                metrics = self._compute_metrics_from_dict(agg, spec.metrics)
                rows.append(
                    SummaryRow(
                        group_keys={"request_type": req_type},
                        metrics=metrics,
                    )
                )

            return rows

        except Exception as e:
            logger.error(
                f"Failed to get request type summary: {e}", extra={"error": str(e)}
            )
            raise BackendError(f"Failed to get request type summary: {e}") from e

    async def timeseries(
        self, spec: AggregateSpec, filters: UsageFilter
    ) -> list[TimeBucket]:
        """Get time-series data for graphing (alias for summary_by_day)."""
        return await self.summary_by_day(spec, filters)

    async def delete_project(self, options: DeleteOptions) -> DeleteResult:
        """Delete usage data for a project."""
        if not self.client:
            raise BackendError("Redis client not connected")

        events_deleted = 0
        aggregates_deleted = 0

        try:
            current_day = options.time_from or datetime(2020, 1, 1, tzinfo=timezone.utc)
            end_day = options.time_to or datetime.now(timezone.utc) + timedelta(days=1)

            if options.simulate:
                # Count only, don't delete
                day = current_day.replace(hour=0, minute=0, second=0, microsecond=0)

                while day <= end_day:
                    day_key = day.strftime("%Y%m%d")

                    # Count events in project ZSET
                    proj_key = f"tum:proj:{options.project_name}:{day_key}"
                    count = await self.client.zcard(proj_key)
                    events_deleted += count or 0

                    # Count aggregate keys
                    if options.include_aggregates:
                        agg_pattern = f"tum:agg:{day_key}:proj:{options.project_name}*"
                        async for _ in self.client.scan_iter(match=agg_pattern):
                            aggregates_deleted += 1

                    day += timedelta(days=1)

                return DeleteResult(
                    events_deleted=events_deleted,
                    aggregates_deleted=aggregates_deleted,
                    simulated=True,
                )

            # Actual deletion
            day = current_day.replace(hour=0, minute=0, second=0, microsecond=0)

            while day <= end_day:
                day_key = day.strftime("%Y%m%d")
                proj_key = f"tum:proj:{options.project_name}:{day_key}"

                # Get all event IDs for this project on this day
                event_ids = await self.client.zrange(proj_key, 0, -1)

                if event_ids:
                    # Delete event hashes
                    async with self.client.pipeline(transaction=False) as pipe:
                        for event_id in event_ids:
                            event_key = f"tum:e:{event_id.decode()}"
                            await pipe.delete(event_key)
                            events_deleted += 1

                        # Remove from project ZSET
                        await pipe.delete(proj_key)

                        # Remove from time ZSET
                        ts_key = f"tum:ts:{day_key}"
                        for event_id in event_ids:
                            await pipe.zrem(ts_key, event_id)

                        await pipe.execute()

                # Delete aggregates
                if options.include_aggregates:
                    agg_pattern = f"tum:agg:{day_key}:proj:{options.project_name}*"
                    async for key in self.client.scan_iter(match=agg_pattern):
                        await self.client.delete(key)
                        aggregates_deleted += 1

                day += timedelta(days=1)

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
        self, agg_data: dict[bytes, bytes], metrics: set[AggregateMetric]
    ) -> dict[str, int | float]:
        """Compute requested metrics from aggregate data."""
        result: dict[str, int | float] = {}

        input_tokens = int(agg_data.get(b"input_tokens", b"0"))
        output_tokens = int(agg_data.get(b"output_tokens", b"0"))
        total_tokens = int(agg_data.get(b"total_tokens", b"0"))
        request_count = int(agg_data.get(b"request_count", b"0"))

        if AggregateMetric.SUM_INPUT in metrics:
            result["sum_input"] = input_tokens

        if AggregateMetric.SUM_OUTPUT in metrics:
            result["sum_output"] = output_tokens

        if AggregateMetric.SUM_TOTAL in metrics:
            result["sum_total"] = total_tokens

        if AggregateMetric.COUNT_REQUESTS in metrics:
            result["count_requests"] = request_count

        if AggregateMetric.AVG_TOTAL_PER_REQUEST in metrics:
            result["avg_total_per_request"] = (
                total_tokens / request_count if request_count > 0 else 0.0
            )

        return result

    def _compute_metrics_from_dict(
        self, agg: dict[str, int], metrics: set[AggregateMetric]
    ) -> dict[str, int | float]:
        """Compute requested metrics from dict."""
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
