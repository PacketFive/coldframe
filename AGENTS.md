# coldframe

Orientation for anyone, human or AI, picking this repository up. Read this
before changing anything; most of what follows was learned by getting it wrong
once.

## What this is

A scaffolding tool and a set of quality gates, generalised out of the Image
Factory CQC pipeline. Two things distinguish it from the alternatives:

- The gates run identically on a terminal and on a build agent. There is one
  code path, not a local convenience wrapper and a separate CI script that drift
  apart.
- A check that found problems and a check that never ran are different results.
  Most CI setups collapse them, and the second is the one worth acting on.

It is a dependency of the SigRaft book at `/opt/repo/sigraft`, where thirty labs
are checked with it. Breaking a gate here breaks thirty projects there, so run
`make labs` in that repository after changing gate behaviour.

## State, as of the last session

Complete and self-checking. 120 tests pass, the four configured gates are green
on the tool itself, and `coldframe new` produces a project that passes its own
gates immediately with no edits.

Nothing has been committed. The repository has one empty history and every file
is untracked. A commit message draft exists in the session state directory; the
policy below explains why it was not used.

| Area | State |
|---|---|
| `check`, `fix`, `new`, `info`, `gates` | working |
| format, lint, typecheck, test gates | working, enabled by default |
| audit gate | working, opt in, needs network |
| Azure Pipelines template | written and exercised |
| GitHub Actions template | written, never run on GitHub |
| `docs/` | empty directory, nothing written yet |
| Packaging | installs from source; never published anywhere |

## Verifying a change

```bash
pip install -e ".[dev]"
coldframe check          # the acceptance test
```

The tool checks itself with its own gates. That is deliberate and it is the only
acceptance test that matters: a quality tool that cannot pass its own bar has
nothing to say. Expect exit 0 and four gates reported: format, lint, typecheck
and test. The audit gate exists and is opt in, because it needs the network to
fetch an advisory database and a gate that only works when the network is up
fails on a laptop with no connection. `coldframe gates` lists all five.

The individual tools still work on their own, which is the point of the design:

```bash
ruff format --check src tests
ruff check src tests
pyright
pytest
```

If you changed anything in `scaffold.py` or under `templates/`, the real check is
that a generated project is green without being touched:

```bash
cd /tmp && coldframe new demo-project --no-git --ci none
cd demo-project && coldframe check
```

That path has failed before, in exactly the way that is hardest to notice: the
tool's own gates were green while every project it produced was broken.

## Layout

```
src/coldframe/
  model.py          Outcome, Finding, GateResult, RunReport, exit-code policy
  config.py         reads pyproject.toml, infers settings from the tools' own tables
  runner.py         runs the gates, never stops early
  junit.py          JUnit XML, <failure> against <error>
  report.py         terminal output, text and GitHub annotation formats
  scaffold.py       project generation
  cli.py            argparse entry point, five subcommands
  gates/
    base.py         subprocess handling, tool discovery, the ERRORED decision
    format_gate.py  ruff format --check, plain text parsing
    lint_gate.py    ruff check --output-format=json
    typecheck_gate.py  pyright
    test_gate.py    pytest with coverage, JUnit passthrough
    audit_gate.py   dependency vulnerabilities
  templates/
    project/        the scaffolded project, {{ placeholder }} substitution
    ci/azure/       Azure Pipelines definition
    ci/github/      GitHub Actions workflow
tests/              120 tests across five modules
```

The most important file to read first is `model.py`. The three-outcome model in
it is the whole design, and everything else follows from it.

## Design rules

- **Three outcomes, not two.** `PASSED`, `FAILED`, `ERRORED`. A tool that did
  not run has judged nothing, and recording that as either a pass or a failure
  loses the only fact worth acting on. Exit 2 outranks exit 1 for the same
  reason: not knowing is worse than knowing you are wrong.
- **No second description of the build.** Everything lives in `pyproject.toml`.
  Adding a `BUILD` file, a lockfile of our own, or a `Makefile` that duplicates
  the gate list creates two sources of truth that drift. This was an explicit
  requirement, not a preference, and it rules out the Buck2 and Bazel shape.
- **Infer rather than require.** Line length comes from `[tool.ruff]`, coverage
  settings from the pytest table. A project that already configured its tools
  should not have to say the same thing again under `[tool.coldframe]`.
- **Standard library only.** argparse rather than typer, string substitution
  rather than Jinja2. The tool installs into environments that have nothing else
  yet, so a bootstrapper with its own dependency tree argues against its premise.
  `tomli` on Python 3.10 is the single exception, because `tomllib` is not there
  yet.
- **Never `|| true`.** In CI use `continueOnError: true` and gate on
  `PublishTestResults@2` with `failTaskOnFailedTests`, so a broken gate arrives
  as `<error>` and still fails the build. A bare `|| true` discards the outcome
  entirely, which is the bug the Image Factory pipeline carries.
- **Parse the stable format.** `ruff check` supports `--output-format=json` and
  that is used. `ruff format` does not, so its plain text output is parsed
  instead.

## Traps found the hard way

- `ruff format --output-format=json` exits 2 having read nothing. The flag
  belongs to `ruff check` only. This is the bug the project was built around and
  it is why the ERRORED outcome exists.
- Ruff anchors `S603` and `S607` to the **first argument** of a `subprocess`
  call, not to the call line. A `# noqa` on the `subprocess.run(` line has no
  effect; it has to sit on the argument beneath it.
- Azure Pipelines writes `${{ parameters.x }}`, which shares the outer braces
  with the scaffold's `{{ name }}` placeholders. They stay distinct only because
  the placeholder pattern requires `\w+`, so a dotted expression cannot match.
  Any single-word ADO expression in a template would be eaten.
- A src layout needs `pythonpath = ["src"]` under `[tool.pytest.ini_options]`
  and `src = ["src", "tests"]` under `[tool.ruff]`. Without the first, a fresh
  clone fails to import; without the second, the import sorter treats the
  project's own package as third-party and reports a sorting error on a file
  nobody has touched. Both were missing from the templates at one point and the
  only thing that caught it was a test that scaffolded a project and checked it.
- `str.isidentifier()` returns True for `class` and `import`, so keyword
  rejection needs `keyword.iskeyword` as well.
- pytest exit 5 means no tests were collected. It is treated as `ERRORED`,
  because an empty suite reporting green is how a project loses its tests
  without anyone noticing.
- `pip` in this environment defaults to a private ADO feed with no public
  packages. Installing anything public needs
  `--index-url https://pypi.org/simple`.

## If you are continuing the work

Nothing is outstanding, so the following are opportunities rather than a backlog.
In rough order of value:

1. `docs/` is an empty directory. The README covers usage; a written explanation
   of the three-outcome model and the CI wiring would be the first page.
2. The GitHub Actions template has never run on GitHub. It is written by
   analogy with the Azure one and should be assumed untested.
3. There is no `--fix` coverage for the typecheck or test gates, which is
   correct, but `coldframe fix` does not say so clearly when asked for them.
4. Gates are discovered from a fixed table in `gates/__init__.py`. A plugin
   entry point would let a project add one without changing this repository.
5. Nothing is published. If it is ever to be installed by name rather than by
   path, the wheel needs building and a home.

Before any of that, run the SigRaft labs. That repository is the only real
consumer and it is where a regression will show first.

## Conventions

- Prose in code comments, docs and commit messages: no em dashes or double
  hyphens, no `word: sentence` headings, no filler summary or conclusion
  sections. Avoid: delve, robust, pivotal, foster, multifaceted, nuanced,
  integral, tapestry, plethora, underpins, strive, realm, seamless, leverage,
  crucial, vital, furthermore, moreover, conversely, in conclusion.
- Comments explain why, not what. A comment restating the line beneath it is
  noise that ages badly. The comments in `pyproject.toml` are the intended
  standard.
- Commit message bodies wrap at 73 columns.

## Do not commit

Stage changes or leave them unstaged, print a commit message draft, and wait for
a human to review it. Do not run `git commit`, `git push`, `git tag`,
`git rebase` or `git reset --hard`.

Do not add `Signed-off-by:` or `Co-authored-by:` for an AI. The attribution
trailer is:

```
Assisted-by: Copilot-CLI:Claude [ruff] [pytest]
Reviewed-by: Weqaar Janjua <weqaar.janjua@packetfive.com>
# Human submitter: add your Signed-off-by: trailer before committing.
```

Model family name only, no version. The bracketed tools are specialised analysis
tools that were actually run, not basic ones like git.
