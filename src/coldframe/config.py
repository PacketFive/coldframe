"""Configuration, read from ``pyproject.toml`` and nowhere else.

The design constraint is deliberate. Build systems in the Buck or Bazel mould
ask a project to maintain a parallel description of itself: a ``BUILD`` file per
package listing sources and dependencies that the language ecosystem already
knows about. That second description then drifts from the first.

coldframe keeps one description. Dependencies live where pip and Poetry
already look, ``[project]`` or ``[tool.poetry]``. Gate settings live in
``[tool.coldframe]``. Each tool keeps its own ``[tool.ruff]``,
``[tool.pyright]`` and ``[tool.pytest.ini_options]`` table, untouched, so every
tool stays usable directly. Running ``ruff check src`` by hand must behave
exactly as it does under ``coldframe check``, or the wrapper has started
lying.

What ``[tool.coldframe]`` adds is only the part the tools cannot know: which
gates make up this project's definition of done.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise SystemExit(
            "coldframe needs TOML parsing. On Python 3.10 install tomli, or run on 3.11+."
        ) from exc


DEFAULT_GATES = ("format", "lint", "typecheck", "test")
"""Gates enabled when a project says nothing.

``audit`` is not in the default set. It reaches the network to fetch an advisory
database, which makes it unsuitable for a gate that must work on a laptop with
no connection. Projects opt in.
"""


class ConfigError(RuntimeError):
    """Raised when pyproject.toml is present but says something contradictory."""


@dataclass
class Config:
    """Resolved settings for one project."""

    root: Path
    """Directory holding pyproject.toml. All relative paths resolve against it."""

    source_dirs: list[str] = field(default_factory=lambda: ["src"])
    test_dirs: list[str] = field(default_factory=lambda: ["tests"])
    gates: list[str] = field(default_factory=lambda: list(DEFAULT_GATES))
    coverage_min: float | None = None
    coverage_package: str = ""
    """Package passed to ``--cov``. Empty means pytest config already sets it."""

    python_version: str = ""
    line_length: int | None = None
    project_name: str = ""
    suite_prefix: str = "Codebase Quality and Compliance"
    """Prefix for JUnit suite names, which is what CI groups results by."""

    gate_options: dict[str, dict[str, Any]] = field(default_factory=dict)

    def existing_paths(self, *, include_tests: bool = True) -> list[str]:
        """Source and test directories that actually exist.

        Passing a missing directory to ruff or pyright makes it exit non-zero
        with a usage error, which the runner would then correctly report as
        ERRORED. Filtering here keeps a project that has no tests yet from
        looking broken.
        """
        names = list(self.source_dirs) + (list(self.test_dirs) if include_tests else [])
        return [n for n in names if (self.root / n).exists()]

    def options_for(self, gate: str) -> dict[str, Any]:
        return self.gate_options.get(gate, {})


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk upwards from *start* looking for pyproject.toml.

    Mirrors how git, ruff and pytest find their own roots, so ``coldframe
    check`` behaves the same from a subdirectory as from the top.
    """
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return None


def _as_str_list(value: Any, key: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise ConfigError(f"[tool.coldframe] {key} must be a string or a list of strings")


def load(root: Path | None = None) -> Config:
    """Read configuration for the project rooted at *root*.

    A project with no ``[tool.coldframe]`` table gets working defaults. That
    matters for adoption: pointing the tool at an existing repository should
    produce a useful answer before anyone edits anything.
    """
    resolved = root or find_project_root()
    if resolved is None:
        raise ConfigError(
            "No pyproject.toml found here or in any parent directory. "
            "coldframe works on a Python project; run 'coldframe new' to create one."
        )

    pyproject = resolved / "pyproject.toml"
    if not pyproject.is_file():
        # An explicit root that holds no project is a different mistake from a
        # search that ran out of parents, and the message should say which.
        raise ConfigError(
            f"No pyproject.toml in {resolved}. "
            "coldframe works on a Python project; run 'coldframe new' to create one."
        )
    try:
        with pyproject.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"Cannot read {pyproject}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{pyproject} is not valid TOML: {exc}") from exc

    cfg = Config(root=resolved)
    cfg.project_name = _project_name(data)
    _apply_inferred(cfg, data)
    _apply_explicit(cfg, data)
    return cfg


def _project_name(data: dict[str, Any]) -> str:
    project = data.get("project")
    if isinstance(project, dict) and isinstance(project.get("name"), str):
        return project["name"]
    poetry = data.get("tool", {}).get("poetry")
    if isinstance(poetry, dict) and isinstance(poetry.get("name"), str):
        return poetry["name"]
    return ""


def _version_from_ruff_target(target: str) -> str:
    """Turn a ruff ``target-version`` into a dotted version.

    Ruff writes ``py311``; pyright, tox and every human write ``3.11``.
    Stripping only the prefix yields ``311``, which is silently wrong wherever
    the value is passed on, so the minor digits are split off explicitly.
    """
    digits = target.removeprefix("py")
    if not digits.isdigit() or len(digits) < 2:
        return target
    return f"{digits[0]}.{digits[1:]}"


def _apply_inferred(cfg: Config, data: dict[str, Any]) -> None:
    """Take what the standard tool tables already state.

    Reading ruff's ``line-length`` rather than asking for it again is the whole
    point: one fact, one place.
    """
    tools = data.get("tool", {})
    if not isinstance(tools, dict):
        return

    ruff = tools.get("ruff", {})
    if isinstance(ruff, dict):
        if isinstance(ruff.get("line-length"), int):
            cfg.line_length = ruff["line-length"]
        if isinstance(ruff.get("target-version"), str):
            cfg.python_version = _version_from_ruff_target(ruff["target-version"])

    pyright = tools.get("pyright", {})
    if isinstance(pyright, dict) and isinstance(pyright.get("pythonVersion"), str):
        # pyright already states the version in the form everything else wants,
        # so where both are present this one is taken as the more direct answer.
        cfg.python_version = pyright["pythonVersion"]

    pyright = tools.get("pyright", {})
    if isinstance(pyright, dict) and isinstance(pyright.get("include"), list):
        include = [p for p in pyright["include"] if isinstance(p, str)]
        if include:
            sources = [p for p in include if not p.startswith("test")]
            tests = [p for p in include if p.startswith("test")]
            if sources:
                cfg.source_dirs = sources
            if tests:
                cfg.test_dirs = tests

    pytest_cfg = tools.get("pytest", {})
    if isinstance(pytest_cfg, dict):
        ini = pytest_cfg.get("ini_options", {})
        if isinstance(ini, dict) and isinstance(ini.get("testpaths"), list):
            paths = [p for p in ini["testpaths"] if isinstance(p, str)]
            if paths:
                cfg.test_dirs = paths


def _apply_explicit(cfg: Config, data: dict[str, Any]) -> None:
    """Apply ``[tool.coldframe]``, which wins over anything inferred."""
    table = data.get("tool", {}).get("coldframe")
    if table is None:
        return
    if not isinstance(table, dict):
        raise ConfigError("[tool.coldframe] must be a table")

    if "source" in table:
        cfg.source_dirs = _as_str_list(table["source"], "source")
    if "tests" in table:
        cfg.test_dirs = _as_str_list(table["tests"], "tests")
    if "gates" in table:
        cfg.gates = _as_str_list(table["gates"], "gates")
    if "suite-prefix" in table:
        prefix = table["suite-prefix"]
        if not isinstance(prefix, str):
            raise ConfigError("[tool.coldframe] suite-prefix must be a string")
        cfg.suite_prefix = prefix
    if "coverage-min" in table:
        value = table["coverage-min"]
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise ConfigError("[tool.coldframe] coverage-min must be a number")
        if not 0 <= float(value) <= 100:
            raise ConfigError("[tool.coldframe] coverage-min must be between 0 and 100")
        cfg.coverage_min = float(value)
    if "coverage-package" in table:
        package = table["coverage-package"]
        if not isinstance(package, str):
            raise ConfigError("[tool.coldframe] coverage-package must be a string")
        cfg.coverage_package = package

    for key, value in table.items():
        if isinstance(value, dict):
            cfg.gate_options[key] = value
