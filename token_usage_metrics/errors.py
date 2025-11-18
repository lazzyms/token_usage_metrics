"""Custom exceptions for token usage metrics."""


class TokenUsageMetricsError(Exception):
    """Base exception for all token usage metrics errors."""

    pass


class BackendError(TokenUsageMetricsError):
    """Backend operation failed."""

    pass


class BackendUnavailable(BackendError):
    """Backend is unavailable or unhealthy."""

    pass


class ConnectionError(BackendError):
    """Failed to connect to backend."""

    pass


class TimeoutError(BackendError):
    """Backend operation timed out."""

    pass


class ValidationError(TokenUsageMetricsError):
    """Data validation failed."""

    pass


class BufferFullError(TokenUsageMetricsError):
    """Buffer is full and cannot accept more events."""

    pass


class DroppedEventError(TokenUsageMetricsError):
    """Event was dropped due to buffer overflow."""

    def __init__(self, count: int, policy: str):
        self.count = count
        self.policy = policy
        super().__init__(f"Dropped {count} events using {policy} policy")


class CircuitBreakerOpen(BackendUnavailable):
    """Circuit breaker is open, rejecting requests."""

    pass


class DeletionError(BackendError):
    """Deletion operation failed."""

    pass
