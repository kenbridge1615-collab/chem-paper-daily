"""LLM backends for generating introductions.

Both backends expose the same entry point:

    generate(prompt: str, *, model: str, timeout: int) -> GenerationResult

and raise GenerationError subclasses so the caller can tell retryable failures
apart from terminal ones (not logged in / usage limit).
"""

from app.llm.base import (
    GenerationError,
    GenerationResult,
    NotInstalledError,
    NotLoggedInError,
    TimeoutError,
    UsageLimitError,
)

__all__ = [
    "GenerationError",
    "GenerationResult",
    "NotInstalledError",
    "NotLoggedInError",
    "TimeoutError",
    "UsageLimitError",
]
