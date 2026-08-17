"""JUnit XML output, so any CI can display gate results as tests.

The Python ecosystem standardises JUnit XML for test runs and nothing else.
Formatters, linters, type checkers and auditors each speak their own dialect,
which is why pipelines end up with a wall of bespoke parsing. Emitting JUnit
from every gate lets one publish step handle all of them, and gives reviewers a
per-file breakdown in the CI test tab rather than a log they have to scroll.

The important detail is how ERRORED is represented. JUnit distinguishes
``<failure>`` from ``<error>``, which lines up exactly with the distinction this
tool cares about: a failure is a judgement about the code, an error means the
tool never delivered one. CI systems surface both as a red test, so a gate that
could not run is visible instead of silently absent.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from pathlib import Path

from pybootstrap.model import GateResult, Outcome, RunReport

MAX_MESSAGE = 200
"""JUnit message attributes are shown in a table cell; keep them short."""


def _testcase(classname: str, name: str) -> ET.Element:
    return ET.Element("testcase", attrib={"classname": classname, "name": name})


def _suite_for(result: GateResult, prefix: str) -> ET.Element:
    classname = f"pybootstrap.{result.name}"
    cases: list[ET.Element] = []

    if result.outcome is Outcome.ERRORED:
        case = _testcase(classname, "gate-did-not-run")
        error = ET.SubElement(case, "error", attrib={"message": result.detail[:MAX_MESSAGE]})
        error.text = _with_repro(result, result.detail)
        cases.append(case)

    elif result.outcome is Outcome.SKIPPED:
        case = _testcase(classname, "gate-skipped")
        ET.SubElement(case, "skipped", attrib={"message": result.detail[:MAX_MESSAGE]})
        cases.append(case)

    elif not result.findings:
        cases.append(_testcase(classname, "no-problems-found"))

    else:
        # One case per (file, rule). Grouping this way means a reviewer reading
        # the test tab sees which file and which rule, without opening the log.
        grouped: dict[tuple[str, str, str], list[str]] = {}
        for finding in result.findings:
            key = (finding.path, finding.code, finding.severity)
            grouped.setdefault(key, []).append(str(finding))

        for (path, code, severity), messages in sorted(grouped.items()):
            case = _testcase(
                classname if severity == "error" else f"{classname}.warning",
                f"{path}::{code}",
            )
            body = "\n".join(messages)
            if severity == "error":
                failure = ET.SubElement(
                    case, "failure", attrib={"message": messages[0][:MAX_MESSAGE]}
                )
                failure.text = _with_repro(result, body)
            else:
                # Warnings pass. Attaching them as system-out keeps them
                # readable without turning the build red.
                ET.SubElement(case, "system-out").text = body
            cases.append(case)

    failures = sum(1 for c in cases if c.find("failure") is not None)
    errors = sum(1 for c in cases if c.find("error") is not None)
    skipped = sum(1 for c in cases if c.find("skipped") is not None)

    suite = ET.Element(
        "testsuite",
        attrib={
            "name": f"{prefix} - {result.name.capitalize()}",
            "tests": str(len(cases)),
            "failures": str(failures),
            "errors": str(errors),
            "skipped": str(skipped),
            "time": f"{result.duration_s:.3f}",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        },
    )
    for case in cases:
        suite.append(case)
    return suite


def _with_repro(result: GateResult, body: str) -> str:
    """Append the exact command, so the reader can reproduce it locally.

    A CI failure a developer cannot reproduce becomes a guessing game. The
    command is already known here, so printing it costs nothing.
    """
    if not result.command:
        return body
    return f"{body}\n\nReproduce locally:\n  {' '.join(result.command)}"


def write_gate(result: GateResult, path: Path, prefix: str) -> Path:
    """Write one gate's results to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("testsuites")
    root.append(_suite_for(result, prefix))
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def write_report(report: RunReport, directory: Path, prefix: str) -> list[Path]:
    """Write one file per gate into *directory*.

    Separate files rather than one combined document, because CI publish tasks
    are usually configured with a glob and a per-file run title. One file per
    gate lets the results appear as separate, named runs.
    """
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for result in report.results:
        written.append(write_gate(result, directory / f"junit-{result.name}.xml", prefix))
    return written
