"""Dependency audit gate, ``pip-audit``.

Off by default. It needs network access to fetch the advisory database, and a
gate that only works when the network is up is a gate that fails on aeroplanes
and in air-gapped build agents. Projects opt in by listing ``audit`` in
``gates``.

Because the failure is environmental as often as it is real, an audit that
cannot reach its database reports ERRORED rather than FAILED. "We could not
check" and "we checked and found a vulnerability" call for different responses.
"""

from __future__ import annotations

import json

from coldframe.config import Config
from coldframe.gates.base import Completed, missing_tool, run_tool
from coldframe.model import Finding, GateResult, Outcome

PIP_AUDIT_INSTALL_HINT = "pip install pip-audit"

_OFFLINE_MARKERS = (
    "temporary failure in name resolution",
    "failed to establish a new connection",
    "connection refused",
    "connectionerror",
    "read timed out",
    "max retries exceeded",
    "certificate verify failed",
    "proxy",
)


class AuditGate:
    name = "audit"
    description = "No known vulnerabilities in the dependency tree"

    def run(self, config: Config) -> GateResult:
        argv = ["pip-audit", "--format=json", "--progress-spinner=off"]
        options = config.options_for("audit")
        for ignored in options.get("ignore-vulns", []) or []:
            argv.extend(["--ignore-vuln", str(ignored)])

        completed = run_tool(argv, cwd=str(config.root), timeout=600)
        return self._interpret(completed)

    def _interpret(self, completed: Completed) -> GateResult:
        if not completed.launched:
            return missing_tool(self.name, completed, PIP_AUDIT_INSTALL_HINT)

        lowered = completed.output.lower()
        if completed.returncode != 0 and any(m in lowered for m in _OFFLINE_MARKERS):
            return GateResult(
                name=self.name,
                outcome=Outcome.ERRORED,
                detail=(
                    "pip-audit could not reach the advisory database, so no audit "
                    f"was performed. Output: {completed.output[:300]}"
                ),
                duration_s=completed.duration_s,
                command=completed.argv,
            )

        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return GateResult(
                name=self.name,
                outcome=Outcome.ERRORED,
                detail=(
                    f"pip-audit exited {completed.returncode} without usable JSON. "
                    f"Output: {completed.output[:300]}"
                ),
                duration_s=completed.duration_s,
                command=completed.argv,
            )

        findings = _collect(payload)
        return GateResult(
            name=self.name,
            outcome=Outcome.FAILED if findings else Outcome.PASSED,
            findings=findings,
            duration_s=completed.duration_s,
            command=completed.argv,
        )


def _collect(payload: object) -> list[Finding]:
    """Read both pip-audit JSON shapes.

    Older releases emit a bare list of dependencies, newer ones wrap it in a
    ``dependencies`` key. Handling both means the gate survives a pip-audit
    upgrade instead of silently reporting a clean tree.
    """
    if isinstance(payload, dict):
        dependencies = payload.get("dependencies", [])
    elif isinstance(payload, list):
        dependencies = payload
    else:
        return []

    findings: list[Finding] = []
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            continue
        package = str(dependency.get("name", "?"))
        version = str(dependency.get("version", "?"))
        for vuln in dependency.get("vulns", []) or []:
            if not isinstance(vuln, dict):
                continue
            fixed = ", ".join(str(v) for v in vuln.get("fix_versions", []) or [])
            remedy = f" Fixed in {fixed}." if fixed else " No fixed version published yet."
            findings.append(
                Finding(
                    path=f"{package}=={version}",
                    code=str(vuln.get("id", "VULN")),
                    message=(str(vuln.get("description", "")).splitlines() or [""])[0] + remedy,
                )
            )
    return findings
