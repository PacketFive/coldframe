"""Gate registry.

Adding a gate means writing the class and adding one entry here. Projects then
enable it by name in ``[tool.coldframe] gates``.
"""

from __future__ import annotations

from coldframe.gates.audit_gate import AuditGate
from coldframe.gates.base import Gate, errored
from coldframe.gates.format_gate import FormatGate
from coldframe.gates.lint_gate import LintGate
from coldframe.gates.test_gate import TestGate
from coldframe.gates.typecheck_gate import TypeCheckGate

REGISTRY: dict[str, type] = {
    "format": FormatGate,
    "lint": LintGate,
    "typecheck": TypeCheckGate,
    "test": TestGate,
    "audit": AuditGate,
}

__all__ = ["REGISTRY", "Gate", "available", "build", "errored"]


def available() -> list[tuple[str, str]]:
    """Every gate name with its one-line description."""
    return [(name, cls.description) for name, cls in sorted(REGISTRY.items())]


def build(name: str) -> Gate | None:
    cls = REGISTRY.get(name)
    return cls() if cls is not None else None
