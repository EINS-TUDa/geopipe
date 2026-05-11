# coding=utf-8
import time
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd
from pathlib import Path
import networkx as nx
from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    YamlConfigSettingsSource,
)

from .imports import Import, load_imports_from_yaml
from .technology import PipeTechnology, GridTechnology, CentralTechnology, DecentralTechnology
from .region import compute_region_connections, RegionConnections
from .demand import DemandType
from ..data.data_registry import DataRegistry
from .region import Demand, Region
from .units import Unit, UnitEnum
from ..topology_builder.topology import Topology
from ..plot.energy_system_plotter import plot_system_topology

import logging

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EnergySystem:
    name: str
    regions: list[Region]
    units: Unit
    system_topology: nx.Graph | None = None
    imports: list[Import] = field(default_factory=list)
    pipes: list[PipeTechnology] = field(default_factory=list)

    def plot_system_topology(self, output_path: Optional[str | Path] = None) -> None:
        plot_system_topology(self, output_path=output_path)


class EnergySystemBuilderConfig(BaseSettings):
    #: A dict that maps the name of a decentral technology to the minimum share (between 0 and 1) of the total demand
    minimum_decentral_technology_share: dict[str, float] = Field(default_factory=dict)
    #: Threshold to consider regions connected. Within a connected group, exactly one region acts as the
    #: source for existing-capacity propagation along the MST.
    considered_connected_region_distance_m: float = np.inf
    #: Per commodity_out, the list of region ids where central technologies of that commodity are built.
    #: A central tech is instantiated in every listed region whose topology satisfies the tech's
    #: ``constrain_location_to_streets`` (if any). If a connected region group contains no listed region,
    #: the largest-grid region in the group is auto-added to this set for that commodity.
    central_tech_locations_per_commodity: dict[str, list[int]] = Field(default_factory=dict)
    #: Per commodity_out, per region id (or the literal ``"default"``), the list of ``(tech_name, share)``
    #: tuples that distribute the source region's total existing output capacity among central techs.
    #: Shares per region key must sum to 1. Region keys must appear in
    #: ``central_tech_locations_per_commodity`` for the same commodity. Lookup for a source region falls
    #: back to ``"default"``; if neither is configured and the source has positive capacity, an error is
    #: raised.
    central_tech_existing_capacities: dict[str, dict[int | str, list[tuple[str, float]]]] = Field(
        default_factory=dict)
    #: Name of the ID property on the edges of the topology Graph
    region_id_name: str = "id"
    #: Name of the street-id property on the edges of the topology Graph; used to evaluate
    #: ``constrain_location_to_streets`` for central technologies.
    street_id_name: str = "street_id"
    #: Factor added to the grid capacity. Dict of grid name to factor. 10 % corresponds to 1.1
    additional_grid_capacity_factor: dict[str, float]

    @classmethod
    def settings_customise_sources(
            cls,
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return YamlConfigSettingsSource(settings_cls), init_settings


class EnergySystemBuilder:

    def __init__(self, energy_system_name: str = "Default", base_crs: str = "EPSG:25832"):
        self._energy_system_name = energy_system_name
        self._base_crs = base_crs
        self._system_topology: Optional[Topology] = None
        self._data_registry: Optional[DataRegistry] = None
        self._unit: Optional[Unit] = None
        self._config: Optional[EnergySystemBuilderConfig] = None
        self._demand_types: list[DemandType] = []
        self._imports: Optional[list[Import]] = None
        self.__region_topologies: Optional[dict[int, Topology]] = None

    @property
    def energy_system_name(self) -> str:
        return self._energy_system_name

    @property
    def base_crs(self) -> str:
        return self._base_crs

    def set_system_topology(self, system_topology: nx.Graph | Topology):
        if isinstance(system_topology, nx.Graph):
            system_topology = Topology(system_topology)
        self._system_topology = system_topology
        return self

    def set_data_registry(self, data_registry: DataRegistry):
        self._data_registry = data_registry
        return self

    def set_unit(self, input_unit: UnitEnum):
        self._unit = input_unit.unit
        return self

    def add_demand_types(self, *demand_types):
        self._demand_types.extend(demand_types)
        return self

    def set_demand_types(self, demand_types: list[DemandType]):
        if not isinstance(demand_types, list) and not all(isinstance(d, DemandType) for d in demand_types):
            raise TypeError("demands must be a list of Demand instances.")
        self._demand_types = demand_types
        return self

    def set_imports(self, import_yaml: str | Path):
        self._imports = load_imports_from_yaml(import_yaml)
        return self

    def set_config(self, config: EnergySystemBuilderConfig):
        if not isinstance(config, EnergySystemBuilderConfig):
            raise TypeError(f"config must be an instance of EnergySystemBuilderConfig and not {type(config)}")
        self._config = config
        return self

    def _region_topologies(self):
        if self.__region_topologies is None:
            self.__region_topologies = self._system_topology.sub_topologies_by_edge_property("id")
        return self.__region_topologies

    def _build_demands(self, topology: Topology) -> list[Demand]:
        collection = []
        for demand_type in self._demand_types:
            # base_query = {"region": topology, "base_crs": self.base_crs}
            profile = pd.read_csv(demand_type.profile_path, sep="\s+", decimal=".", header=None)
            if profile is None or profile.empty:
                raise ValueError(f"Demand profile for {demand_type.name} not found in data registry.")
            profile = pd.Series(profile.values.ravel())

            value = float(np.nansum(topology.property_from_edges(demand_type.demand_column_name)))
            if not isinstance(value, (int, float)):
                raise ValueError(f"Demand value for {demand_type.name} not found or invalid in data registry.")

            # Create a RegionDemand instance
            demand = Demand(demand_type=demand_type, value=value, profile=profile, profile_name=demand_type.name)
            collection.append(demand)
        return collection

    def _process_technology_shares(self, technology_shares_data: dict[str, float], demand: Demand, topology: Topology) \
            -> dict[str, float]:
        # If technology_shares_data is empty
        if np.isclose(sum(technology_shares_data.values()), 0.0):
            default_tech_name = demand.demand_type.default_decentral_supply_technology
            if default_tech_name is None:
                raise ValueError(
                    f"No default technology configured for demand commodity {demand.demand_type.commodity_in}")
            if not DecentralTechnology.has_type(default_tech_name):
                raise ValueError(f"Default technology {default_tech_name} for demand commodity "
                                 f"{demand.demand_type.commodity_in} is not a valid DecentralTechnology")
            return {default_tech_name: 1.0}

        # Apply minimum share thresholds from config
        for name, share in technology_shares_data.items():
            if share < self._config.minimum_decentral_technology_share.get(name, -1):
                region_id = next(iter(
                    data.get("id") for _, _, data in topology.graph.edges(data=True) if data.get("id") is not None),
                                 "unknown")
                logger.info("Share of technology %s for demand %s in region_id %s is below "
                            "the minimum threshold. Skipping.", name, demand.demand_type.name, region_id)

                technology_shares_data[name] = 0

        # Normalize shares to sum to 1 if they don't already
        total_share = sum(technology_shares_data.values())
        if not np.isclose(total_share, 1.0):
            technology_shares_data = {name: share / total_share for name, share in technology_shares_data.items()}

        return technology_shares_data

    def _build_decentral_technologies(self, topology: Topology, demands: list[Demand]) -> list[DecentralTechnology]:
        decentral_technologies = []
        base_query = {"region": topology.graph, "base_crs": self.base_crs}
        for demand in demands:
            technology_shares_data: dict[str, float] = self._data_registry.query(
                demand.demand_type.technology_shares_query_params | base_query)

            technology_shares_data = self._process_technology_shares(technology_shares_data, demand, topology)

            shares_seen = set()
            for name in DecentralTechnology.registered_type_names():
                share = technology_shares_data.get(name, 0.0)
                existing_capacity = share * demand.peak(
                    year_period=0) * 1000  # factor energy (e.g. MWH) to power (e.g. KW)
                decentral_technologies.append(DecentralTechnology(name=name, existing_capacity=existing_capacity,
                                                                  output_profile_name=demand.profile_name))
                shares_seen.add(name)
            for name, share in technology_shares_data.items():
                if share > 0 and name not in shares_seen:
                    raise ValueError(f"Non-zero share {share} for technology '{name}' in demand "
                                     f"{demand.demand_type.name} cannot be assigned to any DecentralTechnology. "
                                     f"Ensure {demand.demand_type.name} contains valid mappings to a registered "
                                     f"DecentralTechnology. ")

        return decentral_technologies

    def _build_grids(self, topology: Topology, decentralized_tech_per_region) -> dict[str, GridTechnology]:
        region_length_km = topology.total_edge_length / 1000.0
        grids = {grid_type_name: GridTechnology(name=grid_type_name, length_km=region_length_km)
                 for grid_type_name in GridTechnology.registered_type_names()}

        for decentralized_technology in decentralized_tech_per_region:
            grid = grids.get(self._find_grid_type_from_decentralized_technology(decentralized_technology, grids))
            if not grid:
                continue
            grid.existing_capacity += decentralized_technology.existing_capacity / decentralized_technology.efficiency

        return grids

    def _build_central_technologies(self, effective_locations: dict[str, set[int]],
                                    source_capacity_per_commodity: dict[tuple[str, int], float]) -> dict[
        int, dict[str, CentralTechnology]]:
        """Instantiate every central tech (matching commodity_out) in every region listed in
        ``effective_locations[commodity]``, skipping regions excluded by the tech's
        ``constrain_location_to_streets``.

        Then distribute each source region's total output capacity over the configured
        (tech, share) tuples to set ``existing_capacity`` on the respective central techs.
        """
        central_techs_per_region: dict[int, dict[str, CentralTechnology]] = {
            region_id: {} for region_id in self._region_topologies().keys()}
        for commodity, region_ids in effective_locations.items():
            for region_id in region_ids:
                for central_tech_name in CentralTechnology.get_type_names_by_attribute("commodity_out", commodity):
                    if not self._can_place_tech_in_region(central_tech_name, region_id):
                        continue
                    central_techs_per_region[region_id][central_tech_name] = CentralTechnology.from_name(
                        central_tech_name)

        # Distribute the source region's total output capacity over the configured (tech, share) tuples.
        for (commodity, source_region), total_output_capacity in source_capacity_per_commodity.items():
            if total_output_capacity <= 0:
                continue
            shares = self._lookup_existing_capacity_shares(commodity, source_region)
            for tech_name, share in shares:
                if not self._can_place_tech_in_region(tech_name, source_region):
                    raise ValueError(
                        f"Tech '{tech_name}' is configured to receive existing capacity in region "
                        f"{source_region} for commodity '{commodity}' but cannot be placed there due to its "
                        f"constrain_location_to_streets.")
                tech = central_techs_per_region[source_region].get(tech_name)
                if tech is None:
                    raise ValueError(
                        f"Tech '{tech_name}' is configured to receive existing capacity in region "
                        f"{source_region} for commodity '{commodity}' but is not instantiated there. "
                        f"Add region {source_region} to central_tech_locations_per_commodity[{commodity!r}].")
                tech.existing_capacity = total_output_capacity * share

        return central_techs_per_region

    def _can_place_tech_in_region(self, tech_name: str, region_id: int) -> bool:
        """Return False if the tech declares ``constrain_location_to_streets`` and the region's topology
        contains none of those street ids. Techs without the constraint are placeable everywhere.
        """
        constrained_streets = CentralTechnology.get_type_defaults(tech_name).get("constrain_location_to_streets") or []
        if not constrained_streets:
            return True
        topology = self._region_topologies()[region_id]
        street_id_name = self._config.street_id_name
        region_streets = {data.get(street_id_name) for _, _, data in topology.graph.edges(data=True)
                          if data.get(street_id_name) is not None}
        return any(s in region_streets for s in constrained_streets)

    def _initial_effective_locations(self) -> dict[str, set[int]]:
        """Initial effective placement set per commodity, taken from config. Auto-extended later in build()
        when a connected region group contains no configured location for its commodity.
        """
        return {commodity: set(regions)
                for commodity, regions in self._config.central_tech_locations_per_commodity.items()}

    def _lookup_existing_capacity_shares(self, commodity: str, region_id: int) -> list[tuple[str, float]]:
        """Return the (tech_name, share) list for the source region. Falls back to the ``"default"`` entry
        if the region has no explicit configuration. Raises if neither is configured.
        """
        by_region = self._config.central_tech_existing_capacities.get(commodity, {})
        if region_id in by_region:
            return by_region[region_id]
        if "default" in by_region:
            return by_region["default"]
        raise ValueError(
            f"No existing-capacity shares configured for commodity '{commodity}' in region {region_id}, "
            f"and no 'default' fallback provided in central_tech_existing_capacities[{commodity!r}].")

    def _pre_build(self):
        if self._config is None:
            logger.info("No config given. Using the default values.")
            self.set_config(EnergySystemBuilderConfig())

        self.verify()

    @staticmethod
    def _find_grid_type_from_pipe_type(pipe_type_name: str) -> str:
        """Find the name of the grid type that uses the same commodities as the pipe type."""
        pipe_tech_commodity_in = PipeTechnology.get_type_defaults(pipe_type_name).get("commodity_in")
        pipe_tech_commodity_out = PipeTechnology.get_type_defaults(pipe_type_name).get("commodity_out")

        for grid_type_name in GridTechnology.registered_type_names():
            type_defaults = GridTechnology.get_type_defaults(grid_type_name)
            commodity_in = type_defaults.get("commodity_in")
            commodity_out = type_defaults.get("commodity_out")
            if not commodity_in or not commodity_out:
                continue
            if commodity_in == pipe_tech_commodity_out and commodity_out == pipe_tech_commodity_in:
                return grid_type_name
        raise ValueError(
            f"No grid type found for pipe type {pipe_type_name} with commodity_in {pipe_tech_commodity_in} "
            f"and commodity_out {pipe_tech_commodity_out}")

    @staticmethod
    def _find_grid_type_from_decentralized_technology(decentralized_technology: DecentralTechnology,
                                                      grids: dict[str, GridTechnology]) -> Optional[str]:
        """Find the name of the grid type that has the given commodity_out."""
        for grid_type_name, grid in grids.items():
            if grid.commodity_out == decentralized_technology.commodity_in:
                return grid_type_name
        return None

    def _determine_source_region_for_group(self, commodity: str, region_group: set[int],
                                           effective_locations: dict[str, set[int]],
                                           grid_tech_per_region: dict[int, dict[str, GridTechnology]],
                                           grid_type_name: str) -> int:
        """Pick the region that holds the existing capacity for ``commodity`` in ``region_group``.

        If at least one region of the group is listed in ``effective_locations[commodity]``, the largest-grid
        candidate among those is chosen. Otherwise the largest-grid region in the group is auto-added to
        ``effective_locations[commodity]`` and returned.
        """
        locations = effective_locations.setdefault(commodity, set())
        candidates = locations & region_group
        if not candidates:
            chosen = max(region_group, key=lambda rid: grid_tech_per_region[rid][grid_type_name].existing_capacity)
            locations.add(chosen)
            logger.info("No region of commodity %s configured in region group %s; auto-adding region %s "
                        "(largest grid capacity).", commodity, sorted(region_group), chosen)
            return chosen
        return max(candidates, key=lambda rid: grid_tech_per_region[rid][grid_type_name].existing_capacity)

    def build(self) -> EnergySystem:
        start = time.perf_counter()
        self._pre_build()

        region_ids = tuple(self._region_topologies().keys())
        demands_per_region: dict[int, list[Demand]] = {}
        decentralized_tech_per_region: dict[int, list[DecentralTechnology]] = {}
        grid_tech_per_region: dict[int, dict[str, GridTechnology]] = {}
        for region_id, topology in self._region_topologies().items():
            demands_per_region[region_id] = self._build_demands(topology)
            decentralized_tech_per_region[region_id] = self._build_decentral_technologies(topology,
                                                                                          demands_per_region[region_id])
            grid_tech_per_region[region_id] = self._build_grids(topology, decentralized_tech_per_region[region_id])

        # All connections between regions
        region_connections: RegionConnections = compute_region_connections(self._region_topologies(),
                                                                           self._system_topology,
                                                                           self._config.region_id_name)

        # Effective placement set per commodity. Initialised from config; auto-extended below when a connected
        # region group contains no configured location for its commodity.
        effective_locations = self._initial_effective_locations()

        # Build pipes for all region connections
        # Derive existing capacities for grids, pipes and central technologies
        pipes_per_type: dict[str, dict[tuple[int, int], PipeTechnology]] = {}
        region_groups_per_grid_type: dict[str, list[set[int]]] = {}
        # Total existing output capacity assigned to each (commodity, source_region).
        source_capacity_per_commodity: dict[tuple[str, int], float] = {}
        for pipe_type_name in PipeTechnology.registered_type_names():
            pipes_per_type[pipe_type_name] = {}
            # build a pipe for each connection for each type
            for(region_1_id, region_2_id), (length_m, connection_graph) in region_connections:
                pipes_per_type[pipe_type_name][(region_1_id, region_2_id)] = PipeTechnology(
                    pipe_type_name, region_id_in=region_1_id,
                    region_id_out=region_2_id, pipe_length_km=length_m/1000.0, topology=connection_graph)
                pipes_per_type[pipe_type_name][(region_2_id, region_1_id)] = PipeTechnology(
                    pipe_type_name, region_id_in=region_2_id,
                    region_id_out=region_1_id, pipe_length_km=length_m / 1000.0, topology=connection_graph)

            # The grid type name that works with the same commodities as the current pipe technology
            grid_type_name = self._find_grid_type_from_pipe_type(pipe_type_name)

            # A graph that contains regions as node and connections as edges.
            # Only those regions are included that have an existing capacity in their grid as the current pipe type
            # Only those connections are included that are below the maximum distance threshold
            region_graph = nx.Graph()
            nodes_in_graph = [region_id for region_id in region_ids
                              if grid_tech_per_region[region_id][grid_type_name].existing_capacity > 0]
            edges_in_graph = [(region_1_id, region_2_id, {"len": length_m}) for (region_1_id, region_2_id), (length_m, topology) in
                              region_connections
                              if length_m <= self._config.considered_connected_region_distance_m
                              if region_1_id in nodes_in_graph and region_2_id in nodes_in_graph]
            region_graph.add_nodes_from(nodes_in_graph)
            region_graph.add_edges_from(edges_in_graph)

            # List with sets of connected regions. Each set contains the region ids of connected regions.
            region_groups_per_grid_type[grid_type_name] = list(nx.connected_components(region_graph))

            # For each region group, plan the pipes and modify the grids.
            pipe_commodity_out = PipeTechnology.get_type_defaults(pipe_type_name).get("commodity_out")
            for region_group in region_groups_per_grid_type[grid_type_name]:
                source_region = self._determine_source_region_for_group(
                    commodity=pipe_commodity_out, region_group=region_group,
                    effective_locations=effective_locations,
                    grid_tech_per_region=grid_tech_per_region, grid_type_name=grid_type_name)

                # From the source region, find the minimum spanning tree in this group.
                sub_graph_in_region_group = region_graph.subgraph(region_group)

                tree = nx.minimum_spanning_tree(sub_graph_in_region_group)

                successors = nx.dfs_successors(tree, source=source_region)

                def update_existing_capacities(start: int) -> float:
                    """
                    Start at the region with given index.
                    For each node, get the existing capacities of the children.
                    Update the existing capacities of the pipes (start, child) according to the capacity of the child.
                    Update the own capacity with the factor and the sum of the capacities from the pipes to children.
                    Return the grid capacity of the start node.
                    """
                    capacity_from_children = 0
                    for child in successors.get(start, []):
                        # on edge = (start,child)
                        existing_capacity_of_child_grid = update_existing_capacities(child)
                        child_pipe: PipeTechnology = pipes_per_type[pipe_type_name][(start, child)]
                        child_pipe.existing_capacity += existing_capacity_of_child_grid/grid_tech_per_region[child][grid_type_name].efficiency
                        capacity_from_children += child_pipe.existing_capacity/child_pipe.efficiency

                    grid_on_region: GridTechnology = grid_tech_per_region[start][grid_type_name]
                    grid_on_region.existing_capacity *= self._config.additional_grid_capacity_factor.get(grid_type_name,
                                                                                                         1.0)
                    grid_on_region.existing_capacity += capacity_from_children
                    return grid_on_region.existing_capacity

                total_grid_capacity = update_existing_capacities(source_region)
                # Total output capacity at the central tech (= grid input) after dividing by the grid efficiency.
                source_capacity_per_commodity[(pipe_commodity_out, source_region)] = (
                    total_grid_capacity / grid_tech_per_region[source_region][grid_type_name].efficiency)

        # Place central technologies in every effective location (filtered by per-tech street constraints) and
        # distribute existing capacities from the source regions onto the configured (tech, share) tuples.
        central_techs_per_region = self._build_central_technologies(effective_locations, source_capacity_per_commodity)

        # create regions
        regions = []
        for region_id, topology in self._region_topologies().items():
            technologies_per_region = []
            technologies_per_region.extend(decentralized_tech_per_region[region_id])
            technologies_per_region.extend(grid_tech_per_region[region_id].values())
            technologies_per_region.extend(central_techs_per_region[region_id].values())
            region = Region(id_=region_id,
                            topology=topology,
                            demands=demands_per_region[region_id],
                            technologies=technologies_per_region)
            regions.append(region)

        pipes = [pipe for pipes_by_connection in pipes_per_type.values() for pipe in pipes_by_connection.values()]
        energy_system = EnergySystem(name=self.energy_system_name,
                                     regions=regions,
                                     units=self._unit,
                                     system_topology=self._system_topology,
                                     imports=self._imports,
                                     pipes=pipes
                                     )
        logger.info("EnergySystemBuilder.build() took %.3f s", time.perf_counter() - start)
        return energy_system

    def verify(self):
        errors=[]
        if not isinstance(self.energy_system_name, str) or not self.energy_system_name:
            errors.append(f"Energy system name must be a non-empty string and not {type(self.energy_system_name)}")
        if not isinstance(self.base_crs, str) or not self.base_crs:
            errors.append(f"Base CRS must be a non-empty string and not {type(self.base_crs)}")
        if not isinstance(self._system_topology, Topology):
            errors.append(f"System topology must be set and of type Topology and not {type(self._system_topology)}")
        if not isinstance(self._data_registry, DataRegistry):
            errors.append(f"DataRegistry must be set and of type DataRegistry and not {type(self._data_registry)}")
        if not isinstance(self._unit, Unit):
            errors.append(f"Unit must be set and of type Unit and not {type(self._unit)}")
        if not isinstance(self._config, EnergySystemBuilderConfig) and self._config is not None:
            errors.append(f"Config must be of type EnergySystemBuilderConfig and not {type(self._config)}")
        if not self._demand_types:
            errors.append("At least one demand type must be added using set_demand_types() or add_demand_types().")
        if self._imports is None:
            errors.append("Imports must be set using set_imports() with a non-empty imports.yaml")

        if isinstance(self._config, EnergySystemBuilderConfig):
            if isinstance(self._system_topology, Topology):
                region_ids = set(self._region_topologies().keys())
                for commodity, regions in self._config.central_tech_locations_per_commodity.items():
                    for region_id in regions:
                        if region_id not in region_ids:
                            errors.append(
                                f"central_tech_locations_per_commodity[{commodity!r}] references region id "
                                f"{region_id} which is not part of the topology (known: {sorted(region_ids)}).")

            for commodity, by_region in self._config.central_tech_existing_capacities.items():
                allowed_regions = set(self._config.central_tech_locations_per_commodity.get(commodity, []))
                for region_key, share_list in by_region.items():
                    where = f"central_tech_existing_capacities[{commodity!r}][{region_key!r}]"
                    if region_key != "default" and region_key not in allowed_regions:
                        errors.append(
                            f"{where}: region {region_key} must be listed in "
                            f"central_tech_locations_per_commodity[{commodity!r}] (got {sorted(allowed_regions)}).")
                    if not isinstance(share_list, list) or not share_list:
                        errors.append(f"{where} must be a non-empty list of (tech_name, share) tuples.")
                        continue
                    share_sum = sum(share for _, share in share_list)
                    if not np.isclose(share_sum, 1.0):
                        errors.append(f"{where}: shares sum to {share_sum}, must be 1.0.")
                    for tech_name, _ in share_list:
                        if not CentralTechnology.has_type(tech_name):
                            errors.append(f"{where}: '{tech_name}' is not a registered CentralTechnology.")
                            continue
                        tech_commodity = CentralTechnology.get_type_defaults(tech_name).get("commodity_out")
                        if tech_commodity != commodity:
                            errors.append(
                                f"{where}: tech '{tech_name}' has commodity_out='{tech_commodity}', "
                                f"expected '{commodity}'.")

        if errors:
            raise ValueError("Errors in EnergySystemBuilder configuration:\n" + "\n".join(errors))