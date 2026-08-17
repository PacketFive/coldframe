# pybootstrap

Project scaffolding and quality gates that run the same way on a laptop and on a
build agent.

```bash
pip install -e ".[dev]"

pybootstrap new my-service    # scaffold a project
pybootstrap check             # run every gate
pybootstrap fix               # apply what can be applied automatically
```

## The problem it solves

A quality pipeline usually starts like this:

```yaml
- script: ruff format --check --output-format=json src || true
```

Two things are wrong with that line, and they compound.

`ruff format` does not accept `--output-format`; the flag belongs to
`ruff check`. Ruff exits 2 with a usage error having read no files. Then
`|| true` converts that exit code into success. The step is green, the badge is
green, and no file has been looked at. In the codebase this tool was extracted
from, that line had been passing for the life of the pipeline while 107 of 273
files would have been reformatted.

The `|| true` is not carelessness. It is there because the pipeline gates on a
later step that publishes test results, so the check must not abort the job. The
mistake is that the mechanism cannot tell "the tool found nothing wrong" from
"the tool never ran".

pybootstrap keeps those two answers apart everywhere.

## Three outcomes

| Outcome | Meaning | Exit |
|---|---|---|
| `PASSED` | The tool ran and found nothing | 0 |
| `FAILED` | The tool ran and found problems | 1 |
| `ERRORED` | The tool did not run, so nothing was judged | 2 |
| `SKIPPED` | Not applicable yet, for example no tests exist | 0 |

Exit 2 outranks exit 1. If a run reports both, the failures may be an artefact
of the broken gate, so the exit code points at the cause rather than the
symptom.

JUnit XML already draws the same distinction, and pybootstrap uses it: `FAILED`
becomes `<failure>`, `ERRORED` becomes `<error>`. Both render red in Azure
Pipelines and GitHub Actions, so a gate that could not run stays visible instead
of disappearing.

## Configuration

One file, the one that is already there.

```toml
[tool.pybootstrap]
gates = ["format", "lint", "typecheck", "test"]
coverage-min = 80
```

That is the whole of it. There is no `BUILD` file, no lockfile of its own, and
no second description of the project to drift out of step with the first.
Dependencies stay in `[project]` where pip and Poetry already look, and each
tool keeps its own table untouched, so `ruff check src` typed by hand behaves
exactly as it does under `pybootstrap check`.

Settings already stated elsewhere are read rather than asked for again:

| Read from | Used for |
|---|---|
| `[tool.ruff] line-length`, `target-version` | reported by `info`, written into scaffolds |
| `[tool.pyright] include`, `pythonVersion` | which paths to check |
| `[tool.pytest.ini_options] testpaths` | where the tests are |

## Gates

| Name | Tool | Passes when |
|---|---|---|
| `format` | `ruff format --check` | every file matches the formatter |
| `lint` | `ruff check` | no rule violations |
| `typecheck` | `pyright` | no static type errors |
| `test` | `pytest` | the suite passes and coverage clears the floor |
| `audit` | `pip-audit` | no known vulnerabilities |

`audit` is off by default because it needs network access. A pipeline that fails
when the package index is briefly unreachable teaches people to ignore it, so a
connection failure is reported as `ERRORED` rather than as a vulnerability.

## In CI

`pybootstrap check --junit-dir .quality` writes one `junit-<gate>.xml` per gate.
Publish them and gate on the publish step:

```yaml
- script: pybootstrap check --junit-dir .quality
  continueOnError: true

- task: PublishTestResults@2
  inputs:
    testResultsFiles: ".quality/junit-*.xml"
    failTaskOnFailedTests: true
```

`continueOnError` does what `|| true` was doing, but the information is no
longer thrown away. A gate that could not run arrives as `<error>` and fails the
publish step exactly as a real failure does.

`--format github` emits `::error file=...,line=...` annotations instead, so
findings appear inline on a pull request.

`pybootstrap new --ci azure` or `--ci github` writes a working pipeline.

## Commands

| Command | Does |
|---|---|
| `pybootstrap check` | run the configured gates |
| `pybootstrap check --gate lint` | run one gate |
| `pybootstrap fix` | `ruff format` and `ruff check --fix` |
| `pybootstrap new NAME` | scaffold a project |
| `pybootstrap info` | show what would run and where the settings came from |
| `pybootstrap gates` | list every available gate |

## Requirements

Python 3.10 or newer. pybootstrap has no runtime dependencies beyond `tomli` on
3.10, where `tomllib` is not yet in the standard library. A tool that needed its
own dependency tree resolved before it could bootstrap a project would be
arguing against its own premise.

The gates need the tools they drive. `pip install "pybootstrap[gates]"` brings
in ruff, pyright and pytest, or a project can pin its own versions and
pybootstrap will use those.

## A note on the name

The name `pybootstrap` is taken on PyPI by a placeholder uploaded in 2017, at
version 0.1, with the summary "A random test lib" and no description. The import
name and the console script used here are unaffected. Publishing under this name
would need a PEP 541 name-reuse request; `pybootstrapper` and `pybstrap` are
both free if that request is refused.
