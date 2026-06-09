# coding=utf-8
import time
from dataclasses import dataclass, field
from typing import Annotated, Optional
import numpy as np
import pandas as pd
from pathlib import Path
import networkx as nx
from pydantic import Field, PositiveFloat
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    YamlConfigSettingsSource,
)

from .imports_exports import Import, load_imports_exports_from_yaml, Export
from .technology import PipeTechnology, GridTechnology, CentralTechnology, DecentralTechnology
from .region import compute_region_connections, RegionConnections
from .demand import DemandType, ExplicitDemandValue
from ._year_dep import get_earliest_year_value
from ..data.data_registry import DataRegistry
from .region import Demand, Region
from .units import Unit, UnitEnum
from ..topology_builder.topology import Topology
from ..plot.energy_system_plotter import plot_system_topology

import logging

logger = logging.getLogger(__name__)

class EnergySystemValidationError(ValueError):
    pass

class EnergySystemBuilderError(ValueError):
    pass

@dataclass(slots=True)
class EnergySystem:
    name: str
    regions: list[Region]
    units: Unit
    system_topology: nx.Graph | None = None
    imports: list[Import] = field(default_factory=list)
    exports: list[Export] = field(default_factory=list)
    pipes: list[PipeTechnology] = field(default_factory=list)

    def plot_system_topology(self, output_path: Optional[str | Path] = None) -> None:
        plot_system_topology(self, output_path=output_path)

def validate_energy_system(es: EnergySystem):
    errors, warnings = [], []
    for imp in es.imports:
        if imp.max_cap_per_year is None and imp.max_energy_out_per_year is None:
            continue
        max_cap_y0 = get_earliest_year_value(imp.max_cap_per_year)
        max_energy_y0 = get_earliest_year_value(imp.max_energy_out_per_year)
        supplied_capacity = 0.0
        supplied_energy = 0.0
        central_with_existing: list[str] = []
        for region in es.regions:
            for tech in region.central_techs:
                if tech.commodity_in != imp.commodity_out:
                    continue
                supplied_capacity += tech.existing_capacity / tech.efficiency
                if tech.existing_capacity > 0 and max_energy_y0 is not None:
                    central_with_existing.append(
                        f"{tech.name}@region{region.id} (existing_capacity={tech.existing_capacity})"
                    )
            for tech in region.decentral_techs:
                if tech.commodity_in != imp.commodity_out:
                    continue
                supplied_capacity += tech.existing_capacity / tech.efficiency
                supplied_energy += tech.existing_energy_output / tech.efficiency  # central techs don't have existing_energy_output
        if central_with_existing:
            warnings.append(
                f"Import {imp.name} has max_energy_out_per_year={max_energy_y0} and must serve "
                f"central technologies with existing capacity: {', '.join(central_with_existing)}. "
                f"If max_energy_out_per_year is not sufficiently high, this will lead to infeasibility."
            )
        if max_cap_y0 is not None and supplied_capacity > max_cap_y0:
            errors.append(
                f"Inconsistent data input. Import {imp.name} has max_cap_per_year {max_cap_y0} but existing capacity of "
                f"technologies consuming {imp.commodity_out} is {supplied_capacity}. This will lead to "
                f"infeasibility. Consider increasing max_cap_per_year or reducing existing capacities.")
        if max_energy_y0 is not None and supplied_energy > max_energy_y0:
            errors.append(
                f"Inconsistent data input. Import {imp.name} has max_energy_out_per_year {max_energy_y0} but the "
                f"existing energy output of decentralized technologies, not considering central technologies, "
                f"consuming {imp.commodity_out} is {supplied_energy}. This will lead to "
                f"infeasibility. Consider increasing max_energy_out_per_year or reducing existing energy outputs.")
    if warnings:
        warning_message = "Energy system validation raised the following warnings:\n" + "\n".join(warnings)
        logger.warning(warning_message)
    if errors:
        error_message = "Energy system validation failed with the following errors:\n" + "\n".join(errors)
        raise EnergySystemValidationError(error_message)


#: A share constrained to the closed interval [0, 1].
Share = Annotated[float, Field(ge=0.0, le=1.0)]
#: A grid capacity factor; must be strictly greater than 1 (e.g. 1.1 == +10 %).
GridCapacityFactor = Annotated[float, Field(gt=1.0)]


class EnergySystemBuilderConfig(BaseSettings):
    #: A dict that maps the name of a decentral technology to the minimum share (between 0 and 1) of the total demand
    minimum_decentral_technology_share: dict[str, Share] = Field(default_factory=dict)
    #: Threshold to consider regions connected. Within a connected group, exactly one region acts as the
    #: source for existing-capacity propagation along the MST.
    considered_connected_region_distance_m: PositiveFloat = np.inf
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
    central_tech_existing_capacities: dict[str, dict[int | str, list[tuple[str, Share]]]] = Field(
        default_factory=dict)
    #: Name of the ID property on the edges of the topology Graph
    region_id_name: str = "id"
    #: Name of the street-id property on the edges of the topology Graph; used to evaluate
    #: ``constrain_location_to_streets`` for central technologies.
    street_id_name: str = "street_id"
    #: Factor added to the grid capacity. Dict of grid name to factor. 10 % corresponds to 1.1
    additional_grid_capacity_factor: dict[str, GridCapacityFactor]
    #: Forced tech share per region
    #: region_id → {tech_name: forced_share}
    #: Region existence, tech registration, commodity matching, and per-commodity sum (≤ 1) are
    #: enforced in ``EnergySystemBuilder.verify()`` where the technology registry is available.
    forced_decentral_technology_share_per_region: dict[int, dict[str, Share]] = Field(default_factory=dict)

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
        self._exports: Optional[list[Export]] = None
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

    def set_imports_exports(self, import_yaml: str | Path):
        self._imports, self._exports = load_imports_exports_from_yaml(import_yaml)
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

    def _build_demands(self, topology: Topology, region_id: int) -> list[Demand]:
        collection = []
        for demand_type in self._demand_types:
            # A None or zero value means the demand does not exist in this region; the value
            # source uses this to scope special demands to the regions where they are present.
            value = demand_type.value_source.value_for_region(region_id, topology)
            if value is None or value == 0:
                continue
            if not isinstance(value, (int, float)):
                raise ValueError(f"Demand value for {demand_type.name} not found or invalid in data registry.")

            profile = pd.read_csv(demand_type.profile_path, sep="\s+", decimal=".", header=None)
            if profile is None or profile.empty:
                raise ValueError(f"Demand profile for {demand_type.name} not found in data registry.")
            profile = pd.Series(profile.values.ravel())

            # Create a RegionDemand instance
            demand = Demand(demand_type=demand_type, value=value, profile=profile, profile_name=demand_type.name)
            collection.append(demand)
        return collection

    def _get_forced_technology_shares_for_demand(self, demand: Demand, region_id: int) -> dict[str, float]:
        """Filter ``forced_decentral_technology_share_per_region[region_id]`` to entries whose tech's ``commodity_out`` matches the demand's ``commodity_in``. Assumes the config has already been validated by ``verify()``."""
        forced_shares = self._config.forced_decentral_technology_share_per_region.get(region_id, {})
        return {
            name: share for name, share in forced_shares.items()
            if DecentralTechnology.has_type(name)
            and DecentralTechnology.get_type_defaults(name).get("commodity_out") == demand.demand_type.commodity_in
        }

    def _process_technology_shares(self, technology_shares_data: dict[str, float],
                                   demand: Demand, region_id: int) -> dict[str, float]:
        # If technology_shares_data is empty, fall back to the demand's default supply mix
        # (a single technology or a {tech: share} mix that sums to 1).
        if np.isclose(sum(technology_shares_data.values()), 0.0):
            default_shares = demand.demand_type.default_supply_shares()
            if not default_shares:
                raise EnergySystemBuilderError(
                    f"No default technology configured for demand commodity {demand.demand_type.commodity_in}")
            for default_tech_name in default_shares:
                if not DecentralTechnology.has_type(default_tech_name):
                    raise EnergySystemBuilderError(f"Default technology {default_tech_name} for demand commodity "
                                     f"{demand.demand_type.commodity_in} is not a valid DecentralTechnology")
            return dict(default_shares)

        # Apply minimum share thresholds from config
        for name, share in technology_shares_data.items():
            if share < self._config.minimum_decentral_technology_share.get(name, -1):
                logger.info("Share of technology %s for demand %s in region_id %s is below "
                            "the minimum threshold. Skipping.", name, demand.demand_type.name, region_id)
                technology_shares_data[name] = 0

        # Forced shares from config, or plain normalize if none
        forced_technology_shares = self._get_forced_technology_shares_for_demand(demand, region_id)
        if forced_technology_shares:
            logger.info("Applying forced technology shares for region_id %s: %s", region_id, forced_technology_shares)
            for name, forced_share in forced_technology_shares.items():
                technology_shares_data[name] = forced_share

            # Fix forced shares and scale all other shares proportionally so total (forced + others) == 1
            forced_keys = set(forced_technology_shares.keys())
            forced_total = sum(technology_shares_data.get(name, 0.0) for name in forced_keys)
            remaining = 1.0 - forced_total
            non_forced_keys = [k for k in technology_shares_data.keys() if k not in forced_keys]
            non_forced_sum = sum(technology_shares_data[k] for k in non_forced_keys)

            if non_forced_sum > 0 and not np.isclose(remaining, 0.0):
                scale = remaining / non_forced_sum
                for k in non_forced_keys:
                    technology_shares_data[k] = technology_shares_data[k] * scale
            else:
                # No non-forced share mass to distribute — set remaining non-forced shares to zero.
                for k in non_forced_keys:
                    technology_shares_data[k] = 0.0

        else: # Normalize shares to sum to 1 if they don't already
            total_share = sum(technology_shares_data.values())
            if not np.isclose(total_share, 1.0):
                technology_shares_data = {name: share / total_share for name, share in
                                          technology_shares_data.items()}

        return technology_shares_data

    def _build_decentral_technologies(self, topology: Topology, region_id: int, demands: list[Demand]) -> list[DecentralTechnology]:
        decentral_technologies = []
        base_query = {"region": topology.graph, "base_crs": self.base_crs}
        for demand in demands:
            matching_tech_names = DecentralTechnology.get_type_names_by_attribute(
                "commodity_out", demand.demand_type.commodity_in)
            if not matching_tech_names:
                # directly supplied by import
                logger.info("No decentral technology produces commodity '%s' for demand '%s' in region_id "
                            "%s; demand is supplied directly by import.",
                            demand.demand_type.commodity_in, demand.demand_type.name, region_id)
                continue

            if demand.demand_type.technology_shares_query_params is None:
                technology_shares_data: dict[str, float] = {}
            else:
                technology_shares_data = self._data_registry.query(
                    demand.demand_type.technology_shares_query_params | base_query)

            technology_shares_data = self._process_technology_shares(technology_shares_data, demand, region_id)

            shares_seen = set()
            for name in matching_tech_names:
                share = technology_shares_data.get(name, 0.0)
                existing_capacity = share * demand.peak(
                    year_period=0) * 1000  # factor energy (e.g. MWH) to power (e.g. KW)
                existing_energy_output = share * demand.value(0)
                output_profile_path = None
                if not demand.demand_type.cooperation_of_technologies:
                    output_profile_path = demand.demand_type.profile_path
                decentral_technologies.append(DecentralTechnology(name=name, existing_capacity=existing_capacity,
                                                                  existing_energy_output=existing_energy_output,
                                                                  output_profile_path=output_profile_path))
                shares_seen.add(name)
            for name, share in technology_shares_data.items():
                if share > 0 and name not in shares_seen:
                    raise EnergySystemBuilderError(f"Non-zero share {share} for technology '{name}' in demand "
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
            demands_per_region[region_id] = self._build_demands(topology, region_id)
            decentralized_tech_per_region[region_id] = self._build_decentral_technologies(topology, region_id,
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
            for (region_1_id, region_2_id), (length_m, connection_graph) in region_connections:
                pipes_per_type[pipe_type_name][(region_1_id, region_2_id)] = PipeTechnology(
                    pipe_type_name, region_id_in=region_1_id,
                    region_id_out=region_2_id, pipe_length_km=length_m / 1000.0, topology=connection_graph)
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
            edges_in_graph = [(region_1_id, region_2_id, {"len": length_m}) for
                              (region_1_id, region_2_id), (length_m, topology) in
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
                        child_pipe.existing_capacity += existing_capacity_of_child_grid / grid_tech_per_region[child][
                            grid_type_name].efficiency
                        capacity_from_children += child_pipe.existing_capacity / child_pipe.efficiency

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
                                     exports=self._exports,
                                     pipes=pipes
                                     )
        validate_energy_system(energy_system)
        logger.info("EnergySystemBuilder.build() took %.3f s", time.perf_counter() - start)
        return energy_system

    def verify(self):
        errors = []
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
            errors.append("Imports must be set using set_imports_exports() with a non-empty imports_exports.yaml")

        # DemandType validation: supplying technologies and explicit-value region scoping.
        demand_region_ids = (
            set(self._region_topologies().keys())
            if isinstance(self._system_topology, Topology) else None
        )
        import_commodities = {imp.commodity_out for imp in self._imports} if self._imports else set()
        for demand_type in self._demand_types:
            where = f"DemandType '{demand_type.name}'"
            # The demand commodity must be suppliable: either by a decentral technology
            # (e.g. a boiler/heat exchanger) or directly by an import (e.g. electricity via the grid).
            suppliable_by_tech = bool(
                DecentralTechnology.get_type_names_by_attribute("commodity_out", demand_type.commodity_in))
            suppliable_by_import = demand_type.commodity_in in import_commodities
            if not suppliable_by_tech and not suppliable_by_import:
                errors.append(
                    f"{where}: commodity '{demand_type.commodity_in}' is neither the commodity_out of any "
                    f"registered DecentralTechnology nor of any import, so the demand cannot be supplied.")
            # When the demand is supplied by decentral technologies and has no census shares, a default
            # supply mix is required to seed their existing capacity. Import-only demands (no matching
            # decentral tech) need no default.
            if (suppliable_by_tech and demand_type.technology_shares_query_params is None
                    and not demand_type.default_supply_shares()):
                errors.append(
                    f"{where}: default_decentral_supply_technology is required when "
                    f"technology_shares_query_params is None and the commodity is supplied by decentral "
                    f"technologies.")
            # Default supply technologies must be registered and match the demand commodity.
            for tech_name in demand_type.default_supply_shares():
                if not DecentralTechnology.has_type(tech_name):
                    errors.append(f"{where}: default_decentral_supply_technology '{tech_name}' is not a "
                                  f"registered DecentralTechnology.")
                    continue
                commodity_out = DecentralTechnology.get_type_defaults(tech_name).get("commodity_out")
                if commodity_out != demand_type.commodity_in:
                    errors.append(
                        f"{where}: default supply tech '{tech_name}' has commodity_out='{commodity_out}', "
                        f"expected '{demand_type.commodity_in}'.")
            # ExplicitDemandValue region keys must exist in the topology.
            if isinstance(demand_type.value_source, ExplicitDemandValue) and demand_region_ids is not None:
                for region_id in demand_type.value_source.value_per_region:
                    if region_id not in demand_region_ids:
                        errors.append(
                            f"{where}: value_source references region id {region_id} which is not part of the "
                            f"topology (known: {sorted(demand_region_ids)}).")

        if isinstance(self._config, EnergySystemBuilderConfig):
            topology_region_ids = (
                set(self._region_topologies().keys())
                if isinstance(self._system_topology, Topology) else None
            )

            # minimum_decentral_technology_share: keys must be registered DecentralTechnologies.
            for tech_name in self._config.minimum_decentral_technology_share:
                if not DecentralTechnology.has_type(tech_name):
                    errors.append(
                        f"minimum_decentral_technology_share references '{tech_name}' which is not a "
                        f"registered DecentralTechnology.")

            # additional_grid_capacity_factor: keys must be registered GridTechnologies.
            for grid_name in self._config.additional_grid_capacity_factor:
                if not GridTechnology.has_type(grid_name):
                    errors.append(
                        f"additional_grid_capacity_factor references '{grid_name}' which is not a "
                        f"registered GridTechnology.")

            # central_tech_locations_per_commodity: each commodity must be the commodity_out of some
            # registered CentralTechnology, and every region id must be part of the topology.
            for commodity, regions in self._config.central_tech_locations_per_commodity.items():
                if not CentralTechnology.get_type_names_by_attribute("commodity_out", commodity):
                    errors.append(
                        f"central_tech_locations_per_commodity references commodity '{commodity}' which is "
                        f"not the commodity_out of any registered CentralTechnology.")
                if topology_region_ids is not None:
                    for region_id in regions:
                        if region_id not in topology_region_ids:
                            errors.append(
                                f"central_tech_locations_per_commodity[{commodity!r}] references region id "
                                f"{region_id} which is not part of the topology "
                                f"(known: {sorted(topology_region_ids)}).")

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

            demand_commodities = {dt.commodity_in for dt in self._demand_types}
            for region_id, tech_shares in self._config.forced_decentral_technology_share_per_region.items():
                where = f"forced_decentral_technology_share_per_region[{region_id}]"
                if topology_region_ids is not None and region_id not in topology_region_ids:
                    errors.append(
                        f"{where}: region {region_id} is not part of the topology "
                        f"(known: {sorted(topology_region_ids)}).")
                shares_per_commodity: dict[str, float] = {}
                for tech_name, share in tech_shares.items():
                    entry = f"{where}['{tech_name}']"
                    if not DecentralTechnology.has_type(tech_name):
                        errors.append(f"{entry}: '{tech_name}' is not a registered DecentralTechnology.")
                        continue
                    commodity_out = DecentralTechnology.get_type_defaults(tech_name).get("commodity_out")
                    if commodity_out not in demand_commodities:
                        errors.append(
                            f"{entry}: tech has commodity_out='{commodity_out}', but no demand type with "
                            f"commodity_in='{commodity_out}' is configured "
                            f"(known: {sorted(demand_commodities)}).")
                        continue
                    shares_per_commodity[commodity_out] = shares_per_commodity.get(commodity_out, 0.0) + share
                for commodity_out, total in shares_per_commodity.items():
                    if total > 1.0 + 1e-9:
                        errors.append(
                            f"{where}: forced shares for commodity_out='{commodity_out}' sum to "
                            f"{total} > 1.0.")

        if errors:
            raise ValueError("Errors in EnergySystemBuilder configuration:\n" + "\n".join(errors))