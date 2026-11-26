"""claude_code backend tested with a fake `claude` command (task 4.5).

We write small shell scripts that imitate the possible outcomes of `claude -p`
(success JSON / not logged in / usage limit / timeout) and point the backend at
them via an injected runner. This verifies failure classification and the
security contract (tools disabled, empty temp cwd) without a real Claude Code.
"""

from __future__ import annotations

import stat
import subprocess
import sys

import pytest

from app.llm import claude_code
from app.llm.base import NotLoggedInError, TimeoutError, UsageLimitError


def _make_fake(tmp_path, name, body):
    """Create an executable fake claude script and return its path."""
    script = tmp_path / name
    script.write_text("#!/usr/bin/env bash\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IRWXU)
    return script


def _runner_for(fake_path):
    """Return a subprocess.run-like callable that invokes the fake script,
    ignoring the real 'claude' argv[0] but keeping the flags and stdin."""

    def run(argv, **kwargs):
        new_argv = [str(fake_path)] + argv[1:]
        return subprocess.run(new_argv, **kwargs)

    return run


def test_success(tmp_path):
    fake = _make_fake(
        tmp_path,
        "claude_ok",
        'cat >/dev/null\n'
        'echo \'{"result": "hello", "usage": {"input_tokens": 5, "output_tokens": 7}}\'\n',
    )
    result = claude_code.generate(
        "prompt", model="m", timeout=10, _runner=_runner_for(fake)
    )
    assert result.text == "hello"
    assert result.input_tokens == 5
    assert result.output_tokens == 7


def test_not_logged_in(tmp_path):
    fake = _make_fake(
        tmp_path, "claude_login",
        'echo "Error: please run claude login to authenticate" >&2\nexit 1\n',
    )
    with pytest.raises(NotLoggedInError):
        claude_code.generate("p", model="m", timeout=10, _runner=_runner_for(fake))


def test_usage_limit(tmp_path):
    fake = _make_fake(
        tmp_path, "claude_limit",
        'echo "usage limit reached for this period" >&2\nexit 1\n',
    )
    with pytest.raises(UsageLimitError):
        claude_code.generate("p", model="m", timeout=10, _runner=_runner_for(fake))


def test_timeout(tmp_path):
    fake = _make_fake(tmp_path, "claude_slow", "sleep 5\n")
    with pytest.raises(TimeoutError):
        claude_code.generate("p", model="m", timeout=1, _runner=_runner_for(fake))


def test_runs_in_empty_tempdir_and_disables_tools(tmp_path):
    """The fake records its cwd contents and received argv to a side file."""
    marker = tmp_path / "observed.txt"
    fake = _make_fake(
        tmp_path, "claude_probe",
        f'cat >/dev/null\n'
        f'ls -A . > "{marker}"\n'
        f'echo "ARGS: $@" >> "{marker}"\n'
        f'echo \'{{"result": "{{\\"background\\": 1}}"}}\' 2>/dev/null\n'
        f'echo \'{{"result": "x"}}\'\n',
    )
    claude_code.generate("p", model="m", timeout=10, _runner=_runner_for(fake))
    observed = marker.read_text()
    # First line is the directory listing: it must be empty (empty temp cwd).
    lines = observed.splitlines()
    dir_contents = [line for line in lines if line and not line.startswith("ARGS:")]
    assert dir_contents == []
    # Tools are disabled via the allowedTools flag collected in build_argv.
    assert "--allowedTools" in observed


@pytest.mark.skipif(sys.platform == "win32", reason="bash fakes are POSIX")
def test_build_argv_has_security_flags():
    argv = claude_code.build_argv("claude-sonnet-4-5")
    assert "-p" in argv
    assert "--output-format" in argv and "json" in argv
    assert "--allowedTools" in argv
    assert "--model" in argv and "claude-sonnet-4-5" in argv
