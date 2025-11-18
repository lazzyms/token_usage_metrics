"""Async queue with background flushing and circuit breaker."""

import asyncio
from collections import deque
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Deque, Literal

from token_usage_metrics.errors import (
    BufferFullError,
    CircuitBreakerOpen,
    DroppedEventError,
)
from token_usage_metrics.logging import get_logger
from token_usage_metrics.models import UsageEvent

logger = get_logger(__name__)


class CircuitBreaker:
    """Simple circuit breaker for backend health."""

    def __init__(self, threshold: int, timeout: float):
        self.threshold = threshold
        self.timeout = timeout
        self.failures = 0
        self.last_failure_time: datetime | None = None
        self.state: Literal["closed", "open", "half_open"] = "closed"

    def record_success(self) -> None:
        """Record a successful operation."""
        self.failures = 0
        self.state = "closed"
        logger.debug("Circuit breaker: success recorded, state=closed")

    def record_failure(self) -> None:
        """Record a failed operation."""
        self.failures += 1
        self.last_failure_time = datetime.now(timezone.utc)

        if self.failures >= self.threshold:
            self.state = "open"
            logger.warning(
                f"Circuit breaker opened after {self.failures} failures",
                extra={"failures": self.failures, "threshold": self.threshold},
            )

    def is_open(self) -> bool:
        """Check if circuit breaker is open."""
        if self.state == "closed":
            return False

        if self.state == "open" and self.last_failure_time:
            # Check if timeout has elapsed
            elapsed = (
                datetime.now(timezone.utc) - self.last_failure_time
            ).total_seconds()
            if elapsed >= self.timeout:
                self.state = "half_open"
                logger.info("Circuit breaker: half-open, allowing test request")
                return False

        return self.state == "open"

    def allow_request(self) -> bool:
        """Check if request should be allowed."""
        if self.is_open():
            logger.debug("Circuit breaker: request blocked (open)")
            return False
        return True


class AsyncEventQueue:
    """Bounded async queue with background flushing and drop policies."""

    def __init__(
        self,
        flush_callback: Callable[[list[UsageEvent]], Awaitable[None]],
        buffer_size: int,
        flush_interval: float,
        flush_batch_size: int,
        drop_policy: Literal["newest", "oldest"],
        circuit_breaker: CircuitBreaker,
    ):
        self.flush_callback = flush_callback
        self.buffer_size = buffer_size
        self.flush_interval = flush_interval
        self.flush_batch_size = flush_batch_size
        self.drop_policy = drop_policy
        self.circuit_breaker = circuit_breaker

        self._queue: Deque[UsageEvent] = deque(maxlen=buffer_size)
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None
        self._running = False
        self._dropped_count = 0

    async def start(self) -> None:
        """Start the background flush task."""
        if self._running:
            return

        self._running = True
        self._flush_task = asyncio.create_task(self._background_flusher())
        logger.info(
            "AsyncEventQueue started",
            extra={
                "buffer_size": self.buffer_size,
                "flush_interval": self.flush_interval,
                "flush_batch_size": self.flush_batch_size,
            },
        )

    async def stop(self, timeout: float | None = None) -> None:
        """Stop the background flush task and flush remaining events."""
        if not self._running:
            return

        self._running = False

        if self._flush_task:
            # Cancel the background task
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        # Flush remaining events
        await self.flush(timeout=timeout)
        logger.info("AsyncEventQueue stopped")

    async def enqueue(self, event: UsageEvent) -> None:
        """Enqueue an event for async processing."""
        if self.circuit_breaker.is_open():
            raise CircuitBreakerOpen("Backend is unavailable")

        async with self._lock:
            if len(self._queue) >= self.buffer_size:
                # Apply drop policy
                if self.drop_policy == "oldest":
                    dropped = self._queue.popleft()
                    logger.warning(
                        f"Buffer full, dropping oldest event: {dropped.id}",
                        extra={"event_id": dropped.id, "policy": "oldest"},
                    )
                else:  # newest
                    logger.warning(
                        f"Buffer full, dropping newest event: {event.id}",
                        extra={"event_id": event.id, "policy": "newest"},
                    )
                    self._dropped_count += 1
                    raise BufferFullError("Buffer is full, event dropped")

                self._dropped_count += 1

            self._queue.append(event)
            logger.debug(
                f"Event enqueued: {event.id}",
                extra={"event_id": event.id, "queue_size": len(self._queue)},
            )

    async def flush(self, timeout: float | None = None) -> int:
        """Flush all pending events."""
        flushed = 0

        try:
            if timeout:
                async with asyncio.timeout(timeout):
                    flushed = await self._flush_all()
            else:
                flushed = await self._flush_all()

        except asyncio.TimeoutError:
            logger.warning(
                f"Flush timeout after flushing {flushed} events",
                extra={"flushed": flushed, "timeout": timeout},
            )

        return flushed

    async def _flush_all(self) -> int:
        """Internal method to flush all events."""
        flushed = 0

        while True:
            async with self._lock:
                if not self._queue:
                    break

                # Take a batch
                batch_size = min(len(self._queue), self.flush_batch_size)
                batch = [self._queue.popleft() for _ in range(batch_size)]

            if batch:
                try:
                    await self.flush_callback(batch)
                    self.circuit_breaker.record_success()
                    flushed += len(batch)
                    logger.debug(
                        f"Flushed {len(batch)} events",
                        extra={"batch_size": len(batch), "total_flushed": flushed},
                    )
                except Exception as e:
                    self.circuit_breaker.record_failure()
                    logger.error(
                        f"Flush callback failed: {e}",
                        extra={"batch_size": len(batch), "error": str(e)},
                    )
                    # Re-queue the batch (at the front)
                    async with self._lock:
                        self._queue.extendleft(reversed(batch))
                    raise

        return flushed

    async def _background_flusher(self) -> None:
        """Background task that periodically flushes the queue."""
        logger.info("Background flusher started")

        while self._running:
            try:
                await asyncio.sleep(self.flush_interval)

                if not self._running:
                    break

                async with self._lock:
                    queue_size = len(self._queue)

                if queue_size > 0:
                    try:
                        flushed = await self.flush()
                        if flushed > 0:
                            logger.debug(
                                f"Background flush completed: {flushed} events",
                                extra={"flushed": flushed},
                            )
                    except Exception as e:
                        logger.error(
                            f"Background flush failed: {e}",
                            extra={"error": str(e)},
                        )

            except asyncio.CancelledError:
                logger.info("Background flusher cancelled")
                break
            except Exception as e:
                logger.exception(f"Unexpected error in background flusher: {e}")

        logger.info("Background flusher stopped")

    def get_stats(self) -> dict[str, int | str]:
        """Get queue statistics."""
        return {
            "queue_size": len(self._queue),
            "buffer_size": self.buffer_size,
            "dropped_count": self._dropped_count,
            "circuit_state": self.circuit_breaker.state,
            "circuit_failures": self.circuit_breaker.failures,
        }
