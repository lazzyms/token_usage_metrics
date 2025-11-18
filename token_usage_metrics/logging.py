"""Structured logging utilities with correlation IDs."""

import logging
import sys
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

# Context variable for correlation IDs
correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    """Get current correlation ID or generate a new one."""
    cid = correlation_id.get()
    if not cid:
        cid = uuid4().hex[:8]
        correlation_id.set(cid)
    return cid


def set_correlation_id(cid: str) -> None:
    """Set correlation ID for current context."""
    correlation_id.set(cid)


class StructuredLogger:
    """Simple structured logger with correlation IDs."""

    def __init__(self, name: str, level: str = "INFO"):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, level))

        if not self.logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

    def _add_correlation(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Add correlation ID to log extra."""
        if extra is None:
            extra = {}
        extra["correlation_id"] = get_correlation_id()
        return extra

    def debug(self, msg: str, **kwargs: Any) -> None:
        """Log debug message."""
        extra = self._add_correlation(kwargs.pop("extra", None))
        self.logger.debug(msg, extra=extra, **kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        """Log info message."""
        extra = self._add_correlation(kwargs.pop("extra", None))
        self.logger.info(msg, extra=extra, **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        """Log warning message."""
        extra = self._add_correlation(kwargs.pop("extra", None))
        self.logger.warning(msg, extra=extra, **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        """Log error message."""
        extra = self._add_correlation(kwargs.pop("extra", None))
        self.logger.error(msg, extra=extra, **kwargs)

    def exception(self, msg: str, **kwargs: Any) -> None:
        """Log exception with traceback."""
        extra = self._add_correlation(kwargs.pop("extra", None))
        self.logger.exception(msg, extra=extra, **kwargs)


def get_logger(name: str, level: str = "INFO") -> StructuredLogger:
    """Get or create a structured logger."""
    return StructuredLogger(name, level)
