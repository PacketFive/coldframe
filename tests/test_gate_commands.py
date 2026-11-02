"""Tests for what the gates actually execute.

The gates are covered elsewhere for how they read a tool's output. These cover
the argument list they hand to it, which is where the bug that motivated this
project lived: ``ruff format`` was being given a flag it has never accepted, so
it exited without reading a file while the pipeline recorded a pass.

Nothing here runs a real tool. ``run_tool`` is intercepted and the argv is
inspected, so the tests state the contract rather than the local versions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coldframe.config import Config
from coldframe.gates import audit_gate, base, format_gate, lint_gate, test_gate, typecheck_gate
from coldframe.model import Outcome


@pytest.fixture
def project(tmp_path: Path) -> Config:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo").mkdir()
    (tmp_path / "src" / "demo" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    return Config(root=tmp_path, project_name="demo", coverage_min=80.0)


@pytest.fixture
def capture(monkeypatch):
    """Intercept every tool launch and record the argv."""
    calls: list[list[str]] = []

    def fake(argv, cwd=None, timeout=None, env=None):
        calls.append(list(argv))
        return base.Completed(
            argv=list(argv),
            returncode=0,
            stdout="[]",
            stderr="",
            duration_s=0.0,
            launched=True,
        )

    for module in (format_gate, lint_gate, typecheck_gate, test_gate, audit_gate):
        monkeypatch.setattr(module, "run_tool", fake)
    return calls


class TestFormatCommand:
    def test_the_formatter_is_asked_to_check_not_to_write(self, project, capture) -> None:
        """A gate that reformats the tree turns a report into an edit, and in
        CI it would silently disagree with the commit under test."""
        format_gate.FormatGate().run(project)
        assert capture[0][:3] == ["ruff", "format", "--check"]

    def test_no_output_format_flag_is_passed(self, project, capture) -> None:
        """The bug this project was built around.

        ``ruff format`` has never accepted ``--output-format``; that flag
        belongs to ``ruff check``. Passing it makes ruff exit 2 having inspected
        nothing, and paired with the '|| true' that CI pipelines put after the
        command, the gate reports success forever.
        """
        format_gate.FormatGate().run(project)
        assert not any(arg.startswith("--output-format") for arg in capture[0])

    def test_the_configured_paths_are_passed(self, project, capture) -> None:
        format_gate.FormatGate().run(project)
        assert capture[0][-2:] == ["src", "tests"]


class TestLintCommand:
    def test_json_output_is_requested(self, project, capture) -> None:
        """``ruff check`` does support it, and parsing JSON avoids reading a
        human-facing format that changes between releases."""
        lint_gate.LintGate().run(project)
        assert "--output-format=json" in capture[0]
        assert capture[0][:2] == ["ruff", "check"]

    def test_no_fix_flag_is_passed(self, project, capture) -> None:
        """Checking and repairing are separate commands on purpose.

        A check that edits gives a different answer the second time it runs.
        """
        lint_gate.LintGate().run(project)
        assert "--fix" not in capture[0]


class TestTypeCheckCommand:
    def test_json_output_is_requested(self, project, capture) -> None:
        typecheck_gate.TypeCheckGate().run(project)
        assert capture[0][0] == "pyright"
        assert "--outputjson" in capture[0]


class TestTestCommand:
    def test_a_junit_file_is_requested(self, project, capture) -> None:
        """The XML is how failures reach the summary, locally and in CI."""
        test_gate.TestGate().run(project)
        assert any(a.startswith("--junitxml=") for a in capture[0])

    def test_the_coverage_floor_is_passed_to_pytest(self, project, capture) -> None:
        project.coverage_min = 90.0
        test_gate.TestGate().run(project)
        assert "--cov-fail-under=90" in capture[0]

    def test_the_coverage_target_is_inferred_from_the_source_tree(self, project, capture) -> None:
        """Measuring coverage of everything imported, rather than of this
        project, counts the standard library and reads far higher than it is."""
        test_gate.TestGate().run(project)
        assert "--cov=demo" in capture[0]

    def test_an_explicit_coverage_package_wins(self, project, capture) -> None:
        project.coverage_package = "chosen"
        test_gate.TestGate().run(project)
        assert "--cov=chosen" in capture[0]

    def test_coverage_flags_are_absent_when_no_floor_is_set(self, project, capture) -> None:
        project.coverage_min = None
        test_gate.TestGate().run(project)
        assert not any(a.startswith("--cov") for a in capture[0])

    def test_a_project_without_tests_is_skipped_rather_than_failed(self, tmp_path) -> None:
        """Absent tests on a new project is a fact, not a regression.

        Reporting it as a failure would make the first run of the tool on any
        empty project a wall of red, which teaches people to ignore it.
        """
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
        (tmp_path / "src").mkdir()
        result = test_gate.TestGate().run(Config(root=tmp_path))
        assert result.outcome is Outcome.SKIPPED
        assert not result.outcome.is_blocking


class TestAuditCommand:
    def test_json_output_is_requested(self, project, capture) -> None:
        audit_gate.AuditGate().run(project)
        assert capture[0][0] == "pip-audit"
        assert "--format=json" in capture[0]


class TestRunTool:
    def test_a_missing_tool_is_reported_as_not_launched(self) -> None:
        """The difference between 'not installed' and 'found problems' is the
        distinction the whole tool is built on, so it starts here."""
        completed = base.run_tool(["definitely-not-a-real-tool-xyz"], cwd=".")
        assert completed.launched is False
        assert "not installed" in completed.stderr

    def test_output_is_captured_and_the_exit_code_returned(self, tmp_path) -> None:
        completed = base.run_tool(
            ["python3", "-c", "import sys; print('out'); sys.exit(3)"], cwd=str(tmp_path)
        )
        assert completed.launched is True
        assert completed.returncode == 3
        assert "out" in completed.stdout

    def test_stdout_and_stderr_are_both_available(self, tmp_path) -> None:
        completed = base.run_tool(
            ["python3", "-c", "import sys; sys.stderr.write('bad'); print('good')"],
            cwd=str(tmp_path),
        )
        assert "good" in completed.output
        assert "bad" in completed.output

    def test_a_hanging_tool_is_stopped_rather_than_left_to_block_ci(self, tmp_path) -> None:
        """An unattended build agent has no one to press Ctrl-C, so a gate that
        never returns costs the whole job rather than one check."""
        completed = base.run_tool(
            ["python3", "-c", "import time; time.sleep(30)"], cwd=str(tmp_path), timeout=1
        )
        assert completed.launched is False
        assert "timed out" in completed.stderr.lower()

    def test_a_timeout_is_not_described_as_a_missing_tool(self) -> None:
        """The two share a result type but not a remedy, and telling someone to
        install a tool they already have sends them the wrong way."""
        completed = base.Completed(
            argv=["pyright"],
            returncode=base.TIMED_OUT,
            stdout="",
            stderr="timed out after 1s",
            duration_s=1.0,
            launched=False,
        )
        result = base.missing_tool("typecheck", completed, "pip install pyright")
        assert result.outcome is Outcome.ERRORED
        assert "not installed" not in result.detail
        assert "did not finish" in result.detail
