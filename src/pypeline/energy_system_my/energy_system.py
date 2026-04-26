# coding=utf-8
# from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
import networkx as nx
import numpy as np
import pandas as pd
from shapely.geometry import MultiPoint

from pypeline.energy_technology.technology_historical import GridType
from pypeline.energy_technology.technology_registry import TechnologyRegistry
from pypeline.energy_system_my.imports import Imports
from pypeline.energy_technology.technology import PipeTechnology, GridTechnology, CentralTechnology
from pypeline.energy_system_my.region import Region, DemandType, compute_region_connections, RegionConnections
from pypeline.units import Unit

# from __future__ import annotations
from pathlib import Path
from typing import Any
import networkx as nx
import yaml

from pypeline.data.data_registry import DataRegistry
from pypeline.data.dataset import CensusTechnology
from pypeline.energy_system_my.region import Demand, Region
from pypeline.energy_system.dhn import (
    build_district_heat_grid_from_topology,
    build_inter_dhn_pipes_from_topologies,
)
from pypeline.energy_system_my.imports import Imports, load_imports_from_yaml
# from pypeline.energy_system.region import RegionBuilder
# from pypeline.energy_system.rule_book import (
#     DEFAULT_HEAT_GRID_COST_DATASET,
#     DEFAULT_HEAT_GRID_DEMAND_NAME,
#     EnergySystemRuleBook,
#     HeatExchangerCostAdjustmentRule,
#     MinimumHeatGridConstraintRule,
#     MinimumHeatGridOutputRule,
#     RegionRuleBook,
# )
from pypeline.units import Unit, UnitEnum
from pypeline.energy_technology.technology_registry import TechnologyRegistry
from pypeline.topology_builder.topology import Topology
from pypeline.energy_technology.technology import DecentralTechnology
import logging


from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    YamlConfigSettingsSource,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EnergySystem:
    name: str
    regions: list[Region]
    units: Unit
    street_network: nx.Graph | None = None
    technology_registry: TechnologyRegistry | None = None
    imports: list[Imports] = field(default_factory=list)
    constraints: dict[str, dict[int, float]] = field(default_factory=dict)
    data_dir: str | Path | None = None
    pipes: list[PipeTechnology] = field(default_factory=list)


class EnergySystemBuilderConfig(BaseSettings):

    #: A dict that maps the name of a decentral technology to the minimum share (between 0 and 1) of the total demand
    minimum_decentral_technology_share: dict[str, float] = Field(default_factory=dict)
    #: Key: demand commodity name, Value: decentral technology name as default
    default_decentral_technology_per_demand_commodity: dict[str, str] = Field(default_factory=dict)
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
        # self.rule_book: EnergySystemRuleBook | None = None
        # self.region_rule_book: RegionRuleBook | None = None
        self._data_registry: DataRegistry | None = None
        self._technology_registry: TechnologyRegistry | None = None
        self._unit: Unit = UnitEnum.GW.unit
        self._config: EnergySystemBuilderConfig
        self._demand_types: list[DemandType] = []
        # self.imports: list[Imports] | None = None
        # self.pipe_technology_name: str = "heat_pipe"
        # self._dhn_central_seed_cache: dict[int, str] = {}
        self.__region_topologies = None

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

    def set_technology_registry(self, technology_registry: TechnologyRegistry):
        self._technology_registry = technology_registry
        return self

    def set_unit(self, input_unit: UnitEnum):
        self._unit = input_unit.unit
        return self

    def add_demand_types(self, *demand_types):
        self._demand_types.extend(demand_types)
        return self

    # def set_region_rule_book(self, rule_book: RegionRuleBook):
    #     self.region_rule_book = rule_book
    #     return self
    #
    # def set_energy_system_rule_book(self, rule_book: EnergySystemRuleBook):
    #     self.rule_book = rule_book
    #     return self

    # def set_demands(self, demands: list[Demand] | None = None, default: bool = False):
    #     if default:
    #         self._set_default_demands()
    #         return self
    #     if not isinstance(demands, list) or not all(isinstance(d, Demand) for d in demands):
    #         raise TypeError("demands must be a list of Demand instances.")
    #     self.demands = demands
    #     return self

    def set_config(self, config: EnergySystemBuilderConfig):
        self._config = config
        return self
    # def set_region_builder_config(self, config: dict[str, Any] | None, *, merge: bool = True):
    #     if config is None:
    #         return self
    #     if not isinstance(config, dict):
    #         raise TypeError("region_builder_config must be a mapping")
    #
    #     if self.region_builder_config is None or not merge:
    #         self.region_builder_config = dict(config)
    #     else:
    #         merged = dict(self.region_builder_config)
    #         merged.update(config)
    #         self.region_builder_config = merged
    #     return self

    @property
    def _region_topologies(self):
        if self.__region_topologies is None:
            self.__region_topologies = self._system_topology.sub_topologies_by_edge_property("id")
        return self.__region_topologies

    def _build_demands(self, topology: Topology) -> list[Demand]:
        collection = []
        for demand_type in self._demand_types:
            # base_query = {"region": topology, "base_crs": self.base_crs}
            profile = pd.read_csv(demand_type.profile_path)
            if profile is None or profile.empty:
                raise ValueError(f"Demand profile for {demand_type.name} not found in data registry.")

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
                       demand_column_name="residential_heat_demand",
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
                       default_supply_technology="ind_oil_boiler",
                       decrease_percent_per_year=0),]

    #
    # def set_imports(self, import_yaml: str | Path):
    #     self.imports = load_imports_from_yaml(import_yaml)
    #     return self
    #
    # def set_default_region_builder_config(self):
    #     self.region_builder_config = {
    #         "cap_factor_ind_technologies": 1.1,
    #         "min_heat_grid_output_mwh": 0.0,
    #         "min_heat_grid_share": None,
    #         "heat_grid_demand_name": DEFAULT_HEAT_GRID_DEMAND_NAME,
    #         "heat_grid_names": ("heat_exchanger",),
    #         "interdistrict_free_pipe_max_length_m": None,
    #         "heat_grid_cost_dataset": DEFAULT_HEAT_GRID_COST_DATASET,
    #         "heat_grid_cost_scaling": 1.0,
    #         "heat_grid_cost_min_factor": 1.0,
    #         "heat_grid_decentralized_keys": tuple(
    #             ct.value for ct in CensusTechnology if ct is not CensusTechnology.District_Heating
    #         ),
    #     }
    #     return self
    #
    # def _heat_grid_rule_settings(self) -> tuple[float, float | None, str, tuple[str, ...]] | None:
    #     config = self.region_builder_config or {}
    #     min_output = float(config.get("min_heat_grid_output_mwh", 0.0) or 0.0)
    #     min_share_raw = config.get("min_heat_grid_share")
    #     demand_name = config.get("heat_grid_demand_name", DEFAULT_HEAT_GRID_DEMAND_NAME)
    #     heat_grid_names = config.get("heat_grid_names", ("heat_exchanger",))
    #
    #     min_share = None
    #     if min_share_raw is not None:
    #         min_share = float(min_share_raw)
    #
    #     if min_output <= 0.0 and not (min_share is not None and min_share > 0.0):
    #         return None
    #
    #     return min_output, min_share, demand_name, tuple(heat_grid_names)
    #
    # def _heat_grid_cost_settings(self) -> tuple[str, float, float, tuple[str, ...]]:
    #     config = self.region_builder_config or {}
    #     dataset = config.get("heat_grid_cost_dataset", DEFAULT_HEAT_GRID_COST_DATASET)
    #     scale = float(config.get("heat_grid_cost_scaling", 1.0))
    #     min_factor = float(config.get("heat_grid_cost_min_factor", 1.0))
    #     raw_keys = config.get("heat_grid_decentralized_keys")
    #     if raw_keys:
    #         decentralized = tuple(str(key) for key in raw_keys)
    #     else:
    #         decentralized = tuple(
    #             ct.value for ct in CensusTechnology if ct is not CensusTechnology.District_Heating
    #         )
    #     return dataset, scale, min_factor, decentralized
    #
    # def _ensure_heat_grid_rules(self) -> None:
    #     settings = self._heat_grid_rule_settings()
    #     if not settings:
    #         return
    #
    #     min_output, min_share, demand_name, heat_grid_names = settings
    #
    #     if self.region_rule_book is None:
    #         self.region_rule_book = RegionRuleBook()
    #     if not any(isinstance(rule, MinimumHeatGridOutputRule) for rule in getattr(self.region_rule_book, "rules", [])):
    #         self.region_rule_book.add_rule(
    #             MinimumHeatGridOutputRule(
    #                 demand_name=demand_name,
    #                 min_output_mwh=min_output,
    #                 min_share=min_share,
    #                 heat_grid_names=heat_grid_names,
    #             )
    #         )
    #
    #     if self.rule_book is None:
    #         self.rule_book = EnergySystemRuleBook()
    #     if not any(isinstance(rule, MinimumHeatGridConstraintRule) for rule in getattr(self.rule_book, "rules", [])):
    #         self.rule_book.add_rule(
    #             MinimumHeatGridConstraintRule(
    #                 demand_name=demand_name,
    #                 min_output_mwh=min_output,
    #                 min_share=min_share,
    #                 heat_grid_names=heat_grid_names,
    #             )
    #         )
    #
    #     dataset, scale, min_factor, decentralized_keys = self._heat_grid_cost_settings()
    #     if (
    #         self.data_registry
    #         and not any(isinstance(rule, HeatExchangerCostAdjustmentRule) for rule in getattr(self.region_rule_book, "rules", []))
    #     ):
    #         self.region_rule_book.add_rule(
    #             HeatExchangerCostAdjustmentRule(
    #                 data_registry=self.data_registry,
    #                 dataset_type=dataset,
    #                 heat_exchanger_names=heat_grid_names,
    #                 decentralized_keys=decentralized_keys,
    #                 scale_factor=scale,
    #                 min_factor=min_factor,
    #             )
    #         )
    #
    # def _infer_dhn_central_seed_base(self, topology: nx.Graph, district_id: int) -> str:
    #     district_id_i = int(district_id)
    #     cached = self._dhn_central_seed_cache.get(district_id_i)
    #     if cached:
    #         return cached
    #
    #     base_query = {
    #         "key": "heating_shares",
    #         "region": topology,
    #         "base_crs": self.base_crs,
    #         "name_mapping": {},
    #     }
    #     raw_shares = self.data_registry.query(base_query)
    #     if not isinstance(raw_shares, dict):
    #         raise TypeError(
    #             f"Heating share query must return a mapping, got {type(raw_shares)} for district {district_id_i}"
    #         )
    #
    #     candidates: list[tuple[str, float]] = [
    #         ("cen_gas_boiler", float(raw_shares.get(CensusTechnology.Gas, 0.0) or 0.0)),
    #         ("cen_oil_boiler", float(raw_shares.get(CensusTechnology.Oil, 0.0) or 0.0)),
    #         (
    #             "cen_biomass_woodpellets",
    #             float(raw_shares.get(CensusTechnology.Wood, 0.0) or 0.0)
    #             + float(raw_shares.get(CensusTechnology.Biomass, 0.0) or 0.0),
    #         ),
    #     ]
    #     candidates.sort(key=lambda entry: (entry[1], entry[0]), reverse=True)
    #
    #     viable = [
    #         (name, score)
    #         for name, score in candidates
    #         if score > 0.0 and self.technology_registry.has_technology(name)
    #     ]
    #     if not viable:
    #         raise ValueError(
    #             "Unable to infer district-heating central technology from local fuel shares "
    #             f"for district {district_id_i}. Expected positive Gas/Heizoel/Wood/Biomass shares."
    #         )
    #
    #     selected = viable[0][0]
    #     self._dhn_central_seed_cache[district_id_i] = selected
    #     return selected

    def _process_technology_shares(self, technology_shares_data: dict[str, float], demand: Demand, topology: Topology) \
            -> dict[str, float]:
        # If technology_shares_data is empty
        if np.isclose(sum(technology_shares_data.values()), 0.0):
            default_tech_name = (self._config.default_decentral_technology_per_demand_commodity.
                                 get(demand.demand_type.commodity_in))
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
            technology_shares_data: dict[str, float] = self._data_registry.query(demand.demand_type.technology_shares_query_params | base_query)

            technology_shares_data = self._process_technology_shares(technology_shares_data, demand, topology)

            for name in DecentralTechnology.registered_type_names():
                share = technology_shares_data.get(name, 0.0)
                decentral_technologies.append(DecentralTechnology(name=name, existing_capacity=share))

        return decentral_technologies

    def _pre_build(self):
        if not self._demand_types:
            logger.info("No demand types given. Using the default values.")
            self.add_demand_types(*self._default_demand_types())

        if self._config is None:
            logger.info("No config given. Using the default values.")
            self.set_config(EnergySystemBuilderConfig())

        self.verify()

    def _find_grid_type_from_pipe_type(self, pipe_type_name: str) -> Optional[GridType]:
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

    def _determine_region_for_central_technology(self, commodity: str, region_group: list[int]):
        preferred_region = self._config.preferred_central_technologies_location_per_commodity.get(commodity)
        if preferred_region is None or preferred_region not in region_group:
            return None
        return preferred_region

    def build(self) -> EnergySystem:
        self._pre_build()

        region_ids = tuple(self._region_topologies.keys())
        demands_per_region: dict[int, list[Demand]] = {}
        decentralized_tech_per_region: dict[int, list[DecentralTechnology]] = {}
        for region_id, topology in self._region_topologies.items():
            demands_per_region[region_id] = self._build_demands(topology)
            decentralized_tech_per_region[region_id] = self._build_decentral_technologies(topology,
                                                                                          demands_per_region[region_id])

        # Create grids per region. Each registered grid type exists on every region.
        grid_tech_per_region: dict[int, dict[str, GridTechnology]] = {}
        for region_id, decentralized_technologies in decentralized_tech_per_region.items():
            grids = {grid_type_name: GridTechnology(name=grid_type_name)
                     for grid_type_name in GridTechnology.registered_type_names()}

            for decentralized_technology in decentralized_technologies:
                decentralized_technology: DecentralTechnology
                grid = grids.get(decentralized_technology.commodity_in)
                if not grid:
                    continue
                grid.existing_capacity += decentralized_technology.existing_capacity
            grid_tech_per_region[region_id] = grids

        # All connections between regions
        region_connections = compute_region_connections(self._region_topologies, self._system_topology,
                                                        self._config.region_id_name)

        for pipe_type_name in PipeTechnology.registered_type_names():
            # The grid type name that works with the same commodities as the current pipe technology
            grid_type_name = self._find_grid_type_from_pipe_type(pipe_type_name)

            # A graph that contains regions as node and connections as edges.
            # Only those regions are included that have an existing capacity in their grid as the current pipe type
            # Only those connections are included that are below the maximum distance threshold
            region_graph = nx.Graph()
            nodes_in_graph = [region_id for region_id in region_ids
                                if grid_tech_per_region[region_id][grid_type_name].existing_capacity > 0]
            edges_in_graph = [(region_1, region_2, {"len": length}) for region_1, region_2, length in region_connections
                              if length <= self._config.considered_connected_region_distance_m
                              if region_1 in nodes_in_graph and region_2 in nodes_in_graph]
            region_graph.add_nodes_from(nodes_in_graph)
            region_graph.add_edges_from(edges_in_graph)

            # List with sets of connected regions. Each set contains the region ids of connected regions.
            region_groups: list[set] = list(nx.connected_components(region_graph))

            # For each region group, plan the pipes and modify the grids.
            commodity = PipeTechnology.get_type_defaults(pipe_type_name).get("commodity_in")
            for region_group in region_groups:
                # Determine the region in the group where the central technology is built
                central_tech_region = self._determine_region_for_central_technology(commodity=commodity,
                                                                                    region_group=region_group)
                if central_tech_region is None:
                    # Find the region in the group with the largest grid capacity
                    central_tech_region = max(region_ids, key=lambda region_id: grid_tech_per_region[region_id][grid_type_name].existing_capacity)

                # From this region, find the minimum spanning tree in this group.
                sub_graph_in_region_group = region_graph.subgraph(region_group)
                pipes = {}
                for edge, data in sub_graph_in_region_group.edges(data=True):
                    length = data["len"]
                    pipes[(edge[0], edge[1])] = PipeTechnology(pipe_type_name, region_id_in=edge[0],
                                                               region_id_out=edge[1], pipe_length_km=length)
                    pipes[(edge[1], edge[0])] = PipeTechnology(pipe_type_name, region_id_in=edge[1],
                                                               region_id_out=edge[0], pipe_length_km=length)

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
                    for child in successors[start]:
                        # on edge = (start,child)
                        existing_capacity_of_child_grid = update_existing_capacities(child)
                        child_pipe: PipeTechnology = pipes[(start, child)]
                        child_pipe.existing_capacity += existing_capacity_of_child_grid
                        capacity_from_children += child_pipe.existing_capacity

                    grid_on_region: GridTechnology = grid_tech_per_region[start][grid_type_name]
                    grid_on_region.existing_capacity *= self._config.additional_grid_capacity_factor
                    grid_on_region.existing_capacity += capacity_from_children
                    return grid_on_region.existing_capacity

                total_capacity = update_existing_capacities(central_tech_region)
                commodity_in = GridTechnology.get_type_defaults(grid_type_name).get("commodity_in")
                central_type_name = self._config.default_central_technology_per_commodity[commodity_in]
                CentralTechnology(name=central_type_name, existing_capacity=total_capacity)


                    # Add pipes on this tree with nonzero existing capacity. Other pipes have zero existing capacity


        # Determine for each decentral tech if it needs a grid

        # 2. Identify central technology location within the connected groups:
        # Take the first match for self._config.preferred_central_technology_location_per_commodity, if no match,
        # take Region with the highest demand for the commodity supplied by the central technology.


        # 3. Instantiate grids in all regions.
        # For existing capacities in connected regions: Calculate flow within connected regions to derive existing capacity of grid


        # 4. ?How to best allow the user to indicate:
        # Central Technology X only exists in Reigon Y (nowhere else) with existing capacity Z)?
        # This will influence step 5

        # 5. Instantiate central technologies in identified regions of step 2. Don't add central technologies at all
        # to other regions in connected groups (not even without existing capacity). Build
        # central technologies in all other regions of preferred_central_technology_location_per_commodity
        # If preferred_central_technology_location_per_commodity is None, built everywhere (except connected groups)






        #
        #
        #
        # self._dhn_central_seed_cache = {}
        #
        # rb = RegionBuilder(
        #     base_crs=self.base_crs,
        #     data_registry=self.data_registry,
        #     technology_registry=self.technology_registry,
        # )
        # rb.set_demands(self.demands)
        # if not self.region_builder_config:
        #     self.set_default_region_builder_config()
        # rb.set_config(self.region_builder_config)
        # rb.set_dhn_central_seed_base_resolver(self._infer_dhn_central_seed_base)
        #
        # self._ensure_heat_grid_rules()
        # if self.region_rule_book:
        #     rb.set_rule_book(self.region_rule_book)
        #
        # regions: list[Region] = []
        # for region_id, topology in self._region_subgraphs():
        #     regions.append(rb.build(topology=topology, region_id=region_id))
        #
        # pipe_tech = self.technology_registry.get_by_name(self.pipe_technology_name)
        # for region in regions:
        #     cost = build_district_heat_grid_from_topology(
        #         topology=region.topology,
        #         pipe_capex_eur_per_km=float(pipe_tech.pipe_capex_eur_per_km),
        #     )
        #     region.local_dhn_capex_base_eur = float(cost["local_grid_capex_base_eur"])
        #
        # pipes = []
        # if len(regions) > 1:
        #     distance_threshold_m = (self.region_builder_config or {}).get("interdistrict_free_pipe_max_length_m")
        #     pipe_eff = max(0.0, min(1.0, float(pipe_tech.efficiency)))
        #     pipes = build_inter_dhn_pipes_from_topologies(
        #         regions=regions,
        #         full_network=self.street_network,
        #         pipe_capex_eur_per_km=float(pipe_tech.pipe_capex_eur_per_km),
        #         below_distance_threshold_m=(None if distance_threshold_m is None else float(distance_threshold_m)),
        #         pipe_loss_fraction=max(0.0, 1.0 - pipe_eff),
        #         pipe_capex_eur_per_mw=float(pipe_tech.capex_cost_power),
        #         pipe_opex_eur_per_mwh=float(pipe_tech.opex_cost_energy),
        #         pipe_cap_max_mw=float(pipe_tech.cap_max),
        #         pipe_lifetime_years=int(pipe_tech.technical_lifetime),
        #     )
        #
        # es = EnergySystem(
        #     name=self.energy_system_name,
        #     regions=regions,
        #     street_network=self.street_network,
        #     units=self.unit,
        #     technology_registry=self.technology_registry,
        #     imports=self.imports,
        #     pipes=pipes,
        # )
        #
        # if self.rule_book:
        #     es = self.rule_book.apply(es)
        #
        # return es

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
