"""Type check gate, ``pyright --outputjson``.

Warnings are reported but do not block. A project tightens by moving rules from
warning to error in ``[tool.pyright]``, which keeps the decision about rigour in
the tool's own configuration rather than hidden in this wrapper.
"""

from __future__ import annotations

import json

from coldframe.config import Config
from coldframe.gates.base import Completed, missing_tool, run_tool, skipped
from coldframe.model import Finding, GateResult, Outcome

PYRIGHT_INSTALL_HINT = "pip install pyright"


class TypeCheckGate:
    name = "typecheck"
    description = "No static type errors"

    def run(self, config: Config) -> GateResult:
        paths = config.existing_paths()
        if not paths:
            return skipped(self.name, "no source or test directories exist yet")

        argv = ["pyright", "--outputjson", *paths]
        completed = run_tool(argv, cwd=str(config.root))
        return self._interpret(completed)

    def _interpret(self, completed: Completed) -> GateResult:
        if not completed.launched:
            return missing_tool(self.name, completed, PYRIGHT_INSTALL_HINT)

        # pyright writes JSON to stdout and progress to stderr. It exits 1 when
        # it finds errors, and 2+ for its own failures such as a bad config or
        # a node runtime it could not download.
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return GateResult(
                name=self.name,
                outcome=Outcome.ERRORED,
                detail=(
                    f"pyright exited {completed.returncode} without usable JSON. "
                    f"Output: {completed.output[:400]}"
                ),
                duration_s=completed.duration_s,
                command=completed.argv,
            )

        if not isinstance(payload, dict):
            # pyright emits an object. Anything else means the output came from
            # somewhere else, a wrapper script or a proxy, and guessing at it
            # would turn a plumbing problem into an invented type error.
            return GateResult(
                name=self.name,
                outcome=Outcome.ERRORED,
                detail=(
                    "pyright returned JSON that is not an object, so no diagnostics "
                    f"could be read. Output: {completed.output[:400]}"
                ),
                duration_s=completed.duration_s,
                command=completed.argv,
            )

        diagnostics = payload.get("generalDiagnostics")
        if not isinstance(diagnostics, list):
            return GateResult(
                name=self.name,
                outcome=Outcome.ERRORED,
                detail="pyright JSON has no generalDiagnostics array",
                duration_s=completed.duration_s,
                command=completed.argv,
            )

        findings = [
            _to_finding(d)
            for d in diagnostics
            if isinstance(d, dict) and d.get("severity") in ("error", "warning")
        ]
        blocking = [f for f in findings if f.severity == "error"]

        return GateResult(
            name=self.name,
            outcome=Outcome.FAILED if blocking else Outcome.PASSED,
            findings=findings,
            duration_s=completed.duration_s,
            command=completed.argv,
        )


def _to_finding(diagnostic: dict) -> Finding:
    start = (diagnostic.get("range") or {}).get("start") or {}
    severity = str(diagnostic.get("severity", "error"))
    message = str(diagnostic.get("message", "")).splitlines()
    line = start.get("line")
    column = start.get("character")
    return Finding(
        path=str(diagnostic.get("file", "?")),
        code=str(diagnostic.get("rule") or severity),
        message=message[0] if message else "",
        # pyright counts from zero, humans and editors count from one.
        line=line + 1 if isinstance(line, int) else None,
        column=column + 1 if isinstance(column, int) else None,
        severity=severity,
    )
