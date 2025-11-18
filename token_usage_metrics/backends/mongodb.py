"""MongoDB backend implementation for token usage metrics."""

from datetime import datetime, timedelta, timezone
from typing import Any

from motor import motor_asyncio
from pymongo.errors import PyMongoError

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


class MongoDBBackend(Backend):
    """MongoDB backend with time-series collection and aggregates."""

    def __init__(
        self,
        url: str,
        database: str = "token_usage",
        max_pool_size: int = 10,
        timeout: float = 5.0,
    ):
        self.url = url
        self.database_name = database
        self.max_pool_size = max_pool_size
        self.timeout_ms = int(timeout * 1000)
        self.client: motor_asyncio.AsyncIOMotorClient | None = None
        self.db: motor_asyncio.AsyncIOMotorDatabase | None = None

    async def connect(self) -> None:
        """Establish MongoDB connection and create collections."""
        try:
            self.client = motor_asyncio.AsyncIOMotorClient(
                self.url,
                maxPoolSize=self.max_pool_size,
                serverSelectionTimeoutMS=self.timeout_ms,
            )

            self.db = self.client[self.database_name]

            # Test connection
            await self.client.admin.command("ping")

            # Create indexes
            await self._init_schema()

            logger.info(
                "MongoDB backend connected", extra={"database": self.database_name}
            )

        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}", extra={"error": str(e)})
            raise ConnError(f"MongoDB connection failed: {e}") from e

    async def _init_schema(self) -> None:
        """Initialize MongoDB collections and indexes."""
        if not self.db:
            raise BackendError("Database not initialized")

        # Events collection indexes
        events_col = self.db["usage_events"]

        await events_col.create_index([("timestamp", 1)])
        await events_col.create_index([("project_name", 1), ("timestamp", 1)])
        await events_col.create_index([("request_type", 1), ("timestamp", 1)])
        await events_col.create_index(
            [("project_name", 1), ("request_type", 1), ("timestamp", 1)]
        )

        # Aggregates collection indexes
        agg_col = self.db["daily_aggregates"]

        await agg_col.create_index(
            [("date", 1), ("project_name", 1), ("request_type", 1)],
            unique=True,
        )
        await agg_col.create_index([("date", 1)])
        await agg_col.create_index([("project_name", 1), ("date", 1)])

        logger.info("MongoDB schema initialized")

    async def disconnect(self) -> None:
        """Close MongoDB connection."""
        if self.client:
            self.client.close()
            self.client = None
            self.db = None
            logger.info("MongoDB backend disconnected")

    async def health_check(self) -> bool:
        """Check MongoDB health."""
        try:
            if not self.client:
                return False
            await self.client.admin.command("ping")
            return True
        except Exception:
            return False

    async def log_many(self, events: list[UsageEvent]) -> None:
        """Store multiple events."""
        if not self.db:
            raise BackendError("Database not initialized")

        try:
            events_col = self.db["usage_events"]
            agg_col = self.db["daily_aggregates"]

            # Insert events
            event_docs = [
                {
                    "_id": e.id,
                    "timestamp": e.timestamp,
                    "project_name": e.project_name,
                    "request_type": e.request_type,
                    "input_tokens": e.input_tokens,
                    "output_tokens": e.output_tokens,
                    "total_tokens": e.total_tokens or 0,
                    "request_count": e.request_count,
                    "metadata": e.metadata,
                }
                for e in events
            ]

            await events_col.insert_many(event_docs, ordered=False)

            # Update daily aggregates
            for event in events:
                date = event.timestamp.date()

                await agg_col.update_one(
                    {
                        "date": datetime.combine(
                            date, datetime.min.time(), tzinfo=timezone.utc
                        ),
                        "project_name": event.project_name,
                        "request_type": event.request_type,
                    },
                    {
                        "$inc": {
                            "input_tokens": event.input_tokens,
                            "output_tokens": event.output_tokens,
                            "total_tokens": event.total_tokens or 0,
                            "request_count": event.request_count,
                        }
                    },
                    upsert=True,
                )

            logger.debug(
                f"Stored {len(events)} events in MongoDB", extra={"count": len(events)}
            )

        except PyMongoError as e:
            logger.error(f"Failed to store events: {e}", extra={"error": str(e)})
            raise BackendError(f"Failed to store events: {e}") from e

    async def fetch_raw(
        self, filters: UsageFilter
    ) -> tuple[list[UsageEvent], str | None]:
        """Fetch raw events with filters."""
        if not self.db:
            raise BackendError("Database not initialized")

        try:
            events_col = self.db["usage_events"]

            query: dict[str, Any] = {}

            if filters.project_name:
                query["project_name"] = filters.project_name

            if filters.request_type:
                query["request_type"] = filters.request_type

            if filters.time_from or filters.time_to:
                query["timestamp"] = {}
                if filters.time_from:
                    query["timestamp"]["$gte"] = filters.time_from
                if filters.time_to:
                    query["timestamp"]["$lt"] = filters.time_to

            cursor = (
                events_col.find(query).sort("timestamp", 1).limit(filters.limit + 1)
            )

            docs = await cursor.to_list(length=filters.limit + 1)

            events = []
            for doc in docs[: filters.limit]:
                event = UsageEvent(
                    id=doc["_id"],
                    timestamp=doc["timestamp"],
                    project_name=doc["project_name"],
                    request_type=doc["request_type"],
                    input_tokens=doc["input_tokens"],
                    output_tokens=doc["output_tokens"],
                    total_tokens=doc["total_tokens"],
                    request_count=doc["request_count"],
                    metadata=doc.get("metadata"),
                )
                events.append(event)

            # Generate cursor if more results exist
            next_cursor = None
            if len(docs) > filters.limit:
                last_doc = docs[filters.limit - 1]
                next_cursor = f"{last_doc['timestamp'].isoformat()}:{last_doc['_id']}"

            return events, next_cursor

        except PyMongoError as e:
            logger.error(f"Failed to fetch events: {e}", extra={"error": str(e)})
            raise BackendError(f"Failed to fetch events: {e}") from e

    async def summary_by_day(
        self, spec: AggregateSpec, filters: UsageFilter
    ) -> list[TimeBucket]:
        """Get time-bucketed aggregates by day."""
        if not self.db:
            raise BackendError("Database not initialized")

        try:
            agg_col = self.db["daily_aggregates"]

            match_stage: dict[str, Any] = {}

            if filters.project_name:
                match_stage["project_name"] = filters.project_name

            if filters.request_type:
                match_stage["request_type"] = filters.request_type

            if filters.time_from or filters.time_to:
                match_stage["date"] = {}
                if filters.time_from:
                    match_stage["date"]["$gte"] = datetime.combine(
                        filters.time_from.date(),
                        datetime.min.time(),
                        tzinfo=timezone.utc,
                    )
                if filters.time_to:
                    match_stage["date"]["$lt"] = datetime.combine(
                        filters.time_to.date(), datetime.min.time(), tzinfo=timezone.utc
                    )

            pipeline = [
                {"$match": match_stage} if match_stage else {"$match": {}},
                {
                    "$group": {
                        "_id": "$date",
                        "input_tokens": {"$sum": "$input_tokens"},
                        "output_tokens": {"$sum": "$output_tokens"},
                        "total_tokens": {"$sum": "$total_tokens"},
                        "request_count": {"$sum": "$request_count"},
                    }
                },
                {"$sort": {"_id": 1}},
            ]

            cursor = agg_col.aggregate(pipeline)
            docs = await cursor.to_list(length=None)

            buckets = []
            for doc in docs:
                start = doc["_id"]
                end = start + timedelta(days=1)

                metrics = self._compute_metrics(
                    {
                        "input_tokens": doc["input_tokens"],
                        "output_tokens": doc["output_tokens"],
                        "total_tokens": doc["total_tokens"],
                        "request_count": doc["request_count"],
                    },
                    spec.metrics,
                )

                buckets.append(TimeBucket(start=start, end=end, metrics=metrics))

            return buckets

        except PyMongoError as e:
            logger.error(f"Failed to get daily summary: {e}", extra={"error": str(e)})
            raise BackendError(f"Failed to get daily summary: {e}") from e

    async def summary_by_project(
        self, spec: AggregateSpec, filters: UsageFilter
    ) -> list[SummaryRow]:
        """Get aggregated summary grouped by project."""
        if not self.db:
            raise BackendError("Database not initialized")

        try:
            agg_col = self.db["daily_aggregates"]

            match_stage: dict[str, Any] = {}

            if filters.project_name:
                match_stage["project_name"] = filters.project_name

            if filters.request_type:
                match_stage["request_type"] = filters.request_type

            if filters.time_from or filters.time_to:
                match_stage["date"] = {}
                if filters.time_from:
                    match_stage["date"]["$gte"] = datetime.combine(
                        filters.time_from.date(),
                        datetime.min.time(),
                        tzinfo=timezone.utc,
                    )
                if filters.time_to:
                    match_stage["date"]["$lt"] = datetime.combine(
                        filters.time_to.date(), datetime.min.time(), tzinfo=timezone.utc
                    )

            pipeline = [
                {"$match": match_stage} if match_stage else {"$match": {}},
                {
                    "$group": {
                        "_id": "$project_name",
                        "input_tokens": {"$sum": "$input_tokens"},
                        "output_tokens": {"$sum": "$output_tokens"},
                        "total_tokens": {"$sum": "$total_tokens"},
                        "request_count": {"$sum": "$request_count"},
                    }
                },
                {"$sort": {"_id": 1}},
            ]

            cursor = agg_col.aggregate(pipeline)
            docs = await cursor.to_list(length=None)

            summaries = []
            for doc in docs:
                metrics = self._compute_metrics(
                    {
                        "input_tokens": doc["input_tokens"],
                        "output_tokens": doc["output_tokens"],
                        "total_tokens": doc["total_tokens"],
                        "request_count": doc["request_count"],
                    },
                    spec.metrics,
                )

                summaries.append(
                    SummaryRow(
                        group_keys={"project_name": doc["_id"]},
                        metrics=metrics,
                    )
                )

            return summaries

        except PyMongoError as e:
            logger.error(f"Failed to get project summary: {e}", extra={"error": str(e)})
            raise BackendError(f"Failed to get project summary: {e}") from e

    async def summary_by_request_type(
        self, spec: AggregateSpec, filters: UsageFilter
    ) -> list[SummaryRow]:
        """Get aggregated summary grouped by request type."""
        if not self.db:
            raise BackendError("Database not initialized")

        try:
            agg_col = self.db["daily_aggregates"]

            match_stage: dict[str, Any] = {}

            if filters.project_name:
                match_stage["project_name"] = filters.project_name

            if filters.request_type:
                match_stage["request_type"] = filters.request_type

            if filters.time_from or filters.time_to:
                match_stage["date"] = {}
                if filters.time_from:
                    match_stage["date"]["$gte"] = datetime.combine(
                        filters.time_from.date(),
                        datetime.min.time(),
                        tzinfo=timezone.utc,
                    )
                if filters.time_to:
                    match_stage["date"]["$lt"] = datetime.combine(
                        filters.time_to.date(), datetime.min.time(), tzinfo=timezone.utc
                    )

            pipeline = [
                {"$match": match_stage} if match_stage else {"$match": {}},
                {
                    "$group": {
                        "_id": "$request_type",
                        "input_tokens": {"$sum": "$input_tokens"},
                        "output_tokens": {"$sum": "$output_tokens"},
                        "total_tokens": {"$sum": "$total_tokens"},
                        "request_count": {"$sum": "$request_count"},
                    }
                },
                {"$sort": {"_id": 1}},
            ]

            cursor = agg_col.aggregate(pipeline)
            docs = await cursor.to_list(length=None)

            summaries = []
            for doc in docs:
                metrics = self._compute_metrics(
                    {
                        "input_tokens": doc["input_tokens"],
                        "output_tokens": doc["output_tokens"],
                        "total_tokens": doc["total_tokens"],
                        "request_count": doc["request_count"],
                    },
                    spec.metrics,
                )

                summaries.append(
                    SummaryRow(
                        group_keys={"request_type": doc["_id"]},
                        metrics=metrics,
                    )
                )

            return summaries

        except PyMongoError as e:
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
        if not self.db:
            raise BackendError("Database not initialized")

        try:
            events_col = self.db["usage_events"]
            agg_col = self.db["daily_aggregates"]

            query: dict[str, Any] = {"project_name": options.project_name}

            if options.time_from or options.time_to:
                query["timestamp"] = {}
                if options.time_from:
                    query["timestamp"]["$gte"] = options.time_from
                if options.time_to:
                    query["timestamp"]["$lt"] = options.time_to

            if options.simulate:
                # Count only
                events_deleted = await events_col.count_documents(query)

                aggregates_deleted = 0
                if options.include_aggregates:
                    agg_query: dict[str, Any] = {"project_name": options.project_name}

                    if options.time_from or options.time_to:
                        agg_query["date"] = {}
                        if options.time_from:
                            agg_query["date"]["$gte"] = datetime.combine(
                                options.time_from.date(),
                                datetime.min.time(),
                                tzinfo=timezone.utc,
                            )
                        if options.time_to:
                            agg_query["date"]["$lt"] = datetime.combine(
                                options.time_to.date(),
                                datetime.min.time(),
                                tzinfo=timezone.utc,
                            )

                    aggregates_deleted = await agg_col.count_documents(agg_query)

                return DeleteResult(
                    events_deleted=events_deleted,
                    aggregates_deleted=aggregates_deleted,
                    simulated=True,
                )

            # Actual deletion
            result = await events_col.delete_many(query)
            events_deleted = result.deleted_count

            aggregates_deleted = 0
            if options.include_aggregates:
                agg_query_del: dict[str, Any] = {"project_name": options.project_name}

                if options.time_from or options.time_to:
                    agg_query_del["date"] = {}
                    if options.time_from:
                        agg_query_del["date"]["$gte"] = datetime.combine(
                            options.time_from.date(),
                            datetime.min.time(),
                            tzinfo=timezone.utc,
                        )
                    if options.time_to:
                        agg_query_del["date"]["$lt"] = datetime.combine(
                            options.time_to.date(),
                            datetime.min.time(),
                            tzinfo=timezone.utc,
                        )

                agg_result = await agg_col.delete_many(agg_query_del)
                aggregates_deleted = agg_result.deleted_count

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

        except PyMongoError as e:
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
