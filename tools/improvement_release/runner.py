"""Injectable subprocess runner for the release lane (#3218, lane 6).

The drill, the real rollback, ``open_pr``, and ``expose`` all shell out to
``git`` and ``gh``. Each takes a :class:`Runner` so a test can seed canned
answers with :class:`RecordingRunner` and assert the exact argv sequence,
while production uses :class:`SubprocessRunner`.

Every argv is a list; nothing here runs through a shell. A runner never
raises on a nonzero exit: the exit code is the answer, and the caller records
it. A timeout is reported as ``returncode=124, timed_out=True`` (the ``timeout(1)``
convention) so a drill step that hangs becomes a recorded failure rather than
an exception unwinding past the worktree cleanup.

:func:`run_step` is the one step executor the drill and ``rollback()`` share:
it runs one command, appends a readable block to the transcript that becomes
``drill_log``, and returns the compact record that goes in
``rollback_drill["steps"]``.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

#: Exit code reported for a command that exceeded its timeout.
TIMEOUT_RETURNCODE = 124

#: Exit code reported when the executable could not be started at all.
NOT_FOUND_RETURNCODE = 127

#: Bytes of stdout/stderr kept on a step record.
TAIL_BYTES = 2048


@dataclass
class CommandResult:
    """What one command did. ``seconds`` is wall time."""

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class Runner(Protocol):
    """Run ``argv`` (a list, never a shell string) and report the result."""

    def __call__(
        self, argv: list[str], *, cwd: str | None = None, timeout: float | None = None
    ) -> CommandResult: ...


def _decode(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _append_log(log_path: str | Path | None, entry: dict) -> None:
    if log_path is None:
        return
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


class SubprocessRunner:
    """The real runner: ``subprocess.run`` with captured text output.

    With ``log_path`` set, every call appends one JSON line
    ``{argv, cwd, returncode, seconds, timed_out}`` (the CLI's ``--runner-log``).
    """

    def __init__(self, *, log_path: str | Path | None = None):
        self.log_path = log_path

    def __call__(
        self, argv: list[str], *, cwd: str | None = None, timeout: float | None = None
    ) -> CommandResult:
        argv = [str(part) for part in argv]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
            )
            result = CommandResult(
                argv=argv,
                returncode=completed.returncode,
                stdout=_decode(completed.stdout),
                stderr=_decode(completed.stderr),
                seconds=time.monotonic() - started,
            )
        except subprocess.TimeoutExpired as exc:
            result = CommandResult(
                argv=argv,
                returncode=TIMEOUT_RETURNCODE,
                stdout=_decode(exc.stdout),
                stderr=_decode(exc.stderr) + f"\n[timed out after {timeout} s]",
                seconds=time.monotonic() - started,
                timed_out=True,
            )
        except (FileNotFoundError, PermissionError) as exc:
            result = CommandResult(
                argv=argv,
                returncode=NOT_FOUND_RETURNCODE,
                stdout="",
                stderr=f"{type(exc).__name__}: {exc}",
                seconds=time.monotonic() - started,
            )
        _append_log(
            self.log_path,
            {
                "argv": argv,
                "cwd": cwd,
                "returncode": result.returncode,
                "seconds": result.seconds,
                "timed_out": result.timed_out,
            },
        )
        return result


Matcher = Callable[[list[str]], bool] | list[str] | tuple[str, ...]
Response = CommandResult | tuple[int, str, str] | Callable[[list[str]], "CommandResult | tuple"]


class RecordingRunner:
    """A runner for tests: canned responses, every call recorded.

    ``responses`` is a list of ``(matcher, response)`` pairs consulted in
    order. A matcher is either a callable over ``argv`` or an argv prefix
    (``["git", "push"]`` matches any ``git push ...``). A response is a
    :class:`CommandResult`, a ``(returncode, stdout, stderr)`` tuple, or a
    callable over ``argv`` returning one of those. Unmatched calls answer
    ``returncode=0`` with empty output. Every call lands in ``.calls`` as
    ``{"argv", "cwd", "timeout"}``.
    """

    def __init__(
        self,
        responses: list[tuple[Matcher, Response]] | None = None,
        *,
        log_path: str | Path | None = None,
    ):
        self.responses = list(responses or [])
        self.calls: list[dict] = []
        self.log_path = log_path

    @staticmethod
    def _matches(matcher: Matcher, argv: list[str]) -> bool:
        if callable(matcher):
            return bool(matcher(argv))
        prefix = list(matcher)
        return argv[: len(prefix)] == prefix

    def _response_for(self, argv: list[str]) -> CommandResult:
        for matcher, response in self.responses:
            if not self._matches(matcher, argv):
                continue
            if callable(response) and not isinstance(response, CommandResult):
                response = response(argv)
            if isinstance(response, CommandResult):
                return CommandResult(
                    argv=argv,
                    returncode=response.returncode,
                    stdout=response.stdout,
                    stderr=response.stderr,
                    seconds=response.seconds,
                    timed_out=response.timed_out,
                )
            returncode, stdout, stderr = response
            return CommandResult(
                argv=argv,
                returncode=int(returncode),
                stdout=stdout,
                stderr=stderr,
                seconds=0.0,
                timed_out=int(returncode) == TIMEOUT_RETURNCODE,
            )
        return CommandResult(argv=argv, returncode=0, stdout="", stderr="", seconds=0.0)

    def __call__(
        self, argv: list[str], *, cwd: str | None = None, timeout: float | None = None
    ) -> CommandResult:
        argv = [str(part) for part in argv]
        self.calls.append({"argv": argv, "cwd": cwd, "timeout": timeout})
        result = self._response_for(argv)
        _append_log(
            self.log_path,
            {
                "argv": argv,
                "cwd": cwd,
                "returncode": result.returncode,
                "seconds": result.seconds,
                "timed_out": result.timed_out,
            },
        )
        return result

    def argvs(self) -> list[list[str]]:
        """The recorded argv list, in call order."""
        return [call["argv"] for call in self.calls]


def tail(text: str, limit: int = TAIL_BYTES) -> str:
    """The last ``limit`` bytes of ``text`` as UTF-8, without splitting a character."""
    if text is None:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[-limit:].decode("utf-8", errors="ignore")


def run_step(
    runner: Runner,
    argv: list[str],
    *,
    cwd: str | None,
    timeout: float | None = None,
    transcript: list[str],
    name: str | None = None,
) -> dict:
    """Run one command, log it to ``transcript``, and return its step record.

    The record is ``{name, argv, returncode, seconds, timed_out, stdout_tail,
    stderr_tail}``; ``name`` defaults to the first two argv words. The
    transcript block is human-readable: the command, its output, and
    ``[exit N in S s]``.
    """
    argv = [str(part) for part in argv]
    result = runner(argv, cwd=cwd, timeout=timeout)
    step_name = name or " ".join(argv[:2])
    block = [f"$ {' '.join(argv)}"]
    if result.stdout:
        block.append(result.stdout.rstrip("\n"))
    if result.stderr:
        block.append(result.stderr.rstrip("\n"))
    suffix = " (timed out)" if result.timed_out else ""
    block.append(f"[exit {result.returncode} in {result.seconds:.2f} s]{suffix}")
    transcript.append("\n".join(block))
    return {
        "name": step_name,
        "argv": argv,
        "returncode": result.returncode,
        "seconds": round(result.seconds, 3),
        "timed_out": result.timed_out,
        "stdout_tail": tail(result.stdout),
        "stderr_tail": tail(result.stderr),
    }


def result_to_dict(result: CommandResult) -> dict:
    """Plain-dict form of a :class:`CommandResult` for JSON records."""
    return asdict(result)


__all__ = [
    "NOT_FOUND_RETURNCODE",
    "TAIL_BYTES",
    "TIMEOUT_RETURNCODE",
    "CommandResult",
    "RecordingRunner",
    "Runner",
    "SubprocessRunner",
    "result_to_dict",
    "run_step",
    "tail",
]
