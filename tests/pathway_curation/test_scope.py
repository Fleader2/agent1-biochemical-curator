"""Structural scope-safety tests for ``app.pathway_curation`` (Increment C, Step 46).

Mirrors ``tests/agent1/test_scope.py``'s AST-based approach exactly: every
check here inspects actual import statements and actual function/class
definitions via ``ast`` -- never a naive substring search over prose, so a
docstring that merely *discusses* a forbidden concept (to disclose that
this package does not implement it) never fails these tests.

Two properties are specific to this package, beyond what
``tests/agent1/test_scope.py`` already guarantees for all of ``app/``
(and therefore already covers ``app/pathway_curation/`` too):

1. This planner/executor never calls ``app.review.workflow
   .human_review_claim`` -- Step 35's "never auto-accept" guarantee. Only
   a real human, through the existing review API, may ever move a claim to
   ``HUMAN_ACCEPTED``.
2. This package contains no Antimony/SBML/simulation code of its own
   (redundant with the repository-wide check, kept here too so this
   package's own test suite is self-contained and does not depend on
   ``tests/agent1`` existing).
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PATHWAY_CURATION_ROOT = PROJECT_ROOT / "app" / "pathway_curation"

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

#: Never called from this package -- only a real human, through the existing
#: review API, may move a claim to HUMAN_ACCEPTED (Step 35).
_FORBIDDEN_CALLED_NAMES = frozenset({"human_review_claim"})


def _iter_pathway_curation_python_files():
    yield from PATHWAY_CURATION_ROOT.rglob("*.py")


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


def _imported_names(tree: ast.Module) -> set[str]:
    """Every name this module imports directly (``from x import name``), unqualified."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _defined_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
    return names


def _called_names(tree: ast.Module) -> set[str]:
    """Every bare/attribute call target's final identifier, e.g. both ``f()`` and
    ``module.f()`` register as ``"f"``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_pathway_curation_package_exists_and_is_non_empty():
    """Sanity check for this test module's own assumptions -- if this package were ever
    renamed/moved, every other check here should fail loudly, not silently pass over an
    empty glob."""
    assert PATHWAY_CURATION_ROOT.is_dir()
    assert list(_iter_pathway_curation_python_files())


def test_no_forbidden_library_imported_anywhere_in_pathway_curation():
    offenders: dict[str, set[str]] = {}
    for path in _iter_pathway_curation_python_files():
        roots = _imported_roots(_parse(path)) & _FORBIDDEN_IMPORT_ROOTS
        if roots:
            offenders[str(path.relative_to(PROJECT_ROOT))] = roots
    assert offenders == {}, f"forbidden modeling/simulation library imports found: {offenders}"


def test_no_forbidden_definitions_anywhere_in_pathway_curation():
    offenders: dict[str, set[str]] = {}
    for path in _iter_pathway_curation_python_files():
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


def test_pathway_curation_never_imports_or_calls_human_review_claim():
    """Step 35: this planner/executor never auto-accepts a claim on a human's behalf."""
    offenders: dict[str, set[str]] = {}
    for path in _iter_pathway_curation_python_files():
        tree = _parse(path)
        hits = (_imported_names(tree) | _called_names(tree)) & _FORBIDDEN_CALLED_NAMES
        if hits:
            offenders[str(path.relative_to(PROJECT_ROOT))] = hits
    assert offenders == {}, f"forbidden human-review-bypassing call(s) found: {offenders}"


def test_pathway_curation_never_imports_agent2_through_5_packages():
    """This repository is Agent 1 only -- ``app.agent2``/``.agent3``/``.agent4``/``.agent5``
    do not exist here (verified: no such packages are present in ``app/``), and this
    package must never grow an import of one if they are ever vendored in alongside it."""
    forbidden_modules = frozenset({"app.agent2", "app.agent3", "app.agent4", "app.agent5"})
    offenders: dict[str, set[str]] = {}
    for path in _iter_pathway_curation_python_files():
        tree = _parse(path)
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        hits = {
            module
            for module in modules
            if any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in forbidden_modules
            )
        }
        if hits:
            offenders[str(path.relative_to(PROJECT_ROOT))] = hits
    assert offenders == {}, f"forbidden Agent 2-5 import(s) found: {offenders}"


def test_pathway_curation_types_carry_no_model_fields():
    """The core contract types (``PathwayCurationRequest``/``...Plan``/``...Result``, ...)
    carry no Antimony/SBML/ODE/kinetic-model/simulation-result/model-validation field."""
    tree = _parse(PATHWAY_CURATION_ROOT / "types.py")
    field_annotations = [
        node.target.id.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]
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
