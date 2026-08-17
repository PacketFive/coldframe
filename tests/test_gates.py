"""Tests for gate result interpretation.

These matter more than they look. Every gate turns a tool's exit code and output
into one of three outcomes, and the whole value of the tool rests on that
mapping being right. In particular, a tool that refused to start must never be
recorded as a pass.
"""

from __future__ import annotations

import json

import pytest

from pybootstrap.gates.audit_gate import AuditGate
from pybootstrap.gates.base import Completed
from pybootstrap.gates.format_gate import FormatGate
from pybootstrap.gates.lint_gate import LintGate
from pybootstrap.gates.test_gate import TestGate
from pybootstrap.gates.typecheck_gate import TypeCheckGate
from pybootstrap.model import Outcome


def completed(returncode: int, stdout: str = "", stderr: str = "", launched: bool = True):
    return Completed(
        argv=["tool", "arg"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_s=0.1,
        launched=launched,
    )


class TestFormatGate:
    def test_clean_run_passes(self) -> None:
        result = FormatGate()._interpret(completed(0))
        assert result.outcome is Outcome.PASSED
        assert not result.findings

    def test_unformatted_files_become_findings(self) -> None:
        stdout = (
            "Would reformat: src/app/main.py\n"
            "Would reformat: src/app/util.py\n"
            "2 files would be reformatted\n"
        )
        result = FormatGate()._interpret(completed(1, stdout))
        assert result.outcome is Outcome.FAILED
        assert [f.path for f in result.findings] == ["src/app/main.py", "src/app/util.py"]

    def test_rejected_argument_errors_rather_than_failing(self) -> None:
        """The bug this gate exists for.

        ``ruff format`` has never accepted ``--output-format``. Ruff exits 2
        with a usage error having read no files. Reporting that as a failure
        would be wrong, and reporting it as a pass, which is what happens when
        CI appends ``|| true``, lets a codebase drift indefinitely behind a
        green badge.
        """
        stderr = "error: unexpected argument '--output-format' found"
        result = FormatGate()._interpret(completed(2, stderr=stderr))

        assert result.outcome is Outcome.ERRORED
        assert result.outcome is not Outcome.PASSED
        assert "without checking anything" in result.detail
        assert result.outcome.is_blocking

    def test_missing_binary_errors_with_an_install_hint(self) -> None:
        result = FormatGate()._interpret(completed(127, launched=False))
        assert result.outcome is Outcome.ERRORED
        assert "pip install ruff" in result.detail

    def test_failure_with_unreadable_output_errors(self) -> None:
        """Exit 1 with nothing parseable means the output format changed."""
        result = FormatGate()._interpret(completed(1, stdout="something else entirely"))
        assert result.outcome is Outcome.ERRORED
        assert "no recognisable" in result.detail


class TestLintGate:
    def test_empty_json_passes(self) -> None:
        result = LintGate()._interpret(completed(0, "[]"))
        assert result.outcome is Outcome.PASSED

    def test_violations_are_parsed_with_locations(self) -> None:
        payload = json.dumps(
            [
                {
                    "filename": "src/app/main.py",
                    "code": "E501",
                    "message": "Line too long",
                    "location": {"row": 12, "column": 89},
                }
            ]
        )
        result = LintGate()._interpret(completed(1, payload))
        assert result.outcome is Outcome.FAILED
        finding = result.findings[0]
        assert finding.code == "E501"
        assert finding.location == "src/app/main.py:12:89"

    def test_usage_error_errors(self) -> None:
        result = LintGate()._interpret(completed(2, stderr="unknown option"))
        assert result.outcome is Outcome.ERRORED

    def test_failure_without_violations_errors(self) -> None:
        """A contradiction is worth reporting rather than resolving silently."""
        result = LintGate()._interpret(completed(1, "[]"))
        assert result.outcome is Outcome.ERRORED

    def test_non_json_output_errors(self) -> None:
        result = LintGate()._interpret(completed(0, "not json at all"))
        assert result.outcome is Outcome.ERRORED


class TestTypeCheckGate:
    def test_no_diagnostics_passes(self) -> None:
        result = TypeCheckGate()._interpret(completed(0, json.dumps({"generalDiagnostics": []})))
        assert result.outcome is Outcome.PASSED

    def test_errors_fail_and_line_numbers_become_one_based(self) -> None:
        payload = json.dumps(
            {
                "generalDiagnostics": [
                    {
                        "file": "src/app/main.py",
                        "severity": "error",
                        "message": "Cannot assign to str",
                        "rule": "reportAssignmentType",
                        "range": {"start": {"line": 9, "character": 4}},
                    }
                ]
            }
        )
        result = TypeCheckGate()._interpret(completed(1, payload))
        assert result.outcome is Outcome.FAILED
        # pyright counts lines from zero, every editor counts from one
        assert result.findings[0].line == 10
        assert result.findings[0].column == 5

    def test_warnings_alone_do_not_block(self) -> None:
        payload = json.dumps(
            {
                "generalDiagnostics": [
                    {
                        "file": "src/app/main.py",
                        "severity": "warning",
                        "message": "Unused import",
                        "range": {"start": {"line": 0, "character": 0}},
                    }
                ]
            }
        )
        result = TypeCheckGate()._interpret(completed(1, payload))
        assert result.outcome is Outcome.PASSED
        assert len(result.warnings) == 1

    def test_unparseable_output_errors(self) -> None:
        result = TypeCheckGate()._interpret(completed(3, "Traceback: node not found"))
        assert result.outcome is Outcome.ERRORED


class TestTestGate:
    def test_passing_suite_passes(self, tmp_path) -> None:
        assert TestGate()._interpret(completed(0), tmp_path / "none.xml").outcome is Outcome.PASSED

    def test_no_tests_collected_errors(self, tmp_path) -> None:
        """An empty suite must not read as success.

        pytest exits 5 when it collects nothing, which commonly means a rename
        orphaned the test directory. Passing here would hide the loss of the
        entire suite behind a green gate.
        """
        result = TestGate()._interpret(completed(5), tmp_path / "none.xml")
        assert result.outcome is Outcome.ERRORED
        assert "collected no tests" in result.detail

    @pytest.mark.parametrize("code", [2, 3, 4])
    def test_internal_pytest_failures_error(self, code: int, tmp_path) -> None:
        result = TestGate()._interpret(completed(code), tmp_path / "none.xml")
        assert result.outcome is Outcome.ERRORED

    def test_coverage_shortfall_is_explained(self, tmp_path) -> None:
        """Coverage failures arrive with every test passing.

        pytest-cov exits 1 while the JUnit file records no failures, so without
        special handling the gate would report "failed, 0 problems" and send the
        reader looking for a test that does not exist.
        """
        output = "Required test coverage of 80.0% not reached. Total coverage: 61.20%"
        result = TestGate()._interpret(completed(1, stdout=output), tmp_path / "none.xml")
        assert result.outcome is Outcome.FAILED
        assert result.findings[0].code == "coverage"

    def test_failures_are_read_from_junit(self, tmp_path) -> None:
        report = tmp_path / "pytest.xml"
        report.write_text(
            '<?xml version="1.0"?><testsuites><testsuite name="pytest">'
            '<testcase classname="tests.test_app" name="test_adds">'
            '<failure message="assert 1 == 2">details</failure>'
            "</testcase></testsuite></testsuites>",
            encoding="utf-8",
        )
        result = TestGate()._interpret(completed(1), report)
        assert result.outcome is Outcome.FAILED
        assert "test_adds" in result.findings[0].message


class TestAuditGate:
    def test_clean_tree_passes(self) -> None:
        result = AuditGate()._interpret(completed(0, json.dumps({"dependencies": []})))
        assert result.outcome is Outcome.PASSED

    def test_offline_errors_rather_than_failing(self) -> None:
        """No network is not the same as a vulnerability.

        Reporting a connection failure as FAILED would train people to ignore
        the audit gate, which is the one gate whose warnings are about real
        published attacks.
        """
        stderr = "ConnectionError: Max retries exceeded with url: /vulns"
        result = AuditGate()._interpret(completed(1, stderr=stderr))
        assert result.outcome is Outcome.ERRORED
        assert "could not reach" in result.detail

    def test_vulnerabilities_become_findings(self) -> None:
        payload = json.dumps(
            {
                "dependencies": [
                    {
                        "name": "requests",
                        "version": "2.19.0",
                        "vulns": [
                            {
                                "id": "GHSA-x84v",
                                "description": "Credential leak on redirect",
                                "fix_versions": ["2.31.0"],
                            }
                        ],
                    }
                ]
            }
        )
        result = AuditGate()._interpret(completed(1, payload))
        assert result.outcome is Outcome.FAILED
        assert result.findings[0].code == "GHSA-x84v"
        assert "Fixed in 2.31.0" in result.findings[0].message

    def test_bare_list_payload_is_understood(self) -> None:
        """Older pip-audit emits a list, newer wraps it in a key.

        Handling one shape only would report a clean tree after an upgrade,
        which is a false negative in the gate most likely to matter.
        """
        payload = json.dumps(
            [
                {
                    "name": "urllib3",
                    "version": "1.0",
                    "vulns": [{"id": "PYSEC-1", "description": "x"}],
                }
            ]
        )
        result = AuditGate()._interpret(completed(1, payload))
        assert result.outcome is Outcome.FAILED
        assert result.findings[0].code == "PYSEC-1"
