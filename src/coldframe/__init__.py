"""coldframe: project scaffolding and quality gates that run the same way
locally and in CI.

Two ideas hold the tool together.

The first is that a project should describe itself once. Dependencies belong in
``pyproject.toml`` where pip already looks, tool settings belong in each tool's
own table, and coldframe adds only the list of gates that make up "done".
There is no second description of the build to drift out of step with the first.

The second is that "the check failed" and "the check never ran" are different
answers, and a tool that conflates them will eventually report success while
inspecting nothing.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
