"""Command line interface.

Built on argparse rather than a CLI framework. coldframe installs into the
environment of every project that uses it, including the moment before that
project has any dependencies at all, so the tool needs to work from the standard
library alone. A bootstrapper that first requires its own dependency tree is
arguing against itself.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from coldframe import __version__, gates, junit, report, runner
from coldframe.config import Config, ConfigError, load
from coldframe.model import GateResult, Outcome
from coldframe.scaffold import CI_SYSTEMS, ProjectSpec, ScaffoldError, generate

PROGRAM = "coldframe"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=(
            "Scaffold Python projects and run their quality gates. "
            "The same gates run locally and in CI."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes:\n"
            "  0  every gate passed\n"
            "  1  a gate ran and found problems\n"
            "  2  a gate could not run, so nothing was checked\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    check = sub.add_parser("check", help="run the quality gates")
    check.add_argument(
        "--gate",
        action="append",
        dest="only",
        metavar="NAME",
        help="run only this gate; repeatable. Defaults to the pyproject.toml list.",
    )
    check.add_argument(
        "--junit-dir",
        type=Path,
        metavar="DIR",
        help="write one JUnit XML file per gate into DIR, for CI to publish",
    )
    check.add_argument(
        "--format",
        choices=("text", "github"),
        default="text",
        help="text for humans, github for workflow annotations",
    )
    check.add_argument("-v", "--verbose", action="store_true", help="include warnings")
    check.add_argument(
        "-C",
        "--directory",
        type=Path,
        default=None,
        metavar="DIR",
        help="project root; defaults to the nearest pyproject.toml",
    )
    check.set_defaults(func=cmd_check)

    fix = sub.add_parser("fix", help="apply the corrections tools can make automatically")
    fix.add_argument("-C", "--directory", type=Path, default=None, metavar="DIR")
    fix.set_defaults(func=cmd_fix)

    new = sub.add_parser("new", help="scaffold a project wired to the same gates")
    new.add_argument("name", help="project name, for example 'lab-01-sockets'")
    new.add_argument("--path", type=Path, default=None, help="where to write it")
    new.add_argument("--package", default="", help="import name; derived from the project name")
    new.add_argument("--description", default="A Python project.")
    new.add_argument("--author", default="")
    new.add_argument("--email", default="")
    new.add_argument("--python", dest="python_version", default="3.10")
    new.add_argument("--line-length", type=int, default=100)
    new.add_argument("--coverage-min", type=int, default=80)
    new.add_argument(
        "--gate",
        action="append",
        dest="gate_list",
        metavar="NAME",
        help="gate to enable; repeatable. Defaults to format, lint, typecheck, test.",
    )
    new.add_argument("--force", action="store_true", help="write into a non-empty directory")
    new.add_argument(
        "--ci",
        choices=[*CI_SYSTEMS, "none"],
        default="azure",
        help="which pipeline definition to write (default: azure)",
    )
    new.add_argument("--no-git", action="store_true", help="skip git init")
    new.set_defaults(func=cmd_new)

    info = sub.add_parser("info", help="show the resolved configuration")
    info.add_argument("-C", "--directory", type=Path, default=None, metavar="DIR")
    info.set_defaults(func=cmd_info)

    sub.add_parser("gates", help="list the available gates").set_defaults(func=cmd_gates)

    return parser


def _load_config(directory: Path | None) -> Config:
    return load(directory.resolve() if directory else None)


def cmd_check(args: argparse.Namespace) -> int:
    config = _load_config(args.directory)
    use_github = args.format == "github"
    # A carriage return only rewinds; it does not erase. Redirected into a file
    # or a CI log it leaves the progress line sitting in front of the result,
    # so the in-place update is offered only to a terminal that can undo it.
    live = not use_github and sys.stdout.isatty()

    def on_start(name: str) -> None:
        if live:
            print(f"[ ... ] {name}", end="\r", flush=True)

    def on_finish(result: GateResult) -> None:
        if use_github:
            _emit_github(result)
        else:
            if live:
                print("\r\033[2K", end="")
            report.print_result(result, verbose=args.verbose)

    print(f"{PROGRAM} {__version__}, project {config.project_name or config.root.name}")
    result = runner.run(config, only=args.only, on_start=on_start, on_finish=on_finish)

    if not use_github:
        report.print_summary(result)

    if args.junit_dir:
        written = junit.write_report(result, args.junit_dir.resolve(), config.suite_prefix)
        print(f"\nWrote {len(written)} JUnit files to {args.junit_dir}")

    return result.exit_code()


def _emit_github(result: GateResult) -> None:
    """Emit GitHub Actions workflow commands.

    Annotations land on the offending line in the diff, which is where a
    reviewer is already looking.
    """
    print(f"::group::{result.summary()}")
    if result.outcome is Outcome.ERRORED:
        print(f"::error title={result.name} did not run::{result.detail}")
    for finding in result.findings:
        level = "error" if finding.severity == "error" else "warning"
        location = f"file={finding.path}"
        if finding.line is not None:
            location += f",line={finding.line}"
        if finding.column is not None:
            location += f",col={finding.column}"
        message = finding.message.replace("\n", " ")
        print(f"::{level} {location},title={finding.code}::{message}")
    print("::endgroup::")


def cmd_fix(args: argparse.Namespace) -> int:
    """Run the tools that can correct their own findings.

    Only formatting and the lint rules ruff marks as safely fixable. Type errors
    and failing tests are not mechanically fixable, and a command that pretended
    otherwise would do damage.
    """
    config = _load_config(args.directory)
    paths = config.existing_paths()
    if not paths:
        print("Nothing to fix: no source or test directories exist yet.")
        return 0

    if shutil.which("ruff") is None:
        print("ruff is not installed. Install it with: pip install ruff", file=sys.stderr)
        return 2

    commands = [
        ["ruff", "check", "--fix", *paths],
        ["ruff", "format", *paths],
    ]
    worst = 0
    for argv in commands:
        print(f"$ {' '.join(argv)}")
        completed = subprocess.run(argv, cwd=config.root, check=False)  # noqa: S603
        # ruff exits 1 when unfixable findings remain, which is information
        # rather than a failure of the fix command itself.
        if completed.returncode > 1:
            worst = 2
    print("\nFixed what could be fixed automatically. Run 'coldframe check' to see the rest.")
    return worst


def cmd_new(args: argparse.Namespace) -> int:
    spec = ProjectSpec(
        name=args.name,
        package=args.package,
        description=args.description,
        author=args.author or _git_config("user.name"),
        email=args.email or _git_config("user.email"),
        python_version=args.python_version,
        line_length=args.line_length,
        coverage_min=args.coverage_min,
        gates=args.gate_list or ["format", "lint", "typecheck", "test"],
    )
    destination = (args.path or Path.cwd() / spec.name).resolve()
    written = generate(
        spec,
        destination,
        force=args.force,
        ci=None if args.ci == "none" else args.ci,
    )

    print(f"Created {spec.name} in {destination}")
    for path in written:
        print(f"  {path.relative_to(destination)}")

    if not args.no_git and shutil.which("git") and not (destination / ".git").exists():
        # A fixed argument list, no shell. See gates/base.py for why the
        # suppression sits on the argument rather than on the call.
        subprocess.run(
            ["git", "init", "--quiet"],  # noqa: S603, S607
            cwd=destination,
            check=False,
        )
        print("  initialised a git repository")

    print(f'\nNext:\n  cd {destination}\n  pip install -e ".[dev]"\n  coldframe check')
    return 0


def _git_config(key: str) -> str:
    if shutil.which("git") is None:
        return ""
    completed = subprocess.run(  # noqa: S603, S607
        ["git", "config", "--get", key],  # noqa: S603, S607
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip()


def cmd_info(args: argparse.Namespace) -> int:
    config = _load_config(args.directory)
    rows = [
        ("project", config.project_name or "(unnamed)"),
        ("root", str(config.root)),
        ("sources", ", ".join(config.source_dirs)),
        ("tests", ", ".join(config.test_dirs)),
        ("gates", ", ".join(config.gates)),
        ("coverage-min", "not set" if config.coverage_min is None else f"{config.coverage_min:g}%"),
        ("line-length", str(config.line_length) if config.line_length else "tool default"),
        ("suite-prefix", config.suite_prefix),
    ]
    width = max(len(k) for k, _ in rows)
    for key, value in rows:
        print(f"{key:<{width}}  {value}")

    missing = [d for d in config.source_dirs + config.test_dirs if not (config.root / d).exists()]
    if missing:
        print(f"\nConfigured but missing: {', '.join(missing)}")
    return 0


def cmd_gates(_: argparse.Namespace) -> int:
    width = max(len(name) for name, _ in gates.available())
    for name, description in gates.available():
        print(f"{name:<{width}}  {description}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        # Exit 2, the shell convention for a usage error. Returning 0 would let
        # a mistyped CI step, 'coldframe' with the subcommand dropped, report
        # success having checked nothing, which is the failure this tool exists
        # to make impossible.
        return 2

    try:
        return int(args.func(args))
    except (ConfigError, ScaffoldError) as exc:
        print(f"{PROGRAM}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
