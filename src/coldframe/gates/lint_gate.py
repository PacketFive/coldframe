"""Lint gate, ``ruff check``.

``ruff check`` does support ``--output-format=json``, so unlike the format gate
this one can consume structured output. Exit codes are the same three-way split:
0 clean, 1 problems found, anything else means ruff did not do the job.
"""

from __future__ import annotations

import json

from coldframe.config import Config
from coldframe.gates.base import Completed, missing_tool, run_tool, skipped
from coldframe.model import Finding, GateResult, Outcome

RUFF_INSTALL_HINT = "pip install ruff"


class LintGate:
    name = "lint"
    description = "No lint rule violations"

    def run(self, config: Config) -> GateResult:
        paths = config.existing_paths()
        if not paths:
            return skipped(self.name, "no source or test directories exist yet")

        argv = ["ruff", "check", "--output-format=json", *paths]
        completed = run_tool(argv, cwd=str(config.root))
        return self._interpret(completed)

    def _interpret(self, completed: Completed) -> GateResult:
        if not completed.launched:
            return missing_tool(self.name, completed, RUFF_INSTALL_HINT)

        if completed.returncode not in (0, 1):
            return GateResult(
                name=self.name,
                outcome=Outcome.ERRORED,
                detail=(
                    f"ruff check exited {completed.returncode} without linting. "
                    f"Output: {completed.output[:400]}"
                ),
                duration_s=completed.duration_s,
                command=completed.argv,
            )

        try:
            payload = json.loads(completed.stdout or "[]")
        except json.JSONDecodeError as exc:
            return GateResult(
                name=self.name,
                outcome=Outcome.ERRORED,
                detail=f"ruff check produced output that is not JSON: {exc}",
                duration_s=completed.duration_s,
                command=completed.argv,
            )

        findings = [_to_finding(item) for item in payload if isinstance(item, dict)]
        outcome = Outcome.FAILED if findings else Outcome.PASSED

        # Exit 1 with an empty list would mean ruff found something it did not
        # report. Better to say so than to pass on a contradiction.
        if completed.returncode == 1 and not findings:
            return GateResult(
                name=self.name,
                outcome=Outcome.ERRORED,
                detail="ruff check reported failure but listed no violations",
                duration_s=completed.duration_s,
                command=completed.argv,
            )

        return GateResult(
            name=self.name,
            outcome=outcome,
            findings=findings,
            duration_s=completed.duration_s,
            command=completed.argv,
        )


def _to_finding(item: dict) -> Finding:
    location = item.get("location") or {}
    return Finding(
        path=str(item.get("filename", "?")),
        code=str(item.get("code") or "ruff"),
        message=str(item.get("message", "")),
        line=_int_or_none(location.get("row")),
        column=_int_or_none(location.get("column")),
    )


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None
