"""Generate text by calling the Claude Code CLI (`claude -p`) as a subprocess.

Security (requirements R3-7, design section 7): the paper abstract is untrusted
external text. We disable every tool so Claude cannot touch the filesystem or
run commands even if the abstract contains an instruction like "delete a file",
and we run in an empty temporary directory.

IMPORTANT - CLI options are not yet confirmed.
All `claude` command-line options are collected in CLAUDE_CLI_OPTIONS below.
The flag names for disabling tools, selecting the model, and requesting JSON
output must be verified on the real machine with `claude --help`; the values
here follow design section 7's intended behaviour. Change them in one place.
"""

from __future__ import annotations

import json
import subprocess
import tempfile

from app.llm.base import (
    GenerationError,
    GenerationResult,
    NotInstalledError,
    NotLoggedInError,
    TimeoutError,
    UsageLimitError,
)

# --- CLI options, collected in one place -----------------------------------
# VERIFY ON THE REAL MACHINE with `claude --help`. These follow the intended
# behaviour described in design section 7 but the exact flag spellings are not
# confirmed.
CLAUDE_BIN = "claude"


def build_argv(model: str) -> list[str]:
    """Assemble the argv for a non-interactive, tool-free JSON generation.

    Prompt is passed on stdin, not as an argument.
    """
    return [
        CLAUDE_BIN,
        "-p",  # non-interactive "print" mode
        "--output-format", "json",  # structured output incl. token usage
        "--model", model,
        # Disable all tools so untrusted abstract text cannot trigger any action.
        # VERIFY the flag name; `--allowedTools ""` / `--disallowedTools` /
        # `--permission-mode` are candidates depending on the installed version.
        "--allowedTools", "",
    ]


def _classify(stderr: str, returncode: int) -> GenerationError:
    """Map CLI failure output to a typed error. Heuristic on stderr text."""
    low = stderr.lower()
    if "not found" in low or "no such file" in low or "command not found" in low:
        return NotInstalledError(stderr.strip() or "claude not found")
    if "login" in low or "logged in" in low or "authenticate" in low or "unauthori" in low:
        return NotLoggedInError(stderr.strip() or "not logged in")
    if "usage limit" in low or "rate limit" in low or "quota" in low or "limit reached" in low:
        return UsageLimitError(stderr.strip() or "usage limit reached")
    return GenerationError(stderr.strip() or f"claude exited with code {returncode}")


def _extract(stdout: str) -> GenerationResult:
    """Parse `--output-format json` output into text + token usage.

    The claude JSON envelope wraps the assistant text and usage; tolerate a few
    shapes since the exact schema needs run-time confirmation.
    """
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        # Fall back to treating stdout as the raw text.
        return GenerationResult(text=stdout.strip())

    text = (
        data.get("result")
        or data.get("text")
        or data.get("content")
        or ""
    )
    if isinstance(text, list):  # content blocks
        text = "".join(
            b.get("text", "") for b in text if isinstance(b, dict)
        )
    usage = data.get("usage") or {}
    return GenerationResult(
        text=text.strip() if isinstance(text, str) else "",
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        model=data.get("model"),
    )


def generate(
    prompt: str,
    *,
    model: str,
    timeout: int = 180,
    _runner=subprocess.run,
) -> GenerationResult:
    """Run `claude -p` with tools disabled in an empty temp dir.

    `_runner` is injectable so tests can supply a fake claude command path or a
    stub; production uses subprocess.run.
    """
    argv = build_argv(model)
    with tempfile.TemporaryDirectory(prefix="chem-claude-") as workdir:
        try:
            proc = _runner(
                argv,
                input=prompt,
                text=True,
                capture_output=True,
                cwd=workdir,  # empty directory: nothing to touch
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise NotInstalledError(str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"claude timed out after {timeout}s") from exc

    if proc.returncode != 0:
        raise _classify(proc.stderr or "", proc.returncode)

    result = _extract(proc.stdout or "")
    result.model = result.model or model
    return result
