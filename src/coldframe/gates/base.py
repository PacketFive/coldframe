"""Gate protocol and the subprocess plumbing every gate shares."""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from coldframe.config import Config
from coldframe.model import GateResult, Outcome


@dataclass
class Completed:
    """Outcome of running an external tool."""

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    launched: bool = True
    """False when the executable was not on PATH, so nothing ran at all."""

    @property
    def output(self) -> str:
        return (self.stdout + self.stderr).strip()


def run_tool(
    argv: list[str],
    *,
    cwd: str,
    timeout: int = 1800,
    env: dict[str, str] | None = None,
) -> Completed:
    """Run *argv*, never raising for a non-zero exit.

    Gates decide what a return code means, because the meaning is tool-specific.
    Only genuine plumbing failures, a missing binary or a timeout, are turned
    into a distinct result here.
    """
    started = time.monotonic()
    if shutil.which(argv[0]) is None:
        return Completed(
            argv=argv,
            returncode=127,
            stdout="",
            stderr=f"{argv[0]} is not installed or not on PATH",
            duration_s=0.0,
            launched=False,
        )
    try:
        # S603/S607 are suppressed on both the call and the argument line because
        # ruff versions disagree about where the diagnostic is anchored. The
        # argv here is assembled by this module from configuration, never from a
        # shell string, and the tool name is looked up on PATH on purpose so a
        # virtualenv copy wins over a system one.
        proc = subprocess.run(  # noqa: S603
            argv,  # noqa: S603
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # launched=False, because the question is not whether a process started
        # but whether it produced an answer. A tool killed part way through has
        # judged nothing, so it belongs with the missing binary rather than with
        # a real non-zero exit.
        return Completed(
            argv=argv,
            returncode=124,
            stdout="",
            stderr=f"timed out after {timeout}s",
            duration_s=time.monotonic() - started,
            launched=False,
        )
    except OSError as exc:
        return Completed(
            argv=argv,
            returncode=127,
            stdout="",
            stderr=str(exc),
            duration_s=time.monotonic() - started,
            launched=False,
        )
    return Completed(
        argv=argv,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        duration_s=time.monotonic() - started,
    )


@runtime_checkable
class Gate(Protocol):
    """One quality check.

    A gate must never raise. Anything unexpected becomes an ``ERRORED`` result
    so the runner can report every gate rather than stopping at the first
    surprise.
    """

    name: str
    description: str

    def run(self, config: Config) -> GateResult: ...


def errored(name: str, detail: str, argv: list[str] | None = None) -> GateResult:
    return GateResult(
        name=name,
        outcome=Outcome.ERRORED,
        detail=detail,
        command=argv or [],
    )


def skipped(name: str, detail: str) -> GateResult:
    return GateResult(name=name, outcome=Outcome.SKIPPED, detail=detail)


TIMED_OUT = 124


def missing_tool(name: str, completed: Completed, install_hint: str) -> GateResult:
    """Uniform result for a tool that produced no answer.

    Kept separate from a generic error because the fix is usually a specific
    command, and printing it beats making the reader search for the package
    name. A timeout arrives here too, since it also ends with nothing judged,
    but it needs its own wording; telling someone to install a tool that is
    already installed sends them off in the wrong direction.
    """
    if completed.returncode == TIMED_OUT:
        detail = (
            f"{completed.argv[0]} did not finish and was stopped. {completed.stderr}. "
            "Raise the timeout or narrow the paths being checked."
        )
    else:
        detail = f"{completed.argv[0]} is not installed. Install it with: {install_hint}"
    return GateResult(
        name=name,
        outcome=Outcome.ERRORED,
        detail=detail,
        command=completed.argv,
        duration_s=completed.duration_s,
    )
