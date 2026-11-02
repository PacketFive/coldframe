"""Project scaffolding.

Templates are plain files carrying ``{{ name }}`` placeholders, rendered by
straight string substitution. There is no template engine and no dependency on
one, which keeps coldframe installable anywhere without dragging a tree of
packages into the environment it is meant to be bootstrapping.

The generated project is an ordinary Python project. It has a ``pyproject.toml``
that pip and Poetry both understand, a ``src`` layout, a test directory and tool
configuration in the standard tables. Someone who has never heard of coldframe
can clone it and run ``ruff check src`` or ``pytest`` and get exactly what they
expect. The tool adds a gate definition, not a parallel universe.
"""

from __future__ import annotations

import keyword
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

TEMPLATE_ROOT = Path(__file__).parent / "templates"

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# CI definitions live outside the project template so exactly one is written.
CI_SYSTEMS = ("azure", "github")

# Names Python cannot import, plus names that shadow the standard library badly
# enough to cause confusing failures much later.
_RESERVED = {
    "abc",
    "argparse",
    "ast",
    "asyncio",
    "collections",
    "copy",
    "csv",
    "dataclasses",
    "datetime",
    "email",
    "enum",
    "functools",
    "hashlib",
    "http",
    "importlib",
    "io",
    "json",
    "logging",
    "math",
    "os",
    "pathlib",
    "platform",
    "queue",
    "random",
    "re",
    "select",
    "shutil",
    "signal",
    "socket",
    "sqlite3",
    "ssl",
    "string",
    "struct",
    "subprocess",
    "sys",
    "tempfile",
    "test",
    "threading",
    "time",
    "types",
    "typing",
    "unittest",
    "uuid",
    "warnings",
    "xml",
    "zipfile",
}


class ScaffoldError(RuntimeError):
    """Raised when a project cannot be generated."""


@dataclass
class ProjectSpec:
    """Everything the templates need."""

    name: str
    package: str = ""
    description: str = "A Python project."
    author: str = ""
    email: str = ""
    python_version: str = "3.10"
    line_length: int = 100
    coverage_min: int = 80
    gates: list[str] = field(default_factory=lambda: ["format", "lint", "typecheck", "test"])
    year: int = field(default_factory=lambda: date.today().year)

    def __post_init__(self) -> None:
        self.name = normalise_project_name(self.name)
        self.package = self.package or package_name_for(self.name)
        validate_package_name(self.package)

    def as_substitutions(self) -> dict[str, str]:
        return {
            "name": self.name,
            "package": self.package,
            "description": self.description,
            "author": self.author or "Your Name",
            "email": self.email or "you@example.com",
            "python_version": self.python_version,
            "python_target": "py" + self.python_version.replace(".", ""),
            "line_length": str(self.line_length),
            "coverage_min": str(self.coverage_min),
            "gates": ", ".join(f'"{g}"' for g in self.gates),
            "year": str(self.year),
            "title": self.name.replace("-", " ").replace("_", " ").title(),
        }


def normalise_project_name(raw: str) -> str:
    """Reduce *raw* to a distribution-style name.

    Follows the same shape PyPI expects, lower case with hyphens, so the
    generated ``pyproject.toml`` is publishable without a rename later.
    """
    text = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-._").lower()
    text = re.sub(r"[-_.]+", "-", text)
    if not text:
        raise ScaffoldError(f"{raw!r} does not contain any usable characters for a project name")
    if not text[0].isalpha():
        raise ScaffoldError(f"Project name {text!r} must start with a letter")
    return text


def package_name_for(project_name: str) -> str:
    return project_name.replace("-", "_").replace(".", "_")


def validate_package_name(package: str) -> None:
    if not package.isidentifier():
        raise ScaffoldError(
            f"{package!r} is not a valid Python identifier, so it cannot be imported"
        )
    # isidentifier() accepts keywords, so 'class' and 'import' pass it while
    # being impossible to import. Catching them here turns a syntax error in
    # generated code into a message before anything is written.
    if keyword.iskeyword(package):
        raise ScaffoldError(
            f"{package!r} is a reserved word in Python, so no module may be given that name"
        )
    if package in _RESERVED:
        raise ScaffoldError(
            f"{package!r} collides with a standard library module. "
            "Importing it would shadow the real one and break in ways that are hard to trace."
        )


def render(text: str, substitutions: dict[str, str]) -> str:
    """Replace ``{{ key }}`` with its value.

    An unknown placeholder is left untouched rather than raising, so template
    files can contain literal braces, for example inside a shell or CI
    expression, without needing an escape syntax.

    This matters more than it sounds. Azure Pipelines writes compile-time
    expressions as ``${{ parameters.vmImage }}``, which shares the outer
    delimiters. The pattern requires ``\\w+`` between them, so a dotted
    expression cannot match and survives into the generated file intact. Any
    single-word ADO expression would need renaming here, which is why the CI
    templates use dotted parameter references throughout.
    """

    def replace(match: re.Match[str]) -> str:
        return substitutions.get(match.group(1), match.group(0))

    return _PLACEHOLDER.sub(replace, text)


def _target_name(name: str, substitutions: dict[str, str]) -> str:
    # Template files are stored with a .tmpl suffix so an editor does not try to
    # parse a half-written pyproject.toml, and directories can carry
    # placeholders in their names.
    rendered = render(name, substitutions)
    return rendered[: -len(".tmpl")] if rendered.endswith(".tmpl") else rendered


def _copy_tree(source: Path, destination: Path, substitutions: dict[str, str]) -> list[Path]:
    written: list[Path] = []
    for item in sorted(source.rglob("*")):
        relative = item.relative_to(source)
        rendered_parts = [_target_name(part, substitutions) for part in relative.parts]
        target = destination.joinpath(*rendered_parts)

        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            text = item.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # pragma: no cover - no binary templates today
            target.write_bytes(item.read_bytes())
        else:
            target.write_text(render(text, substitutions), encoding="utf-8")
        if target.suffix == ".sh":
            target.chmod(0o755)
        written.append(target)
    return written


def generate(
    spec: ProjectSpec,
    destination: Path,
    *,
    force: bool = False,
    ci: str | None = "azure",
) -> list[Path]:
    """Write the project described by *spec* into *destination*.

    Refuses to write into a non-empty directory unless *force*. A scaffolder
    that overwrites by default eventually overwrites something that mattered.

    *ci* selects one pipeline definition, or ``None`` for a project that will
    supply its own. Writing every supported CI system into every project leaves
    most of them as dead files that nobody removes and nobody trusts.
    """
    source = TEMPLATE_ROOT / "project"
    if not source.is_dir():  # pragma: no cover - packaging error
        raise ScaffoldError(f"Template directory is missing: {source}")

    if ci is not None and ci not in CI_SYSTEMS:
        raise ScaffoldError(
            f"Unknown CI system {ci!r}. Choose one of: {', '.join(sorted(CI_SYSTEMS))}"
        )

    if destination.exists() and any(destination.iterdir()) and not force:
        raise ScaffoldError(
            f"{destination} already exists and is not empty. "
            "Pass --force to write into it anyway."
        )

    substitutions = spec.as_substitutions()
    written = _copy_tree(source, destination, substitutions)

    if ci is not None:
        written.extend(_copy_tree(TEMPLATE_ROOT / "ci" / ci, destination, substitutions))

    return written
