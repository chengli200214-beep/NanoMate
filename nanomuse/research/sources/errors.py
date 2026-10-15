"""Errors that the research orchestrator can classify without parsing messages."""

from __future__ import annotations


class SourceError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "source_error",
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
