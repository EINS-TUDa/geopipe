from __future__ import annotations
import ast
from dataclasses import dataclass
import importlib
import inspect
from pathlib import Path
from typing import Any, Callable
import yaml


@dataclass(frozen=True)
class ScenarioEntry:
    name: str
    module_path: str
    description: str
    relative_path: str
    is_test_case: bool
    yaml_file: Path | None
    has_injections: bool


def _is_runnable_python_file(path: Path) -> bool:
    if path.name.startswith("_"):
        return False
    if path.suffix != ".py":
        return False
    if path.name == "__init__.py":
        return False
    return True


def _has_main_function(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return False

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return True
    return False


def _module_path_from_file(root: Path, file_path: Path) -> str:
    rel = file_path.relative_to(root).with_suffix("")
    return "examples." + ".".join(rel.parts)


def _candidate_yaml_file(py_file: Path) -> Path | None:
    yaml_candidate = py_file.with_suffix(".yaml")
    if yaml_candidate.exists():
        return yaml_candidate

    yml_candidate = py_file.with_suffix(".yml")
    if yml_candidate.exists():
        return yml_candidate

    return None


def _yaml_has_injections(yaml_file: Path | None) -> bool:
    if yaml_file is None:
        return False

    try:
        loaded = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
    except Exception:
        return False

    if not isinstance(loaded, dict):
        return False

    injections = loaded.get("injections")
    return isinstance(injections, list) and len(injections) > 0


def discover_scenarios() -> list[ScenarioEntry]:
    root = Path(__file__).resolve().parent
    entries: list[ScenarioEntry] = []

    for py_file in sorted(root.rglob("*.py")):
        if not _is_runnable_python_file(py_file):
            continue

        rel = py_file.relative_to(root)
        if len(rel.parts) < 2:
            continue
        if not _has_main_function(py_file):
            continue

        rel_no_suffix = rel.with_suffix("")
        relative_path = "/".join(rel_no_suffix.parts)
        is_test_case = rel_no_suffix.parts[0] == "test_cases"
        yaml_file = _candidate_yaml_file(py_file)
        entries.append(
            ScenarioEntry(
                name=relative_path,
                module_path=_module_path_from_file(root, py_file),
                description="Test case" if is_test_case else "Example",
                relative_path=relative_path,
                is_test_case=is_test_case,
                yaml_file=yaml_file,
                has_injections=_yaml_has_injections(yaml_file),
            )
        )

    return entries


def get_scenario_by_name(name: str) -> ScenarioEntry:
    entries = discover_scenarios()
    exact = [entry for entry in entries if entry.name == name or entry.relative_path == name]
    if len(exact) == 1:
        return exact[0]

    by_stem = [entry for entry in entries if entry.relative_path.split("/")[-1] == name]
    if len(by_stem) == 1:
        return by_stem[0]

    if len(by_stem) > 1:
        options = [entry.name for entry in by_stem]
        raise ValueError(f"Ambiguous name '{name}'. Candidates: {options}")

    raise ValueError(f"Unknown scenario '{name}'. Available: {[e.name for e in entries]}")


def _resolve_main(module_path: str) -> Callable[..., Any]:
    module = importlib.import_module(module_path)
    main_fn = getattr(module, "main", None)
    if not callable(main_fn):
        raise ValueError(f"Module '{module_path}' does not expose a callable main()")
    return main_fn


def _supports_parameter(main_fn: Callable[..., Any], parameter_name: str) -> bool:
    try:
        signature = inspect.signature(main_fn)
    except (TypeError, ValueError):
        return False

    parameter = signature.parameters.get(parameter_name)
    if parameter is None:
        return False

    return parameter.kind in {
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    }


def run_scenario(
    entry: ScenarioEntry,
    *,
    apply_injections: bool | None = None,
) -> Any:
    main_fn = _resolve_main(entry.module_path)

    if apply_injections is not None and _supports_parameter(main_fn, "apply_injections"):
        return main_fn(apply_injections=apply_injections)

    return main_fn()
