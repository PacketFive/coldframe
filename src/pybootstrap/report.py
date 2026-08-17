"""Terminal reporting.

Colour is used only when the stream is a TTY and ``NO_COLOR`` is unset, so piped
or captured output stays clean. Build logs are read far more often than they are
watched live.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

from pybootstrap.model import GateResult, Outcome, RunReport

_MARKS = {
    Outcome.PASSED: "PASS",
    Outcome.FAILED: "FAIL",
    Outcome.ERRORED: "ERROR",
    Outcome.SKIPPED: "SKIP",
}

_COLOURS = {
    Outcome.PASSED: "\033[32m",
    Outcome.FAILED: "\033[31m",
    Outcome.ERRORED: "\033[33m",
    Outcome.SKIPPED: "\033[90m",
}

_RESET = "\033[0m"
MAX_FINDINGS_SHOWN = 20


def use_colour(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("PYBOOTSTRAP_FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty()


def _mark(outcome: Outcome, coloured: bool) -> str:
    text = f"[{_MARKS[outcome]:^5}]"
    return f"{_COLOURS[outcome]}{text}{_RESET}" if coloured else text


def gate_line(result: GateResult, *, coloured: bool) -> str:
    return f"{_mark(result.outcome, coloured)} {result.summary()} ({result.duration_s:.1f}s)"


def print_result(result: GateResult, stream: TextIO = sys.stdout, *, verbose: bool = False) -> None:
    coloured = use_colour(stream)
    print(gate_line(result, coloured=coloured), file=stream)

    if result.outcome is Outcome.ERRORED and result.command:
        print(f"        command: {' '.join(result.command)}", file=stream)

    findings = result.errors if not verbose else result.findings
    for finding in findings[:MAX_FINDINGS_SHOWN]:
        print(f"        {finding}", file=stream)
    if len(findings) > MAX_FINDINGS_SHOWN:
        print(f"        ... and {len(findings) - MAX_FINDINGS_SHOWN} more", file=stream)


def print_summary(report: RunReport, stream: TextIO = sys.stdout) -> None:
    coloured = use_colour(stream)
    print("", file=stream)

    errored = [r for r in report.results if r.outcome is Outcome.ERRORED]
    failed = [r for r in report.results if r.outcome is Outcome.FAILED]

    if report.ok:
        mark = _mark(Outcome.PASSED, coloured)
        print(f"{mark} all gates passed", file=stream)
        return

    # Errored gates are listed first and named explicitly. They are the ones
    # people misread as failures and then waste time debugging the wrong thing.
    if errored:
        mark = _mark(Outcome.ERRORED, coloured)
        names = ", ".join(r.name for r in errored)
        print(f"{mark} {len(errored)} gate(s) could not run: {names}", file=stream)
        print(
            "        These made no judgement about your code. Fix the tooling first.", file=stream
        )
    if failed:
        mark = _mark(Outcome.FAILED, coloured)
        names = ", ".join(r.name for r in failed)
        print(f"{mark} {len(failed)} gate(s) failed: {names}", file=stream)
