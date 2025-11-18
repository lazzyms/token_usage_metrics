"""Supabase backend that reuses the Postgres implementation."""

from token_usage_metrics.backends.postgres import PostgresBackend


class SupabaseBackend(PostgresBackend):
    """Supabase backend powered by Postgres-compatible SQL."""

    def __init__(
        self,
        supabase_dsn: str,
        min_size: int = 2,
        max_size: int = 10,
        command_timeout: float = 60.0,
    ) -> None:
        super().__init__(
            dsn=supabase_dsn,
            min_size=min_size,
            max_size=max_size,
            command_timeout=command_timeout,
        )
