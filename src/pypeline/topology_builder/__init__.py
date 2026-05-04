"""Public API facade for topology_builder.

Owns lazy re-exports only.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pypeline.topology_builder.core import (
        AbstractTopologyBuilder,
        TopologyBuildError,
        TopologyBuildResult,
    )
    from pypeline.topology_builder.dijkstra_builder import (
        DijkstraTopologyBuilder,
        DijkstraTopologyBuilderConfig,
    )
    from pypeline.topology_builder.simple_builder import (
        SimpleTopologyBuilder,
        load_yaml,
    )
    from pypeline.topology_builder.region_topology_builder import (
        RegionTopologyConfig,
        RegionTopologyGeometry,
        build_region_topology,
    )

__all__ = [
    "AbstractTopologyBuilder",
    "TopologyBuildError",
    "TopologyBuildResult",
    "DijkstraTopologyBuilder",
    "DijkstraTopologyBuilderConfig",
    "SimpleTopologyBuilder",
    "load_yaml",
    "RegionTopologyConfig",
    "RegionTopologyGeometry",
    "build_region_topology",
]


def __getattr__(name: str) -> Any:
    if name in {"AbstractTopologyBuilder", "TopologyBuildError", "TopologyBuildResult"}:
        module = import_module("pypeline.topology_builder.core")
        return getattr(module, name)
    if name in {"DijkstraTopologyBuilder", "DijkstraTopologyBuilderConfig"}:
        module = import_module("pypeline.topology_builder.dijkstra_builder")
        return getattr(module, name)
    if name in {"SimpleTopologyBuilder", "load_scenario_yaml"}:
        module = import_module("pypeline.topology_builder.simple_builder")
        return getattr(module, name)
    if name in {
        "RegionTopologyConfig",
        "RegionTopologyGeometry",
        "build_region_topology",
    }:
        module = import_module("pypeline.topology_builder.region_topology_builder")
        return getattr(module, name)
    raise AttributeError(f"module 'pypeline.topology_builder' has no attribute '{name}'")
