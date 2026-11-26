"""Shared types for LLM backends."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GenerationResult:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None


class GenerationError(Exception):
    """Base class for generation failures.

    `retryable` tells the caller whether another attempt makes sense.
    `kind` is a short machine label used in logs and the Slack failure notice.
    """

    kind = "other"
    retryable = True


class NotInstalledError(GenerationError):
    kind = "not_installed"
    retryable = False


class NotLoggedInError(GenerationError):
    kind = "not_logged_in"
    retryable = False


class UsageLimitError(GenerationError):
    kind = "usage_limit"
    retryable = False


class TimeoutError(GenerationError):  # noqa: A001  (shadowing is intentional, scoped)
    kind = "timeout"
    retryable = True
