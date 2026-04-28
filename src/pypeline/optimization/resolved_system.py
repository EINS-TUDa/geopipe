"""Backend-agnostic intermediate representation of EnergySystem + Scenario.

``ResolvedSystem`` contains all domain data that an optimization backend needs,
pre-computed for a specific set of scenario years.  It is built once per
``solve()`` call and passed to every backend's input-writer, keeping
backend-specific code free of domain-derivation logic.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from pypeline.energy_system_my.scenario import Scenario
from pypeline.energy_system_my.imports import Import
from pypeline.energy_system_my.region import Demand
from pypeline.energy_system_my.energy_system import EnergySystem
from pypeline.energy_system_my.technology import PipeTechnology, DecentralTechnology, GridTechnology, \
    CentralTechnology, CHPTechnology


@dataclass
class ResolvedSystem:
    name: str
    scenario: Scenario
    imports: list[Import]
    pipe_connections: list[PipeTechnology]
    decentralized_technologies: dict[int, tuple[DecentralTechnology, ...]]  # region id → decentral technologies
    grid_technologies: dict[int, tuple[GridTechnology, ...]]  # region id → grid technologies
    central_technologies: dict[int, tuple[CentralTechnology, ...]] # region id → central technologies
    chp_technologies: dict[int, tuple[CHPTechnology, ...]] # region id → chp
    demands: dict[int, tuple[Demand]] # region id → demand

def resolve_system(energy_system: EnergySystem, scenario: Scenario) -> ResolvedSystem:
    """Build a ``ResolvedSystem`` from an ``EnergySystem`` and a ``Scenario``."""
    decentralized_technologies: dict[int, tuple[DecentralTechnology, ...]] = {}
    grid_technologies: dict[int, tuple[GridTechnology, ...]] = {}
    central_technologies: dict[int, tuple[CentralTechnology, ...]] = {}
    chp_technologies: dict[int, tuple[CHPTechnology, ...]] = {}
    demands: dict[int, tuple[Demand]] = defaultdict(tuple)

    for region in energy_system.regions:
        decentralized_technologies[region.id] = region.decentral_techs
        grid_technologies[region.id] = region.grids
        central_technologies[region.id] = region.central_techs
        chp_technologies[region.id] = region.chps
        for demand in region.demands:
            demands[region.id] += (demand,)

    return ResolvedSystem(
        name=energy_system.name,
        scenario = scenario,
        imports=energy_system.imports,
        pipe_connections=energy_system.pipes,
        decentralized_technologies=decentralized_technologies,
        grid_technologies=grid_technologies,
        central_technologies=central_technologies,
        chp_technologies=chp_technologies,
        demands = demands,
    )

