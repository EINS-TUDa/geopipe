"""Optimization backends for geopipe.

Architecture overview::

    EnergySystem + Scenario
        │
        ▼  OptimizationBackend.solve()            ← solver.py (ABC)
    Solution(results=Results)

    CESM backend:  geopipe.optimization.cesm.CESMOptimizationBackend

Public API
----------
- :class:`OptimizationBackend`  — abstract base class all backends must implement
- :class:`Solution`             — returned by ``solve()``
- :class:`Results`              — standardized results (DataFrames + costs)
- :class:`CESMOptimizationBackend`   — CESM solver backend
"""
from geopipe.optimization.solver import OptimizationBackend, Solution, Results
from geopipe.optimization.cesm import CESMOptimizationBackend

__all__ = [
    "OptimizationBackend",
    "Solution",
    "Results",
    "CESMOptimizationBackend",
]
