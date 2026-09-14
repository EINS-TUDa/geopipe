"""Optimization backends for geopipe.

Architecture overview::

    EnergySystem + Scenario
        │
        ▼  OptimizationBackend.solve()            ← solver.py (ABC)
    Solution(results=Results)

    CESM backend:  geopipe.optimization.cesm.CESMOptimizationBackend

Backends are optional and imported lazily, so geopipe can be installed
without any of them. Each backend has a matching extra, e.g.::

    pip install "geopipe[cesm]"

Public API
----------
- :class:`OptimizationBackend`  — abstract base class all backends must implement
- :class:`Solution`             — returned by ``solve()``
- :class:`Results`              — standardized results (DataFrames + costs)
- :class:`CESMOptimizationBackend`   — CESM solver backend
"""
import importlib

from geopipe.optimization.solver import OptimizationBackend, Solution, Results

# Backend name -> (module providing it, extra that installs its dependencies,
# extra install steps that pip cannot perform on its own).
_BACKENDS: dict[str, tuple[str, str, str]] = {
    "CESMOptimizationBackend": (
        "geopipe.optimization.cesm",
        "cesm",
        'pip install "git+https://github.com/EINS-TUDa/CESM.git@578e0bb"',
    ),
}


def __getattr__(name: str):
    """Import backends on first access (PEP 562).

    Keeps ``import geopipe.optimization`` working when a backend's
    dependencies are absent, and turns the resulting ModuleNotFoundError
    into a message that says how to fix it.
    """
    try:
        module_name, extra, manual_step = _BACKENDS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None

    try:
        return getattr(importlib.import_module(module_name), name)
    except ImportError as exc:
        hint = f'    pip install "geopipe[{extra}]"'
        if manual_step:
            hint += f"\n    {manual_step}"
        raise ImportError(
            f"{name} requires the '{extra}' backend, which is not installed.\n"
            f"{hint}\n"
            f"(original error: {exc})"
        ) from exc


__all__ = [
    "OptimizationBackend",
    "Solution",
    "Results",
    "CESMOptimizationBackend",
]