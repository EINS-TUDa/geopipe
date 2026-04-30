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
from ..data.dataset import CensusTechnology
from .region import Demand, Region
from .units import Unit, UnitEnum
from ..topology_builder.topology import Topology

import logging

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EnergySystem:
    name: str
    regions: list[Region]
    units: Unit
    system_topology: nx.Graph | None = None
    imports: list[Import] = field(default_factory=list)
    constraints: dict[str, dict[int, float]] = field(default_factory=dict)
    data_dir: str | Path | None = None
    pipes: list[PipeTechnology] = field(default_factory=list)


class EnergySystemBuilderConfig(BaseSettings):
    #: A dict that maps the name of a decentral technology to the minimum share (between 0 and 1) of the total demand
    minimum_decentral_technology_share: dict[str, float] = Field(default_factory=dict)
    #: Threshold to consider regions connected. Only one region of all connected regions holds the central technology
    considered_connected_region_distance_m: float = np.inf
    #: Key: commodity_out central technologies, Value: central technology name as default
    default_central_technology_per_commodity: dict[str, str]
    #: Key: commodity_out central technologies, Value: list of region ids to prioritize
    preferred_central_technologies_location_per_commodity: dict[str, list[int]] = Field(default_factory=dict)
    #: Name of the ID property on the edges of the topology Graph
    region_id_name: str = "id"
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

            value = sum(topology.property_from_edges(demand_type.demand_column_name))
            if not isinstance(value, (int, float)):
                raise ValueError(f"Demand value for {demand_type.name} not found or invalid in data registry.")

            # Create a RegionDemand instance
            demand = Demand(demand_type=demand_type, value=value, profile=profile, profile_name=demand_type.name)
            collection.append(demand)
        return collection

    @staticmethod
    def _default_demand_types():
        return [
            DemandType(name="residential_heat",
                       commodity_in="residential_heat",
                       cooperation_of_technologies=False,
                       profile_path=Path(__file__).parent / "HeatDemandProfile.txt",
                       demand_column_name="waerme_mwh",
                       technology_shares_query_params={
                           "key": "heating_shares",
                           "name_mapping": {
                               CensusTechnology.Gas: "ind_gas_boiler",
                               CensusTechnology.Oil: "ind_oil_boiler",
                               CensusTechnology.Wood: "wood",
                               CensusTechnology.Biomass: None,
                               CensusTechnology.Renewable: "ind_heat_pump",
                               CensusTechnology.Electric: None,
                               CensusTechnology.Coal: None,
                               CensusTechnology.District_Heating: "heat_exchanger",
                               CensusTechnology.NoEnergyCarrier: None,
                           },
                       },
                       default_decentral_supply_technology="ind_oil_boiler",
                       decrease_percent_per_year=0), ]

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
                logger.info("Share of technology %s for demand %s in region with topology %s is below "
                            "the minimum threshold. Skipping.", name, demand.demand_type.name, topology)
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

            for name in DecentralTechnology.registered_type_names():
                share = technology_shares_data.get(name, 0.0)
                existing_capacity = share * demand.peak(
                    year_period=0) * 1000  # factor energy (e.g. MWH) to power (e.g. KW)
                decentral_technologies.append(DecentralTechnology(name=name, existing_capacity=existing_capacity,
                                                                  output_profile_name=demand.profile_name))

        return decentral_technologies

    def _build_grids(self, topology: Topology, decentralized_tech_per_region) -> dict[str, GridTechnology]:
        region_length_km = topology.total_edge_length / 1000.0
        grids = {grid_type_name: GridTechnology(name=grid_type_name, length_km=region_length_km)
                 for grid_type_name in GridTechnology.registered_type_names()}

        for decentralized_technology in decentralized_tech_per_region:
            grid = grids.get(self._find_grid_type_from_decentralized_technology(decentralized_technology, grids))
            if not grid:
                continue
            grid.existing_capacity += decentralized_technology.existing_capacity

        return grids

    def _build_central_technologies(self, region_ids: tuple[int, ...],
                                    central_techs_per_region: dict[int, dict[str, CentralTechnology]],
                                    region_groups_per_grid_type: dict[str, list[set[int]]]) -> dict[
        int, dict[str, CentralTechnology]]:
        # Place central technologies in preferred locations
        # If no preferred region for a commodity, place central technologies in all regions outside connected groups.
        for grid_type in GridTechnology.registered_type_names():
            commodity_in = GridTechnology.get_type_defaults(grid_type).get("commodity_in")
            preferred_regions = self._config.preferred_central_technologies_location_per_commodity.get(commodity_in,
                                                                                                       None)
            if preferred_regions:
                for region_id in preferred_regions:
                    if region_id not in region_ids:
                        raise ValueError(
                            f"Preferred region id {region_id} for commodity {commodity_in} is not a valid region id.")
                    for central_tech_name in CentralTechnology.get_type_names_by_attribute("commodity_out",
                                                                                           commodity_in):
                        if central_tech_name in central_techs_per_region[region_id]:
                            continue
                        central_techs_per_region[region_id][central_tech_name] = CentralTechnology(
                            name=central_tech_name)
            else:
                for region_id in region_ids:
                    if any(region_id in group for group in region_groups_per_grid_type.get(grid_type, [])):
                        continue
                    for central_tech_name in CentralTechnology.get_type_names_by_attribute("commodity_out",
                                                                                           commodity_in):
                        if central_tech_name in central_techs_per_region[region_id]:
                            continue
                        central_techs_per_region[region_id][central_tech_name] = CentralTechnology(
                            name=central_tech_name)

        return central_techs_per_region

    def _pre_build(self):
        if not self._demand_types:
            logger.info("No demand types given. Using the default values.")
            self.add_demand_types(*self._default_demand_types())

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

    def _determine_region_for_central_technology(self, commodity: str, region_group: set[int]):
        preferred_regions = self._config.preferred_central_technologies_location_per_commodity.get(commodity)
        if not preferred_regions:
            return None
        for region in preferred_regions:
            if region in region_group:
                return region
        return None

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
        # Build pipes for all region connections
        # Derive existing capacities for grids, pipes and central technologies
        pipes_per_type: dict[str, dict[tuple[int, int], PipeTechnology]] = {}
        central_techs_per_region: dict[int, dict[str, CentralTechnology]] = {region_id: {} for region_id in region_ids}
        region_groups_per_grid_type: dict[str, list[set[int]]] = {}
        for pipe_type_name in PipeTechnology.registered_type_names():
            pipes_per_type[pipe_type_name] = {}
            # build a pipe for each connection for each type
            for region_1_id, region_2_id, length in region_connections:
                pipes_per_type[pipe_type_name][(region_1_id, region_2_id)] = PipeTechnology(
                    pipe_type_name, region_id_in=region_1_id,
                    region_id_out=region_2_id, pipe_length_km=length)
                pipes_per_type[pipe_type_name][(region_2_id, region_1_id)] = PipeTechnology(
                    pipe_type_name, region_id_in=region_2_id,
                    region_id_out=region_1_id, pipe_length_km=length)

            # The grid type name that works with the same commodities as the current pipe technology
            grid_type_name = self._find_grid_type_from_pipe_type(pipe_type_name)

            # A graph that contains regions as node and connections as edges.
            # Only those regions are included that have an existing capacity in their grid as the current pipe type
            # Only those connections are included that are below the maximum distance threshold
            region_graph = nx.Graph()
            nodes_in_graph = [region_id for region_id in region_ids
                              if grid_tech_per_region[region_id][grid_type_name].existing_capacity > 0]
            edges_in_graph = [(region_1_id, region_2_id, {"len": length}) for region_1_id, region_2_id, length in
                              region_connections
                              if length <= self._config.considered_connected_region_distance_m
                              if region_1_id in nodes_in_graph and region_2_id in nodes_in_graph]
            region_graph.add_nodes_from(nodes_in_graph)
            region_graph.add_edges_from(edges_in_graph)

            # List with sets of connected regions. Each set contains the region ids of connected regions.
            region_groups_per_grid_type[grid_type_name] = list(nx.connected_components(region_graph))

            # For each region group, plan the pipes and modify the grids.
            pipe_commodity_out = PipeTechnology.get_type_defaults(pipe_type_name).get("commodity_out")
            for region_group in region_groups_per_grid_type[grid_type_name]:
                # Determine the region in the group where the central technology is built
                central_tech_region = self._determine_region_for_central_technology(commodity=pipe_commodity_out,
                                                                                    region_group=region_group)
                if central_tech_region is None:
                    # Find the region in the group with the largest grid capacity
                    central_tech_region = max(region_group, key=lambda region_id: grid_tech_per_region[region_id][
                        grid_type_name].existing_capacity)

                # add all central technologies to the central_tech_region
                for central_tech_name in CentralTechnology.get_type_names_by_attribute("commodity_out",
                                                                                       pipe_commodity_out):
                    central_techs_per_region[central_tech_region][central_tech_name] = CentralTechnology(
                        name=central_tech_name)

                # From central tech region, find the minimum spanning tree in this group.
                sub_graph_in_region_group = region_graph.subgraph(region_group)

                tree = nx.minimum_spanning_tree(sub_graph_in_region_group)

                successors = nx.dfs_successors(tree, source=central_tech_region)

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
                        child_pipe.existing_capacity += existing_capacity_of_child_grid
                        capacity_from_children += child_pipe.existing_capacity

                    grid_on_region: GridTechnology = grid_tech_per_region[start][grid_type_name]
                    logger.info(
                        "Applying additional grid capacity factor for region %s and grid type %s. Original existing capacity: %s, "
                        "capacity from children: %s, factor: %s",
                        start, grid_type_name, grid_on_region.existing_capacity, capacity_from_children,
                        self._config.additional_grid_capacity_factor.get(grid_type_name, 1.0))
                    grid_on_region.existing_capacity *= self._config.additional_grid_capacity_factor.get(grid_type_name,
                                                                                                         1.0)
                    grid_on_region.existing_capacity += capacity_from_children
                    return grid_on_region.existing_capacity

                total_capacity = update_existing_capacities(central_tech_region)
                central_type_name = self._config.default_central_technology_per_commodity.get(pipe_commodity_out, None)
                if central_type_name is None:
                    raise ValueError(
                        f"Default central technology has to be configured for commodity {pipe_commodity_out} as "
                        f"the model has build existing capacities. ")
                if CentralTechnology.get_type_defaults(central_type_name).get("commodity_out") != pipe_commodity_out:
                    raise ValueError(
                        f"Default central technology {central_type_name} for commodity {pipe_commodity_out} has wrong commodity_out."
                        f"Should be {CentralTechnology.get_type_defaults(central_type_name).get('commodity_out')}.")
                central_techs_per_region[central_tech_region][central_type_name].existing_capacity = total_capacity

        # Place central technologies in preferred locations
        # If no preferred region for a commodity, place central technologies in all regions outside connected groups.
        central_techs_per_region = self._build_central_technologies(region_ids, central_techs_per_region, region_groups_per_grid_type)

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
        if not isinstance(self.energy_system_name, str) or not self.energy_system_name:
            raise ValueError(f"Energy system name must be a non-empty string and not {type(self.energy_system_name)}")
        if not isinstance(self.base_crs, str) or not self.base_crs:
            raise ValueError(f"Base crs must be a non-empty string and not {type(self.base_crs)}")
        if not isinstance(self._system_topology, Topology):
            raise ValueError("System topology must be set")
        # region_ids = set()
        # for _, _, d in self.street_network.edges(data=True):
        #     raw = d.get("id")
        #     try:
        #         region_ids.add(int(float(raw)))
        #     except (TypeError, ValueError):
        #         pass
        # if not region_ids:
        #     raise ValueError(
        #         "street_network has no edges with a valid 'id' attribute. "
        #         "Every edge must carry an integer 'id' indicating its region. "
        #         "Use set_street_network() with a graph produced by gdf_to_nx() after "
        #         "assigning region ids to the street GeoDataFrame."
        #     )
        # if self.rule_book is not None and not isinstance(self.rule_book, EnergySystemRuleBook):
        #     raise ValueError(f"RuleBook must be None or EnergySystemRuleBook and not {type(self.rule_book)}")
        # if not isinstance(self.data_registry, DataRegistry):
        #     raise ValueError(f"DataRegistry must be set and of type DataRegistry and not {type(self.data_registry)}")
        # if not isinstance(self.technology_registry, TechnologyRegistry):
        #     raise ValueError(
        #         f"TechnologyRegistry must be set and of type TechnologyRegistry and not {type(self.technology_registry)}"
        #     )
        # if not self.imports:
        #     raise ValueError("Imports must be set using set_imports() with a non-empty imports.yaml")
