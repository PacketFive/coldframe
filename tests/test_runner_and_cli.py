"""Tests for the runner, the terminal report and the command line.

These cover the path a person actually takes. The gates decide what is true;
this layer decides what gets shown and what the shell sees afterwards, and both
of those are load bearing when the tool runs unattended in CI.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from pybootstrap import cli, gates, report, runner
from pybootstrap.config import Config
from pybootstrap.gates.base import Gate
from pybootstrap.model import Finding, GateResult, Outcome, RunReport


class StubGate:
    """A gate with a decided answer, or one that breaks on contact."""

    description = "stub"

    def __init__(self, name: str, outcome: Outcome | None = None, boom: bool = False) -> None:
        self.name = name
        self.outcome = outcome or Outcome.PASSED
        self.boom = boom
        self.ran = False

    def run(self, config: Config) -> GateResult:
        self.ran = True
        if self.boom:
            raise RuntimeError("gate exploded")
        return GateResult(self.name, self.outcome)


@pytest.fixture
def project(tmp_path: Path) -> Config:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    return Config(root=tmp_path)


def install(monkeypatch, *stubs: StubGate) -> None:
    registry = {s.name: s for s in stubs}
    monkeypatch.setattr(gates, "REGISTRY", registry)
    monkeypatch.setattr(gates, "build", lambda name: registry.get(name))


class TestRunner:
    def test_every_gate_runs_even_after_one_fails(self, project, monkeypatch) -> None:
        """Stopping early would cost a round trip.

        A developer who fixes the formatting only to be shown the type errors
        next has paid for two runs to learn what one could have told them.
        """
        first = StubGate("format", Outcome.FAILED)
        second = StubGate("lint", Outcome.FAILED)
        third = StubGate("test", Outcome.PASSED)
        install(monkeypatch, first, second, third)

        result = runner.run(project, only=["format", "lint", "test"])

        assert all(gate.ran for gate in (first, second, third))
        assert len(result.results) == 3

    def test_a_gate_that_raises_becomes_an_error_not_a_crash(self, project, monkeypatch) -> None:
        """One broken gate must not take the other answers with it."""
        broken = StubGate("lint", boom=True)
        healthy = StubGate("test", Outcome.PASSED)
        install(monkeypatch, broken, healthy)

        result = runner.run(project, only=["lint", "test"])

        assert result.results[0].outcome is Outcome.ERRORED
        assert "RuntimeError" in result.results[0].detail
        assert result.results[1].outcome is Outcome.PASSED
        assert result.exit_code() == 2

    def test_an_unknown_gate_is_reported_rather_than_ignored(self, project, monkeypatch) -> None:
        """A typo in configuration must not silently reduce what is checked.

        Skipping quietly is how a project ends up believing it runs four gates
        while running three.
        """
        install(monkeypatch, StubGate("lint"))

        result = runner.run(project, only=["lint", "typcheck"])

        unknown = [r for r in result.results if r.name == "typcheck"][0]
        assert unknown.outcome is Outcome.ERRORED
        assert "unknown gate" in unknown.detail
        assert result.exit_code() == 2

    def test_progress_hooks_fire_in_order(self, project, monkeypatch) -> None:
        install(monkeypatch, StubGate("lint"), StubGate("test"))
        started: list[str] = []
        finished: list[str] = []

        runner.run(
            project,
            only=["lint", "test"],
            on_start=started.append,
            on_finish=lambda r: finished.append(r.name),
        )

        assert started == ["lint", "test"]
        assert finished == ["lint", "test"]

    def test_configured_gates_are_used_when_none_are_named(self, project, monkeypatch) -> None:
        install(monkeypatch, StubGate("lint"), StubGate("test"))
        project.gates = ["lint"]

        result = runner.run(project)

        assert [r.name for r in result.results] == ["lint"]

    def test_resolve_separates_known_from_unknown(self) -> None:
        known, unknown = runner.resolve(["lint", "nonsense", "test"])
        assert known == ["lint", "test"]
        assert unknown == ["nonsense"]


class TestReport:
    def test_colour_is_suppressed_when_output_is_redirected(self) -> None:
        """Escape codes in a log file are noise, and in a CI summary they can
        be read as part of a filename."""
        assert report.use_colour(io.StringIO()) is False

    def test_a_broken_gate_is_described_differently_from_a_failing_one(self) -> None:
        """The distinction is the whole point of the three-outcome model, so it
        has to survive as far as the words the reader sees."""
        stream = io.StringIO()
        report.print_summary(
            RunReport(
                [
                    GateResult("format", Outcome.ERRORED, detail="ruff rejected an argument"),
                    GateResult("lint", Outcome.FAILED),
                ]
            ),
            stream,
        )
        text = stream.getvalue()

        assert "could not run" in text
        assert "made no judgement" in text
        assert "failed" in text

    def test_a_clean_run_says_so(self) -> None:
        stream = io.StringIO()
        report.print_summary(RunReport([GateResult("lint", Outcome.PASSED)]), stream)
        assert "passed" in stream.getvalue().lower()

    def test_findings_are_listed_with_their_location(self) -> None:
        stream = io.StringIO()
        report.print_result(
            GateResult(
                "lint",
                Outcome.FAILED,
                findings=[Finding("src/a.py", "E501", "Line too long", line=4, column=101)],
            ),
            stream,
        )
        text = stream.getvalue()
        assert "src/a.py:4:101" in text
        assert "E501" in text

    def test_warnings_appear_only_when_asked_for(self) -> None:
        """Warnings that cannot fail a build should not compete for attention
        with the findings that can."""
        result = GateResult(
            "typecheck",
            Outcome.PASSED,
            findings=[Finding("src/a.py", "W1", "unused", severity="warning")],
        )

        quiet = io.StringIO()
        report.print_result(result, quiet)
        assert "unused" not in quiet.getvalue()

        loud = io.StringIO()
        report.print_result(result, loud, verbose=True)
        assert "unused" in loud.getvalue()


class TestCli:
    def test_version_is_reported(self, capsys) -> None:
        with pytest.raises(SystemExit) as exit_info:
            cli.main(["--version"])
        assert exit_info.value.code == 0
        assert cli.__version__ in capsys.readouterr().out

    def test_gates_lists_every_registered_gate(self, capsys) -> None:
        assert cli.main(["gates"]) == 0
        out = capsys.readouterr().out
        for name in ("format", "lint", "typecheck", "test", "audit"):
            assert name in out

    def test_info_shows_what_would_run(self, tmp_path, capsys) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\n[tool.pybootstrap]\ngates = ["lint"]\n', encoding="utf-8"
        )
        assert cli.main(["info", "-C", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "demo" in out
        assert "lint" in out

    def test_a_directory_with_no_project_fails_with_advice(self, tmp_path, capsys) -> None:
        """The most likely first mistake, so the message has to point somewhere."""
        code = cli.main(["info", "-C", str(tmp_path)])
        assert code == 2
        assert "pybootstrap new" in capsys.readouterr().err

    def test_check_exit_code_follows_the_worst_outcome(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
        install(monkeypatch, StubGate("lint", Outcome.FAILED), StubGate("test", Outcome.ERRORED))
        assert cli.main(["check", "-C", str(tmp_path), "--gate", "lint", "--gate", "test"]) == 2

    def test_check_writes_one_junit_file_per_gate(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
        install(monkeypatch, StubGate("lint", Outcome.PASSED))
        out = tmp_path / "junit"

        cli.main(["check", "-C", str(tmp_path), "--gate", "lint", "--junit-dir", str(out)])

        assert (out / "junit-lint.xml").is_file()

    def test_new_creates_a_project_that_passes_its_own_gates(self, tmp_path, capsys) -> None:
        """The claim the scaffold makes to its user, checked rather than assumed.

        A generated project that fails the gates it ships with teaches the
        reader to distrust the gates, which is the opposite of the point.
        """
        assert (
            cli.main(
                [
                    "new",
                    "lab-demo",
                    "--path",
                    str(tmp_path / "lab-demo"),
                    "--no-git",
                    "--ci",
                    "none",
                ]
            )
            == 0
        )
        capsys.readouterr()

        completed = subprocess.run(
            [sys.executable, "-m", "pybootstrap", "check"],
            cwd=tmp_path / "lab-demo",
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr

    def test_new_refuses_a_name_that_cannot_be_imported(self, tmp_path, capsys) -> None:
        code = cli.main(["new", "json", "--path", str(tmp_path / "json"), "--no-git"])
        assert code == 2
        assert "standard library" in capsys.readouterr().err

    def test_no_subcommand_prints_help_rather_than_a_traceback(self, capsys) -> None:
        assert cli.main([]) == 2
        assert "usage" in capsys.readouterr().out.lower()


class TestGateRegistry:
    def test_every_registered_gate_satisfies_the_protocol(self) -> None:
        for name in gates.REGISTRY:
            gate = gates.build(name)
            assert isinstance(gate, Gate)
            assert gate.name == name
            assert gate.description

    def test_an_unregistered_name_builds_nothing(self) -> None:
        assert gates.build("nonexistent") is None

    def test_descriptions_are_written_as_statements_of_success(self) -> None:
        """The list doubles as the definition of done, so each line should read
        as the condition that holds when the gate passes."""
        for _, description in gates.available():
            assert description[0].isupper() or description[0].isalpha()
            assert not description.endswith(".")
