"""Test gate, pytest with optional coverage.

pytest's exit codes are richer than the usual pass/fail pair and the difference
is exactly the one this tool cares about:

===== ==========================================================
 Code  Meaning
===== ==========================================================
 0     all tests passed
 1     tests ran, some failed
 2     the run was interrupted
 3     an internal error
 4     pytest was used incorrectly
 5     no tests were collected
===== ==========================================================

Codes 2 to 4 mean the suite did not deliver a verdict, so they map to ERRORED.

Code 5 deserves its own decision. An empty suite passing is how a project ends
up with a green test gate and no tests, usually after a rename quietly orphans
the test directory. This gate treats "no tests collected" as ERRORED. A project
that genuinely has no tests yet should drop ``test`` from its gate list and say
so, rather than run a check that inspects nothing and calls it success.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory

from pybootstrap.config import Config
from pybootstrap.gates.base import Completed, missing_tool, run_tool, skipped
from pybootstrap.model import Finding, GateResult, Outcome

PYTEST_INSTALL_HINT = "pip install pytest"

EXIT_OK = 0
EXIT_TESTS_FAILED = 1
EXIT_NO_TESTS = 5

_COVERAGE_RE = re.compile(r"Required test coverage of ([\d.]+)% not reached")


class TestGate:
    name = "test"
    description = "The test suite passes"

    def run(self, config: Config) -> GateResult:
        if not config.existing_paths(include_tests=True):
            return skipped(self.name, "no source or test directories exist yet")
        if not any((config.root / d).exists() for d in config.test_dirs):
            return skipped(
                self.name,
                f"no test directory found (looked for {', '.join(config.test_dirs)})",
            )

        with TemporaryDirectory(prefix="pybootstrap-") as tmp:
            report = Path(tmp) / "pytest.xml"
            argv = ["pytest", f"--junitxml={report}"]
            if config.coverage_min is not None:
                package = config.coverage_package or _guess_package(config)
                if package:
                    argv.append(f"--cov={package}")
                argv.append(f"--cov-fail-under={config.coverage_min:g}")
            argv.extend(config.test_dirs)

            completed = run_tool(argv, cwd=str(config.root))
            return self._interpret(completed, report)

    def _interpret(self, completed: Completed, report: Path) -> GateResult:
        if not completed.launched:
            return missing_tool(self.name, completed, PYTEST_INSTALL_HINT)

        if completed.returncode == EXIT_NO_TESTS:
            return GateResult(
                name=self.name,
                outcome=Outcome.ERRORED,
                detail=(
                    "pytest collected no tests. Either the test directory is wrong "
                    "or the suite is empty; a gate that inspects nothing must not pass."
                ),
                duration_s=completed.duration_s,
                command=completed.argv,
            )

        if completed.returncode not in (EXIT_OK, EXIT_TESTS_FAILED):
            return GateResult(
                name=self.name,
                outcome=Outcome.ERRORED,
                detail=(
                    f"pytest exited {completed.returncode}, which means the run did not "
                    f"complete. Output: {completed.output[-400:]}"
                ),
                duration_s=completed.duration_s,
                command=completed.argv,
            )

        findings = _parse_junit(report)

        # Coverage below the floor makes pytest exit 1 with every test passing,
        # so the JUnit file holds no failures. Without this the gate would
        # report "failed, 0 problems", which reads like a bug in the tool.
        coverage_shortfall = _COVERAGE_RE.search(completed.output)
        if coverage_shortfall and not findings:
            findings.append(
                Finding(
                    path="coverage",
                    code="coverage",
                    message=coverage_shortfall.group(0),
                )
            )

        outcome = Outcome.FAILED if completed.returncode == EXIT_TESTS_FAILED else Outcome.PASSED
        if outcome is Outcome.FAILED and not findings:
            findings.append(
                Finding(
                    path="pytest",
                    code="pytest",
                    message=f"pytest reported failure. Output: {completed.output[-300:]}",
                )
            )

        return GateResult(
            name=self.name,
            outcome=outcome,
            findings=findings,
            duration_s=completed.duration_s,
            command=completed.argv,
        )


def _guess_package(config: Config) -> str:
    """Best guess at the importable package name for ``--cov``.

    Uses the first directory under the first source root, which is right for the
    src layout this tool scaffolds. Projects that differ set ``coverage-package``
    explicitly rather than having a wrong guess passed to pytest.
    """
    if config.project_name:
        candidate = config.project_name.replace("-", "_")
        for source in config.source_dirs:
            if (config.root / source / candidate).is_dir():
                return candidate
    for source in config.source_dirs:
        directory = config.root / source
        if not directory.is_dir():
            continue
        for child in sorted(directory.iterdir()):
            if child.is_dir() and (child / "__init__.py").exists():
                return child.name
    return ""


def _parse_junit(report: Path) -> list[Finding]:
    """Turn pytest's own JUnit XML into findings."""
    if not report.exists():
        return []
    try:
        # S314: the input is the JUnit file pytest just wrote into a path this
        # process chose. Pulling in defusedxml to parse our own output would add
        # a dependency for a threat that cannot reach here.
        tree = ET.parse(report)  # noqa: S314
    except ET.ParseError:
        return []

    findings: list[Finding] = []
    for case in tree.iter("testcase"):
        for child in case:
            if child.tag not in ("failure", "error"):
                continue
            classname = case.get("classname", "")
            name = case.get("name", "?")
            findings.append(
                Finding(
                    path=classname.replace(".", "/") or "tests",
                    code=child.tag,
                    message=f"{name}: {child.get('message', '')}",
                    line=_int_or_none(case.get("line")),
                )
            )
    return findings


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
