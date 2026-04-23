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
- :class:`CESMOptimizationBackend`   — CESM solver backend
"""
from pypeline.optimization.solver import OptimizationBackend, Solution, Results
from pypeline.optimization.cesm import CESMOptimizationBackend

__all__ = [
    "OptimizationBackend",
    "Solution",
    "Results",
    "CESMOptimizationBackend",
]
