from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pypeline.topology_builder.abstract_topology_builder import (
        AbstractTopologyBuilder,
        TopologyBuildError,
        TopologyBuildResult,
    )
    from pypeline.topology_builder.dijkstra_builder import (
        DijkstraTopologyBuilder,
        DijkstraTopologyBuilderConfig,
    )
    from pypeline.topology_builder.topology_injection import (
        SimpleTopologyBuilder,
        load_scenario_yaml,
    )

__all__ = [
    "AbstractTopologyBuilder",
    "TopologyBuildError",
    "TopologyBuildResult",
    "DijkstraTopologyBuilder",
    "DijkstraTopologyBuilderConfig",
    "SimpleTopologyBuilder",
    "load_scenario_yaml",
]


def __getattr__(name: str) -> Any:
    if name in {"AbstractTopologyBuilder", "TopologyBuildError", "TopologyBuildResult"}:
        module = import_module("pypeline.topology_builder.abstract_topology_builder")
        return getattr(module, name)
    if name in {"DijkstraTopologyBuilder", "DijkstraTopologyBuilderConfig"}:
        module = import_module("pypeline.topology_builder.dijkstra_builder")
        return getattr(module, name)
    if name in {"SimpleTopologyBuilder", "load_scenario_yaml"}:
        module = import_module("pypeline.topology_builder.topology_injection")
        return getattr(module, name)
    raise AttributeError(f"module 'pypeline.topology_builder' has no attribute '{name}'")
