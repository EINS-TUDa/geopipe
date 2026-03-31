"""Optimization backends for pypeline.

Architecture overview::

    EnergySystem + Scenario
        │
        ▼  build_optimization_context()          ← optimization_context.py
    OptimizationContext   (backend-agnostic intermediate representation)
        │
        ▼  OptimizationBackend.solve()            ← solver.py (ABC)
    Solution(results=Results)

    CESM backend:  pypeline.optimization.cesm.CESMOptimizationBackend
    Future:        pypeline.optimization.pypsa.PyPSAOptimizationBackend (example)

Public API
----------
- :class:`OptimizationBackend`  — abstract base class all backends must implement
- :class:`Solution`             — returned by ``solve()``
- :class:`Results`              — standardized results (DataFrames + costs)
- :class:`OptimizationContext`  — intermediate context passed to backends
- :func:`build_optimization_context` — builds context from EnergySystem + Scenario
- :class:`CESMOptimizationBackend`   — CESM solver backend
"""
from pypeline.optimization.solver import OptimizationBackend, Solution, Results
from pypeline.optimization.optimization_context import (
    OptimizationContext,
    build_optimization_context,
)
from pypeline.optimization.cesm import CESMOptimizationBackend

__all__ = [
    "OptimizationBackend",
    "Solution",
    "Results",
    "OptimizationContext",
    "build_optimization_context",
    "CESMOptimizationBackend",
]
