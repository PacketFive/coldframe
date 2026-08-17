"""Result types shared by every gate.

A gate reports three distinct outcomes and the distinction matters more than it
first appears:

``passed``
    The tool ran and found nothing to complain about.
``failed``
    The tool ran and found real problems. This is the outcome CI should block on.
``errored``
    The tool did not run. It was missing, it rejected its arguments, or it
    crashed. No judgement about the code was made.

Collapsing the last two into "non-zero exit" is the mistake that makes a quality
gate lie. A checker invoked with an argument it does not support exits non-zero
without inspecting a single file, and a pipeline that treats that as "failed"
looks strict while a pipeline that swallows it with ``|| true`` looks green. Both
are wrong, and the second is worse because nobody notices.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Outcome(enum.Enum):
    """How a gate finished. See the module docstring for why there are three."""

    PASSED = "passed"
    FAILED = "failed"
    ERRORED = "errored"
    SKIPPED = "skipped"

    @property
    def is_blocking(self) -> bool:
        """Whether this outcome should stop a pipeline.

        ``ERRORED`` blocks. A gate that could not run has not cleared the code,
        and treating "unknown" as "fine" is how an unconfigured checker survives
        for months.
        """
        return self in (Outcome.FAILED, Outcome.ERRORED)


@dataclass(frozen=True)
class Finding:
    """One thing a tool objected to.

    Every gate normalises its tool's native output into these, so the reporters
    do not need to know which tool produced what.
    """

    path: str
    """File the finding relates to, relative to the project root where possible."""

    code: str
    """Tool-specific rule identifier, for example ``E501`` or ``reportUnusedImport``."""

    message: str
    line: int | None = None
    column: int | None = None
    severity: str = "error"
    """``error`` counts towards failure. ``warning`` is reported but does not block."""

    @property
    def location(self) -> str:
        """``path:line:column``, omitting the parts the tool did not supply."""
        if self.line is None:
            return self.path
        if self.column is None:
            return f"{self.path}:{self.line}"
        return f"{self.path}:{self.line}:{self.column}"

    def __str__(self) -> str:
        return f"{self.location} {self.code} {self.message}"


@dataclass
class GateResult:
    """What a gate produced.

    ``findings`` may be non-empty on a pass when every finding is a warning.
    """

    name: str
    outcome: Outcome
    findings: list[Finding] = field(default_factory=list)
    duration_s: float = 0.0
    detail: str = ""
    """Human-readable note. Carries the reason on ``ERRORED`` and ``SKIPPED``."""

    command: list[str] = field(default_factory=list)
    """The argv actually executed, so a developer can reproduce it by hand."""

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    def summary(self) -> str:
        """One line suitable for a terminal."""
        if self.outcome is Outcome.PASSED:
            extra = f" ({len(self.warnings)} warnings)" if self.warnings else ""
            return f"{self.name}: passed{extra}"
        if self.outcome is Outcome.SKIPPED:
            return f"{self.name}: skipped, {self.detail}"
        if self.outcome is Outcome.ERRORED:
            return f"{self.name}: did not run, {self.detail}"
        return f"{self.name}: failed, {len(self.errors)} problems"


@dataclass
class RunReport:
    """Every gate result from one invocation."""

    results: list[GateResult] = field(default_factory=list)

    @property
    def blocking(self) -> list[GateResult]:
        return [r for r in self.results if r.outcome.is_blocking]

    @property
    def ok(self) -> bool:
        return not self.blocking

    def exit_code(self) -> int:
        """``0`` clean, ``1`` a gate failed, ``2`` a gate could not run.

        ``2`` outranks ``1``. A run where a tool never executed is a worse
        signal than one where the checks ran and found problems, because the
        first tells you nothing about the code.
        """
        if any(r.outcome is Outcome.ERRORED for r in self.results):
            return 2
        if any(r.outcome is Outcome.FAILED for r in self.results):
            return 1
        return 0
