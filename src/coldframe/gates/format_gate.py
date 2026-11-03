"""Format gate, ``ruff format --check``.

This gate exists because of a specific failure. The obvious invocation is::

    ruff format --check --output-format=json src tests

``ruff format`` has never accepted ``--output-format``; that flag belongs to
``ruff check``. Ruff rejects it with a usage error and exits 2 without reading a
single file. Pair that with the ``|| true`` that CI scripts use to keep a later
publish step alive and the gate reports success forever, on any codebase, in any
state. A repository can drift to the point where a third of its files would be
reformatted while the dashboard stays green.

So this gate does two things. It uses the plain text output that every ruff
version supports, and it treats exit 2 as ERRORED rather than folding it into
the failure path. A checker that refused to start is not a checker that passed,
and it is not a checker that failed either.
"""

from __future__ import annotations

import re

from coldframe.config import Config
from coldframe.gates.base import Completed, missing_tool, run_tool, skipped
from coldframe.model import Finding, GateResult, Outcome

RUFF_INSTALL_HINT = "pip install ruff"
_LOCATION = re.compile(r":\d+:\d+$")


class FormatGate:
    name = "format"
    description = "Every file matches the formatter's output"

    def run(self, config: Config) -> GateResult:
        paths = config.existing_paths()
        if not paths:
            return skipped(self.name, "no source or test directories exist yet")

        argv = ["ruff", "format", "--check", *paths]
        completed = run_tool(argv, cwd=str(config.root))
        return self._interpret(completed)

    def _interpret(self, completed: Completed) -> GateResult:
        if not completed.launched:
            return missing_tool(self.name, completed, RUFF_INSTALL_HINT)

        if completed.returncode == 0:
            return GateResult(
                name=self.name,
                outcome=Outcome.PASSED,
                duration_s=completed.duration_s,
                command=completed.argv,
            )

        if completed.returncode == 1:
            findings = _parse_would_reformat(completed.stdout)
            if not findings:
                # Exit 1 with nothing parseable means ruff changed its output
                # and this gate can no longer read it. Reporting "failed with
                # no findings" would be a puzzle; say the parser broke.
                return GateResult(
                    name=self.name,
                    outcome=Outcome.ERRORED,
                    detail=(
                        "ruff format exited 1 but produced no recognisable "
                        f"file names. Raw output: {completed.output[:400]}"
                    ),
                    duration_s=completed.duration_s,
                    command=completed.argv,
                )
            return GateResult(
                name=self.name,
                outcome=Outcome.FAILED,
                findings=findings,
                duration_s=completed.duration_s,
                command=completed.argv,
            )

        return GateResult(
            name=self.name,
            outcome=Outcome.ERRORED,
            detail=(
                f"ruff format exited {completed.returncode} without checking anything. "
                f"Output: {completed.output[:400]}"
            ),
            duration_s=completed.duration_s,
            command=completed.argv,
        )


def _parse_would_reformat(stdout: str) -> list[Finding]:
    """Pull paths out of ruff's plain check output.

    Older ruff prints one ``Would reformat: <path>`` line per file. Ruff 0.17
    and later print an ``unformatted:`` diagnostic per file instead, with the
    location on a following ``--> <path>:<line>:<col>`` line. Both shapes are
    accepted, so the gate keeps working across the upgrade.
    """
    findings: list[Finding] = []
    seen: set[str] = set()
    in_diagnostic = False
    for raw in stdout.splitlines():
        line = raw.strip()
        path = ""
        if line.startswith("Would reformat:"):
            path = line.split(":", 1)[1].strip()
        elif line.startswith("unformatted:"):
            in_diagnostic = True
            continue
        elif in_diagnostic and line.startswith("-->"):
            in_diagnostic = False
            path = _LOCATION.sub("", line[3:].strip())
        if path and path not in seen:
            seen.add(path)
            findings.append(
                Finding(
                    path=path,
                    code="format",
                    message="File is not formatted. Run 'ruff format' to fix it.",
                )
            )
    return findings
