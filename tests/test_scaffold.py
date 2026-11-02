"""Tests for project generation.

A scaffold is trusted precisely because nobody reads its output closely, so the
checks here are about the failures a reader would not notice: a name that is not
importable, a template placeholder left unrendered, an existing directory
quietly overwritten.
"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

from coldframe.scaffold import ProjectSpec, ScaffoldError, generate


class TestProjectSpec:
    @pytest.mark.parametrize(
        "given, name, package",
        [
            ("my-service", "my-service", "my_service"),
            ("My Service", "my-service", "my_service"),
            ("my_service", "my-service", "my_service"),
            ("MyService", "myservice", "myservice"),
            ("data.pipeline", "data-pipeline", "data_pipeline"),
            ("  spaced  name  ", "spaced-name", "spaced_name"),
        ],
    )
    def test_names_are_normalised_both_ways(self, given, name, package) -> None:
        """PyPI prefers hyphens, Python requires underscores.

        Deriving both from one input keeps the two from drifting apart, which is
        otherwise a rename waiting to happen the first time someone publishes.
        """
        spec = ProjectSpec(given)
        assert spec.name == name
        assert spec.package == package

    def test_a_leading_digit_is_refused(self) -> None:
        """``import 3d_tools`` is a syntax error.

        Silently rewriting the name would give the reader a project that is not
        the one they asked for, so the choice is handed back to them.
        """
        with pytest.raises(ScaffoldError, match="must start with a letter"):
            ProjectSpec("3d-tools")

    @pytest.mark.parametrize("name", ["class", "import", "lambda", "return"])
    def test_python_keywords_are_rejected(self, name: str) -> None:
        with pytest.raises(ScaffoldError, match="reserved word"):
            ProjectSpec(name)

    @pytest.mark.parametrize("name", ["json", "types", "logging"])
    def test_standard_library_names_are_rejected(self, name: str) -> None:
        """Shadowing a stdlib module produces import failures far from the
        cause, and the cost of the mistake only appears much later."""
        with pytest.raises(ScaffoldError, match="standard library"):
            ProjectSpec(name)

    @pytest.mark.parametrize("name", ["", "   ", "---", "!!!"])
    def test_empty_and_punctuation_only_names_are_rejected(self, name: str) -> None:
        with pytest.raises(ScaffoldError):
            ProjectSpec(name)


class TestGenerate:
    def test_the_expected_files_are_written(self, tmp_path) -> None:
        generate(ProjectSpec("demo-app"), tmp_path / "demo-app")
        root = tmp_path / "demo-app"
        for relative in [
            "pyproject.toml",
            "README.md",
            "AGENTS.md",
            ".gitignore",
            "src/demo_app/__init__.py",
            "tests/test_demo_app.py",
        ]:
            assert (root / relative).is_file(), relative

    def test_no_placeholder_survives_rendering(self, tmp_path) -> None:
        """One unrendered placeholder makes a file invalid rather than untidy,
        and a scaffolded project that will not parse is worse than none.

        ``${{ ... }}`` is excluded because Azure Pipelines uses the same outer
        braces for its own compile-time expressions. Those must reach the
        generated file untouched, so the two syntaxes are told apart by the
        leading dollar rather than by the braces.
        """
        root = tmp_path / "demo-app"
        generate(ProjectSpec("demo-app"), root)
        leftover = re.compile(r"(?<!\$)\{\{")
        for path in root.rglob("*"):
            if path.is_file():
                assert not leftover.search(path.read_text(encoding="utf-8")), path

    def test_azure_expressions_are_not_eaten_by_the_renderer(self, tmp_path) -> None:
        """The collision is easy to reintroduce, so it is pinned here."""
        root = tmp_path / "demo-app"
        generate(ProjectSpec("demo-app"), root, ci="azure")
        pipeline = (root / "azure-pipelines.yml").read_text(encoding="utf-8")
        assert "${{ parameters." in pipeline

    def test_template_suffixes_are_stripped(self, tmp_path) -> None:
        root = tmp_path / "demo-app"
        generate(ProjectSpec("demo-app"), root)
        assert not list(root.rglob("*.tmpl"))

    def test_placeholders_in_paths_are_substituted(self, tmp_path) -> None:
        root = tmp_path / "demo-app"
        generate(ProjectSpec("demo-app"), root)
        assert (root / "src" / "demo_app").is_dir()
        assert not (root / "src" / "{{ package }}").exists()

    def test_the_generated_project_imports_and_its_tests_pass(self, tmp_path) -> None:
        """The scaffold's real contract.

        Everything else here checks the shape of the output. This checks that
        the output works, which is the only claim a reader actually relies on.
        """
        root = tmp_path / "demo-app"
        generate(ProjectSpec("demo-app"), root)

        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header"],
            cwd=root,
            capture_output=True,
            text=True,
            env={"PYTHONPATH": str(root / "src"), "PATH": "/usr/bin:/bin"},
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_the_generated_pyproject_is_valid_toml(self, tmp_path) -> None:
        from coldframe.config import tomllib

        root = tmp_path / "demo-app"
        generate(ProjectSpec("demo-app"), root)
        with (root / "pyproject.toml").open("rb") as handle:
            data = tomllib.load(handle)

        assert data["project"]["name"] == "demo-app"
        assert "coldframe" in data["tool"]

    def test_an_existing_directory_is_not_overwritten(self, tmp_path) -> None:
        root = tmp_path / "demo-app"
        root.mkdir()
        (root / "keep.py").write_text("value = 1", encoding="utf-8")

        with pytest.raises(ScaffoldError, match="not empty"):
            generate(ProjectSpec("demo-app"), root)
        assert (root / "keep.py").read_text(encoding="utf-8") == "value = 1"

    def test_an_empty_directory_is_accepted(self, tmp_path) -> None:
        root = tmp_path / "demo-app"
        root.mkdir()
        generate(ProjectSpec("demo-app"), root)
        assert (root / "pyproject.toml").is_file()

    def test_force_replaces_existing_content(self, tmp_path) -> None:
        root = tmp_path / "demo-app"
        root.mkdir()
        (root / "pyproject.toml").write_text("stale", encoding="utf-8")
        generate(ProjectSpec("demo-app"), root, force=True)
        assert "demo-app" in (root / "pyproject.toml").read_text(encoding="utf-8")

    def test_only_the_requested_ci_definition_is_written(self, tmp_path) -> None:
        """A project that ships pipelines for systems it does not use collects
        files nobody maintains and nobody trusts."""
        plain = tmp_path / "plain"
        generate(ProjectSpec("demo-app"), plain, ci=None)
        assert not (plain / "azure-pipelines.yml").exists()
        assert not (plain / ".github").exists()

        ado = tmp_path / "ado"
        generate(ProjectSpec("demo-app"), ado, ci="azure")
        assert (ado / "azure-pipelines.yml").is_file()

        assert not (ado / ".github").exists()

        gha = tmp_path / "gha"
        generate(ProjectSpec("demo-app"), gha, ci="github")
        assert (gha / ".github" / "workflows" / "quality.yml").is_file()
        assert not (gha / "azure-pipelines.yml").exists()

    def test_an_unknown_ci_system_is_rejected_before_writing(self, tmp_path) -> None:
        root = tmp_path / "demo-app"
        with pytest.raises(ScaffoldError, match="Unknown CI system"):
            generate(ProjectSpec("demo-app"), root, ci="jenkins")
        assert not root.exists()
