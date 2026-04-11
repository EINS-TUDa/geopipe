from __future__ import annotations

import ast
from dataclasses import dataclass
import importlib
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ExampleEntry:
    name: str
    module_path: str
    description: str
    relative_path: str
    is_test_case: bool


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


def discover_examples() -> list[ExampleEntry]:
    root = Path(__file__).resolve().parent
    entries: list[ExampleEntry] = []

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
        entries.append(
            ExampleEntry(
                name=relative_path,
                module_path=_module_path_from_file(root, py_file),
                description="Test case" if is_test_case else "Example",
                relative_path=relative_path,
                is_test_case=is_test_case,
            )
        )

    return entries


def get_example_by_name(name: str) -> ExampleEntry:
    entries = discover_examples()
    exact = [entry for entry in entries if entry.name == name or entry.relative_path == name]
    if len(exact) == 1:
        return exact[0]

    by_stem = [entry for entry in entries if entry.relative_path.split("/")[-1] == name]
    if len(by_stem) == 1:
        return by_stem[0]

    if len(by_stem) > 1:
        options = [entry.name for entry in by_stem]
        raise ValueError(f"Ambiguous name '{name}'. Candidates: {options}")

    raise ValueError(f"Unknown example '{name}'. Available: {[e.name for e in entries]}")


def _resolve_main(module_path: str) -> Callable[[], None]:
    module = importlib.import_module(module_path)
    main_fn = getattr(module, "main", None)
    if not callable(main_fn):
        raise ValueError(f"Module '{module_path}' does not expose a callable main()")
    return main_fn


def run_example(entry: ExampleEntry) -> Any:
    main_fn = _resolve_main(entry.module_path)
    return main_fn()
