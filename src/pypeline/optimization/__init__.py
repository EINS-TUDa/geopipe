"""Optimization backends for pypeline.

Architecture overview::

    EnergySystem + Scenario
        │
        ▼  OptimizationBackend.solve()            ← solver.py (ABC)
    Solution(results=Results)

    CESM backend:  pypeline.optimization.cesm.CESMOptimizationBackend

Public API
----------
- :class:`OptimizationBackend`  — abstract base class all backends must implement
- :class:`Solution`             — returned by ``solve()``
- :class:`Results`              — standardized results (DataFrames + costs)
- :class:`ResolvedSystem`       — resolved backend input bundle
- :func:`resolve_system`        — resolves EnergySystem + Scenario for backends
- :class:`CESMOptimizationBackend`   — CESM solver backend
"""
from pypeline.optimization.solver import OptimizationBackend, Solution, Results
from pypeline.optimization.cesm import CESMOptimizationBackend
from pypeline.optimization.resolved_system import ResolvedSystem, resolve_system

__all__ = [
    "OptimizationBackend",
    "Solution",
    "Results",
    "ResolvedSystem",
    "resolve_system",
    "CESMOptimizationBackend",
]
