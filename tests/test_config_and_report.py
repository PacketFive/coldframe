"""Tests for configuration loading, the model and JUnit output."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from coldframe import junit
from coldframe.config import DEFAULT_GATES, ConfigError, find_project_root, load
from coldframe.model import Finding, GateResult, Outcome, RunReport


def suite_of(root: ET.Element) -> ET.Element:
    """Return the single ``testsuite``, asserting it exists.

    ``find`` returns ``Element | None``; unpacking it here keeps every caller
    from repeating the same narrowing, and turns a missing element into one
    clear failure instead of an AttributeError halfway down a test.
    """
    suite = root.find("testsuite")
    assert suite is not None, "expected exactly one testsuite element"
    return suite


def write_project(tmp_path, body: str):
    (tmp_path / "pyproject.toml").write_text(body, encoding="utf-8")
    return tmp_path


class TestConfig:
    def test_defaults_apply_without_a_coldframe_table(self, tmp_path) -> None:
        """An untouched project must still produce a useful answer.

        Requiring configuration before the tool says anything is the difference
        between something a team tries on an existing repository and something
        they never get round to.
        """
        root = write_project(tmp_path, '[project]\nname = "demo"\n')
        config = load(root)
        assert config.gates == list(DEFAULT_GATES)
        assert config.source_dirs == ["src"]
        assert config.project_name == "demo"

    def test_audit_is_not_on_by_default(self) -> None:
        """It needs the network, so it cannot be part of the baseline."""
        assert "audit" not in DEFAULT_GATES

    def test_settings_are_inferred_from_the_tools_own_tables(self, tmp_path) -> None:
        """Facts already stated elsewhere are read, not asked for again."""
        root = write_project(
            tmp_path,
            '[project]\nname = "demo"\n'
            '[tool.ruff]\nline-length = 120\ntarget-version = "py311"\n'
            '[tool.pytest.ini_options]\ntestpaths = ["spec"]\n',
        )
        config = load(root)
        assert config.line_length == 120
        assert config.python_version == "3.11"
        assert config.test_dirs == ["spec"]

    def test_explicit_settings_win(self, tmp_path) -> None:
        root = write_project(
            tmp_path,
            '[project]\nname = "demo"\n'
            '[tool.pytest.ini_options]\ntestpaths = ["spec"]\n'
            '[tool.coldframe]\ntests = ["t"]\ngates = ["lint"]\ncoverage-min = 95\n',
        )
        config = load(root)
        assert config.test_dirs == ["t"]
        assert config.gates == ["lint"]
        assert config.coverage_min == 95.0

    def test_former_table_name_is_read(self, tmp_path) -> None:
        root = write_project(
            tmp_path,
            '[project]\nname = "demo"\n[tool.pybootstrap]\ngates = ["lint"]\ncoverage-min = 90\n',
        )
        config = load(root)
        assert config.gates == ["lint"]
        assert config.coverage_min == 90.0

    def test_current_table_name_wins_over_former(self, tmp_path) -> None:
        root = write_project(
            tmp_path,
            '[project]\nname = "demo"\n'
            '[tool.coldframe]\ngates = ["test"]\n[tool.pybootstrap]\ngates = ["lint"]\n',
        )
        assert load(root).gates == ["test"]

    def test_poetry_projects_are_understood(self, tmp_path) -> None:
        root = write_project(tmp_path, '[tool.poetry]\nname = "legacy-app"\n')
        assert load(root).project_name == "legacy-app"

    def test_missing_pyproject_is_a_clear_error(self, tmp_path) -> None:
        with pytest.raises(ConfigError, match="No pyproject.toml"):
            load(tmp_path / "nowhere")

    def test_invalid_toml_names_the_file(self, tmp_path) -> None:
        root = write_project(tmp_path, "[project\nname =")
        with pytest.raises(ConfigError, match="not valid TOML"):
            load(root)

    @pytest.mark.parametrize(
        "body, message",
        [
            ('[tool.coldframe]\ncoverage-min = "high"\n', "must be a number"),
            ("[tool.coldframe]\ncoverage-min = 150\n", "between 0 and 100"),
            ("[tool.coldframe]\ngates = 3\n", "list of strings"),
        ],
    )
    def test_contradictory_settings_are_rejected(self, tmp_path, body: str, message: str) -> None:
        root = write_project(tmp_path, f'[project]\nname = "d"\n{body}')
        with pytest.raises(ConfigError, match=message):
            load(root)

    def test_missing_directories_are_filtered_out(self, tmp_path) -> None:
        """Passing a path that does not exist makes ruff and pyright exit with a
        usage error, which would be reported as a broken gate rather than an
        empty project."""
        root = write_project(tmp_path, '[project]\nname = "demo"\n')
        (root / "src").mkdir()
        assert load(root).existing_paths() == ["src"]

    def test_root_is_found_from_a_subdirectory(self, tmp_path) -> None:
        root = write_project(tmp_path, '[project]\nname = "demo"\n')
        nested = root / "src" / "deep" / "deeper"
        nested.mkdir(parents=True)
        assert find_project_root(nested) == root.resolve()


class TestExitCodes:
    def test_clean_run_is_zero(self) -> None:
        report = RunReport([GateResult("lint", Outcome.PASSED)])
        assert report.exit_code() == 0
        assert report.ok

    def test_a_failure_is_one(self) -> None:
        report = RunReport([GateResult("lint", Outcome.FAILED)])
        assert report.exit_code() == 1

    def test_a_gate_that_could_not_run_is_two(self) -> None:
        report = RunReport([GateResult("lint", Outcome.ERRORED)])
        assert report.exit_code() == 2

    def test_could_not_run_outranks_failed(self) -> None:
        """Broken tooling is the more urgent signal.

        If a run reports both, the failures may be an artefact of the broken
        gate, so the exit code points at the cause rather than the symptom.
        """
        report = RunReport(
            [GateResult("lint", Outcome.FAILED), GateResult("format", Outcome.ERRORED)]
        )
        assert report.exit_code() == 2

    def test_skipped_gates_do_not_block(self) -> None:
        report = RunReport([GateResult("test", Outcome.SKIPPED, detail="no tests yet")])
        assert report.exit_code() == 0

    def test_errored_is_blocking(self) -> None:
        assert Outcome.ERRORED.is_blocking
        assert Outcome.FAILED.is_blocking
        assert not Outcome.PASSED.is_blocking
        assert not Outcome.SKIPPED.is_blocking


class TestJunit:
    def test_a_broken_gate_is_an_error_element_not_a_failure(self, tmp_path) -> None:
        """JUnit already draws the distinction this tool needs.

        ``<error>`` means the test could not run, ``<failure>`` means it ran and
        judged the code. Mapping ERRORED onto ``<failure>`` would lose exactly
        the information that makes a broken gate visible in CI.
        """
        result = GateResult("format", Outcome.ERRORED, detail="ruff rejected --output-format")
        path = junit.write_gate(result, tmp_path / "j.xml", "CQC")
        root = ET.parse(path).getroot()

        assert root.find(".//error") is not None
        assert root.find(".//failure") is None
        assert suite_of(root).get("errors") == "1"

    def test_findings_group_by_file_and_rule(self, tmp_path) -> None:
        result = GateResult(
            "lint",
            Outcome.FAILED,
            findings=[
                Finding("src/a.py", "E501", "too long", line=1),
                Finding("src/a.py", "E501", "too long", line=2),
                Finding("src/b.py", "F401", "unused", line=3),
            ],
        )
        root = ET.parse(junit.write_gate(result, tmp_path / "j.xml", "CQC")).getroot()
        names = sorted(c.get("name") or "" for c in root.iter("testcase"))
        assert names == ["src/a.py::E501", "src/b.py::F401"]
        assert suite_of(root).get("failures") == "2"

    def test_warnings_do_not_produce_failures(self, tmp_path) -> None:
        result = GateResult(
            "typecheck",
            Outcome.PASSED,
            findings=[Finding("src/a.py", "reportUnused", "unused", severity="warning")],
        )
        root = ET.parse(junit.write_gate(result, tmp_path / "j.xml", "CQC")).getroot()
        assert root.find(".//failure") is None
        assert root.find(".//system-out") is not None

    def test_the_command_is_included_so_it_can_be_reproduced(self, tmp_path) -> None:
        """A CI failure nobody can reproduce becomes a guessing game."""
        result = GateResult(
            "lint",
            Outcome.FAILED,
            findings=[Finding("src/a.py", "E501", "too long")],
            command=["ruff", "check", "src"],
        )
        root = ET.parse(junit.write_gate(result, tmp_path / "j.xml", "CQC")).getroot()
        failure = root.find(".//failure")
        assert failure is not None
        assert "ruff check src" in (failure.text or "")

    def test_one_file_per_gate(self, tmp_path) -> None:
        report = RunReport([GateResult("lint", Outcome.PASSED), GateResult("test", Outcome.PASSED)])
        written = junit.write_report(report, tmp_path / "out", "CQC")
        assert sorted(p.name for p in written) == ["junit-lint.xml", "junit-test.xml"]

    def test_suite_names_carry_the_configured_prefix(self, tmp_path) -> None:
        result = GateResult("lint", Outcome.PASSED)
        root = ET.parse(junit.write_gate(result, tmp_path / "j.xml", "My Project")).getroot()
        assert suite_of(root).get("name") == "My Project - Lint"
