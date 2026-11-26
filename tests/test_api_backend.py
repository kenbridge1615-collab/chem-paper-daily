"""api backend tested with an injected fake Anthropic client (task 4.3)."""

from __future__ import annotations

import pytest

from app.llm import api
from app.llm.base import NotLoggedInError, UsageLimitError


class _Block:
    def __init__(self, text):
        self.text = text


class _Usage:
    input_tokens = 11
    output_tokens = 22


class _Message:
    content = [_Block("the answer")]
    usage = _Usage()
    model = "claude-sonnet-4-5"


class FakeMessages:
    def __init__(self, behaviour):
        self.behaviour = behaviour

    def create(self, **kwargs):
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return _Message()


class FakeClient:
    def __init__(self, behaviour=None):
        self.messages = FakeMessages(behaviour)


def test_api_success():
    result = api.generate("p", model="m", _client=FakeClient())
    assert result.text == "the answer"
    assert result.input_tokens == 11
    assert result.output_tokens == 22


def test_api_missing_key_without_client():
    with pytest.raises(NotLoggedInError):
        api.generate("p", model="m", api_key="")


def test_api_rate_limit_mapped():
    class RateLimitError(Exception):
        pass

    with pytest.raises(UsageLimitError):
        api.generate("p", model="m", _client=FakeClient(RateLimitError("rate limit")))


def test_api_auth_error_mapped():
    class AuthenticationError(Exception):
        pass

    with pytest.raises(NotLoggedInError):
        api.generate("p", model="m", _client=FakeClient(AuthenticationError("bad api key")))
