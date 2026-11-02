"""Runs the configured gates and collects their results.

The same code path serves a developer's terminal and a build agent. That is the
point: if CI runs something the developer cannot run, the first time anyone sees
a CI-only failure they have to debug it through the pipeline, and the feedback
loop stretches from seconds to minutes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from coldframe import gates as registry
from coldframe.config import Config
from coldframe.model import GateResult, Outcome, RunReport

ProgressHook = Callable[[str], None]


def resolve(names: Iterable[str]) -> tuple[list[str], list[str]]:
    """Split requested gate names into known and unknown."""
    known: list[str] = []
    unknown: list[str] = []
    for name in names:
        (known if name in registry.REGISTRY else unknown).append(name)
    return known, unknown


def run(
    config: Config,
    *,
    only: Iterable[str] | None = None,
    on_start: ProgressHook | None = None,
    on_finish: Callable[[GateResult], None] | None = None,
) -> RunReport:
    """Run every configured gate and return their results.

    Gates always all run. Stopping at the first failure would save seconds and
    cost a round trip, because a developer who fixes the formatting only then
    discovers the type errors underneath.
    """
    requested = list(only) if only is not None else list(config.gates)
    known, unknown = resolve(requested)

    report = RunReport()
    for name in unknown:
        result = GateResult(
            name=name,
            outcome=Outcome.ERRORED,
            detail=(
                f"unknown gate '{name}'. Available: " f"{', '.join(sorted(registry.REGISTRY))}"
            ),
        )
        report.results.append(result)
        if on_finish:
            on_finish(result)

    for name in known:
        gate = registry.build(name)
        if gate is None:  # pragma: no cover - resolve() already filtered these
            continue
        if on_start:
            on_start(name)
        try:
            result = gate.run(config)
        except Exception as exc:  # noqa: BLE001 - a broken gate must not hide the others
            result = GateResult(
                name=name,
                outcome=Outcome.ERRORED,
                detail=f"gate raised {type(exc).__name__}: {exc}",
            )
        report.results.append(result)
        if on_finish:
            on_finish(result)

    return report
