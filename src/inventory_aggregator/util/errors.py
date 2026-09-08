from __future__ import annotations


class RetryableError(Exception):
    """Indicates a failure that may succeed on retry."""


class NonRetryableError(Exception):
    """Indicates a failure that should not be retried."""
