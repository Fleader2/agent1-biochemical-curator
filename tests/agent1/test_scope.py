"""Structural scope-safety tests (Increment 27).

Verifies, by inspecting actual import statements and actual function/class
definitions via ``ast`` -- never by naive substring search over prose --
that no part of ``app/`` implements Antimony/SBML generation, Tellurium/
COPASI execution, ODE construction, model simulation, parameter scans,
sensitivity analysis, mass-balance/conservation-law validation, or model
critique. Increment 27 instructions, Step 20: "Be careful not to fail
merely because these words appear in documentation explaining non-goals."
Every module's own docstring in this repository is free to *discuss* these
concepts (many already do, precisely to disclose that Agent 1 does not
implement them) -- only a real import or a real definition would fail
these tests.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = PROJECT_ROOT / "app"

_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "tellurium",
        "roadrunner",
        "libroadrunner",
        "libsbml",
        "antimony",
        "COPASI",
        "basico",
        "cobra",
    }
)

# Substrings checked against actual `def`/`class` names only (never prose),
# case-insensitive, with underscores stripped so `run_simulation` and
# `RunSimulation` are both caught.
_FORBIDDEN_DEFINITION_SUBSTRINGS = (
    "antimony",
    "sbml",
    "tellurium",
    "copasi",
    "roadrunner",
    "simulate",
    "simulation",
    "odemodel",
    "kineticmodel",
    "parameterscan",
    "sensitivityanalysis",
    "massbalance",
    "conservationlaw",
    "modelcritic",
    "thermodynamiccritique",
)


def _iter_app_python_files():
    yield from APP_ROOT.rglob("*.py")


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _defined_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
    return names


def test_no_forbidden_library_imported_anywhere_in_app():
    offenders: dict[str, set[str]] = {}
    for path in _iter_app_python_files():
        roots = _imported_roots(_parse(path)) & _FORBIDDEN_IMPORT_ROOTS
        if roots:
            offenders[str(path.relative_to(PROJECT_ROOT))] = roots
    assert offenders == {}, f"forbidden modeling/simulation library imports found: {offenders}"


def test_no_forbidden_definitions_anywhere_in_app():
    offenders: dict[str, set[str]] = {}
    for path in _iter_app_python_files():
        defined = _defined_names(_parse(path))
        hits = {
            name
            for name in defined
            if any(
                substring in name.lower().replace("_", "")
                for substring in _FORBIDDEN_DEFINITION_SUBSTRINGS
            )
        }
        if hits:
            offenders[str(path.relative_to(PROJECT_ROOT))] = hits
    assert offenders == {}, f"forbidden modeling/simulation definitions found: {offenders}"


def test_pyproject_declares_no_modeling_or_simulation_dependency():
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    dependencies = data.get("project", {}).get("dependencies", [])
    all_dependency_text = " ".join(dependencies).lower()
    for forbidden in ("tellurium", "libsbml", "antimony", "copasi", "basico", "roadrunner"):
        assert forbidden not in all_dependency_text


def test_agent1_package_contains_no_model_types():
    """The Agent 1 output types themselves carry no Antimony/SBML/ODE/kinetic-model/
    simulation-result/model-validation field of any kind."""
    from app.agent1 import types as agent1_types

    tree = _parse(Path(agent1_types.__file__))
    field_annotations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            field_annotations.append(node.target.id.lower())
    forbidden_field_substrings = (
        "antimony",
        "sbml",
        "odemodel",
        "odesystem",
        "kineticmodel",
        "simulationresult",
        "modelvalidation",
    )
    for field_name in field_annotations:
        normalized = field_name.replace("_", "")
        for forbidden in forbidden_field_substrings:
            assert forbidden not in normalized, f"{field_name!r} looks model-related"
