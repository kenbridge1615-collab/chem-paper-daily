"""Generate text via the official Anthropic SDK (metered API).

This is the switch-in backend for when `claude -p` is unavailable. anthropic is
an optional dependency (install with the `api` extra) and is imported lazily so
the module can be imported without it present.
"""

from __future__ import annotations

from app.llm.base import (
    GenerationError,
    GenerationResult,
    NotLoggedInError,
    UsageLimitError,
)


def generate(
    prompt: str,
    *,
    model: str,
    timeout: int = 180,
    api_key: str | None = None,
    max_tokens: int = 2048,
    _client=None,
) -> GenerationResult:
    """Call the Anthropic Messages API. `_client` is injectable for tests."""
    client = _client
    if client is None:
        if not api_key:
            raise NotLoggedInError("ANTHROPIC_API_KEY is not set")
        try:
            import anthropic  # noqa: PLC0415  (lazy, optional dependency)
        except ImportError as exc:  # pragma: no cover - env dependent
            raise GenerationError(
                "anthropic package not installed; install the 'api' extra"
            ) from exc
        client = anthropic.Anthropic(api_key=api_key, timeout=timeout)

    try:
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # map SDK errors to typed failures
        name = type(exc).__name__.lower()
        text = str(exc).lower()
        if "ratelimit" in name or "rate limit" in text or "overloaded" in text:
            raise UsageLimitError(str(exc)) from exc
        if "authentication" in name or "api key" in text or "unauthorized" in text:
            raise NotLoggedInError(str(exc)) from exc
        raise GenerationError(str(exc)) from exc

    text = ""
    for block in getattr(message, "content", []) or []:
        block_text = getattr(block, "text", None)
        if block_text:
            text += block_text
    usage = getattr(message, "usage", None)
    return GenerationResult(
        text=text.strip(),
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        model=getattr(message, "model", model),
    )
