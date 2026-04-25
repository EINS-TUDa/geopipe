from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Iterable, Callable
import networkx as nx
import pandas as pd
from shapely.geometry import MultiPoint

from pypeline import DataRegistry
from pypeline.energy_technology import Technology
from pypeline.energy_technology.technology_new import DecentralTech, CentralTech, CHP, Grid, Pipe
from pypeline.energy_technology.technology_registry import TechnologyRegistry
from pypeline.energy_system.imports import Imports
from pypeline.energy_system.pipe import Pipe
from pypeline.units import Unit


@dataclass(frozen=True)
class DemandType:
    name: str
    commodity_in: str
    cooperation_of_technologies: bool
    default_supply_technology: str
    demand_query_params: dict[str, Any]
    profile_query_params: dict[str, Any]
    technology_shares_query_params: dict[str, Any]
    decrease_percent_per_year: float


class Demand:

    def __init__(self, demand_type: DemandType, value: float, profile: pd.Series, profile_name: str):
        """

        Parameters
        ----------
        demand_type
        value : float
            The value of the initial year
        profile : pd.Series
            A Series with numeric values. Consists of 8760 data points with the demand per hour
        profile_name : str
        decrease_percent_per_year :
            Decrease of the value per year in percent
        """
        self._demand_type = demand_type
        self._profile = profile / profile.sum()
        self._profile_name = profile_name
        self._value = value

    @property
    def demand_type(self) -> DemandType:
        return self._demand_type

    @property
    def name(self) -> str:
        return self._demand_type.name

    @property
    def profile(self) -> pd.Series:
        return self._profile

    @property
    def profile_name(self) -> str:
        return self._profile_name

    def value(self, year_period) -> float:
        """The value of the year after the start"""
        return self._value * (1 - self.demand_type.decrease_percent_per_year) ** year_period


class Region:
    def __init__(self, id_: int, topology: nx.Graph, demands: Iterable[Demand],
                 technologies: Iterable[DecentralTech | CentralTech | CHP | Grid]):
        self._id = id_
        self._topology = topology
        self._demands = {demand.name: demand for demand in demands}
        self._technologies = {"decentral": [], "central": [], "chp": [], "grid": []}
        class_to_key = {DecentralTech: "decentral", CentralTech: "central", CHP: "chp", Grid: "grid"}
        for technology in technologies:
            self._technologies[class_to_key[type(technology)]].append(technology)

    @property
    def id(self) -> int:
        return self._id

    @property
    def topology(self) -> nx.Graph:
        return self._topology

    @property
    def boundary(self):
        """Convex hull of topology nodes, usable as a polygon geometry for visualisation."""
        nodes = list(self._topology.nodes)
        if not nodes:
            raise ValueError(f"Region {self.id} has no topology nodes")
        return MultiPoint(nodes).convex_hull

    @property
    def crs(self):
        return self._topology.graph.get("crs")

    def demand(self, name: str) -> Optional[Demand]:
        return self._demands.get(name, None)

    @property
    def decentral_techs(self) -> tuple[DecentralTech]:
        return self._technologies["decentral"]

    @property
    def central_techs(self) -> tuple[CentralTech]:
        return self._technologies["central"]

    @property
    def chps(self) -> tuple[CHP]:
        return self._technologies["chp"]

    @property
    def grids(self) -> tuple[Grid]:
        return self._technologies["grid"]


class RegionBuilder:

    def __init__(self,
                 technology_registry: TechnologyRegistry,
                 data_registry: DataRegistry,
                 base_crs: str = "EPSG:25832", ):
        self.base_crs = base_crs
        self.data_registry = data_registry
        self.technology_registry = technology_registry

        # self.rule_book = None
        # self.config = None
        self._demands_types = []
        # self._dhn_central_seed_base_resolver: Callable[[nx.Graph, int], str] | None = None



    #
    # def set_rule_book(self, rule_book: RegionRuleBook):
    #     if not isinstance(rule_book, RegionRuleBook):
    #         raise TypeError(f"rule_book must be an instance of RegionRuleBook and not {type(rule_book)}.")
    #     self.rule_book = rule_book
    #     return self
    #
    # def set_config(self, config: dict):
    #     self.config = config
    #     return self
    #
    # def set_dhn_central_seed_base_resolver(
    #         self,
    #         resolver: Callable[[nx.Graph, int], str] | None,
    # ):
    #     self._dhn_central_seed_base_resolver = resolver
    #     return self
    #
    # def _resolve_dhn_central_seed_base(self, topology: nx.Graph, district_id: int) -> str:
    #     if self._dhn_central_seed_base_resolver is None:
    #         raise ValueError(
    #             "DHN central seed resolver is not configured on RegionBuilder. "
    #             "Use set_dhn_central_seed_base_resolver()."
    #         )
    #     return self._dhn_central_seed_base_resolver(topology, district_id)


    #
    # def _ensure_regional_assets(self, district_id: int) -> None:
    #     suffix = _district_suffix(district_id)
    #     registry = self.technology_registry
    #     regional_base_names = _regional_base_names(registry)
    #
    #     localized: dict[str, str] = {base: _localized_name(base, district_id) for base in regional_base_names}
    #
    #     for base_name, localized_base in localized.items():
    #         if not registry.has_technology(base_name):
    #             continue
    #         base_tech = registry.get_by_name(base_name)
    #         max_units_attr = base_tech.max_units
    #         unlimited = False
    #         units = 1
    #         if max_units_attr is None:
    #             unlimited = True
    #         else:
    #             try:
    #                 units = max(1, int(max_units_attr))
    #             except Exception:
    #                 unlimited = True
    #                 units = 1
    #
    #         existing_names = self._clone_names(localized_base)
    #
    #         for extra_name in existing_names[1:]:
    #             try:
    #                 registry.remove(extra_name)
    #             except TechnologyNotFoundError:
    #                 continue
    #         for alias in REGIONAL_TECH_ALIAS_MAP.get(base_name, ()):
    #             alias_base = _localized_name(alias, district_id)
    #             alias_names = self._clone_names(alias_base)
    #             for extra_name in alias_names[1:]:
    #                 try:
    #                     registry.remove(extra_name)
    #                 except TechnologyNotFoundError:
    #                     continue
    #
    #         if unlimited:
    #             self._ensure_clone(
    #                 base_name=base_name,
    #                 base_tech=base_tech,
    #                 district_id=district_id,
    #                 unit_idx=0,
    #                 suffix=suffix,
    #                 unlimited=True,
    #                 total_units=None,
    #             )
    #             continue
    #
    #         self._ensure_clone(
    #             base_name=base_name,
    #             base_tech=base_tech,
    #             district_id=district_id,
    #             unit_idx=0,
    #             suffix=suffix,
    #             unlimited=False,
    #             total_units=units,
    #         )
    #
    # def _clone_names(self, localized_base: str) -> list[str]:
    #     registry = self.technology_registry
    #     names: list[str] = []
    #     idx = 0
    #     while True:
    #         name = f"{localized_base}{_unit_suffix(idx)}"
    #         if not registry.has_technology(name):
    #             break
    #         names.append(name)
    #         idx += 1
    #     return names
    #
    # def _ensure_clone(
    #         self,
    #         base_name: str,
    #         base_tech: Technology,
    #         district_id: int,
    #         unit_idx: int,
    #         suffix: str,
    #         unlimited: bool,
    #         total_units: int | None,
    # ) -> None:
    #     registry = self.technology_registry
    #     localized_base = _localized_name(base_name, district_id)
    #     suffix_unit = _unit_suffix(unit_idx)
    #     localized_name = f"{localized_base}{suffix_unit}"
    #
    #     commodity_in = base_tech.commodity_in
    #     commodity_out = base_tech.commodity_out
    #
    #     if base_name == "heat_grid":
    #         commodity_in = f"district_heat_in{suffix}"
    #         commodity_out = f"district_heat_out{suffix}"
    #     elif _is_central_base(base_name):
    #         commodity_out = f"district_heat_in{suffix}"
    #     elif base_name == "heat_exchanger":
    #         commodity_in = f"district_heat_out{suffix}"
    #
    #     cap_max_value = None if unlimited else base_tech.cap_max
    #     max_units_value = None if unlimited else total_units
    #     cap_min_value = base_tech.cap_min
    #     availability = base_tech.availability_profile
    #     capex_base = base_tech.capex_cost_base
    #
    #     if registry.has_technology(localized_name):
    #         clone = registry.get_by_name(localized_name)
    #         clone.commodity_in = commodity_in
    #         clone.commodity_out = commodity_out
    #         clone.efficiency = base_tech.efficiency
    #         clone.technical_lifetime = base_tech.technical_lifetime
    #         clone.opex_cost_energy = base_tech.opex_cost_energy
    #         clone.opex_cost_power = base_tech.opex_cost_power
    #         clone.capex_cost_power = base_tech.capex_cost_power
    #         clone.capex_cost_base = capex_base
    #         clone.cap_min = cap_min_value
    #         clone.cap_max = cap_max_value
    #         clone.max_units = max_units_value
    #         clone.availability_profile = availability
    #         clone.stage = base_tech.stage
    #         clone.category = base_tech.category
    #     else:
    #         clone = Technology(
    #             name=localized_name,
    #             commodity_in=commodity_in,
    #             commodity_out=commodity_out,
    #             efficiency=base_tech.efficiency,
    #             technical_lifetime=base_tech.technical_lifetime,
    #             opex_cost_energy=base_tech.opex_cost_energy,
    #             opex_cost_power=base_tech.opex_cost_power,
    #             capex_cost_power=base_tech.capex_cost_power,
    #             capex_cost_base=capex_base,
    #             cap_min=cap_min_value,
    #             cap_max=cap_max_value,
    #             max_units=max_units_value,
    #             availability_profile=availability,
    #             stage=base_tech.stage,
    #             category=base_tech.category,
    #         )
    #         registry.register(clone)
    #
    #     for alias in REGIONAL_TECH_ALIAS_MAP.get(base_name, ()):  # legacy aliases for compatibility
    #         alias_base = _localized_name(alias, district_id)
    #         alias_name = f"{alias_base}{suffix_unit}"
    #         if registry.has_technology(alias_name):
    #             alias_clone = registry.get_by_name(alias_name)
    #             alias_clone.commodity_in = clone.commodity_in
    #             alias_clone.commodity_out = clone.commodity_out
    #             alias_clone.efficiency = clone.efficiency
    #             alias_clone.technical_lifetime = clone.technical_lifetime
    #             alias_clone.opex_cost_energy = clone.opex_cost_energy
    #             alias_clone.opex_cost_power = clone.opex_cost_power
    #             alias_clone.capex_cost_power = clone.capex_cost_power
    #             alias_clone.capex_cost_base = clone.capex_cost_base
    #             alias_clone.cap_min = clone.cap_min
    #             alias_clone.cap_max = clone.cap_max
    #             alias_clone.max_units = clone.max_units
    #             alias_clone.availability_profile = clone.availability_profile
    #             alias_clone.stage = clone.stage
    #             alias_clone.category = clone.category
    #         else:
    #             alias_clone = Technology(
    #                 name=alias_name,
    #                 commodity_in=clone.commodity_in,
    #                 commodity_out=clone.commodity_out,
    #                 efficiency=clone.efficiency,
    #                 technical_lifetime=clone.technical_lifetime,
    #                 opex_cost_energy=clone.opex_cost_energy,
    #                 opex_cost_power=clone.opex_cost_power,
    #                 capex_cost_power=clone.capex_cost_power,
    #                 capex_cost_base=clone.capex_cost_base,
    #                 cap_min=clone.cap_min,
    #                 cap_max=clone.cap_max,
    #                 max_units=clone.max_units,
    #                 availability_profile=clone.availability_profile,
    #                 stage=clone.stage,
    #                 category=clone.category,
    #             )
    #             registry.register(alias_clone)
    #
    # def build_technologies(self, topology: nx.Graph, r_demands: list[RegionDemand], district_id: int) -> list[
    #     RegionTechnology]:
    #     collection: list[RegionTechnology] = []
    #     technologies_with_shares: set[str] = set()
    #     central_seed_outputs: dict[str, float] = {}
    #     central_seed_profile_peaks: dict[str, float] = {}
    #     registry = self.technology_registry
    #     raw_names = registry.get_all(return_type="name")
    #     all_regional_tokens = set(_all_regional_base_tokens(registry))
    #     regional_base_names = _regional_base_names(registry)
    #     regional_base_set = set(regional_base_names)
    #     all_technologies: list[str] = []
    #     seen_names: set[str] = set()
    #     for name in raw_names:
    #         if _is_excluded(name):
    #             continue
    #         if name in all_regional_tokens:
    #             continue
    #         if _is_other_district_clone(name, district_id):
    #             continue
    #         canonical_name = _canonical_regional_name(name)
    #         if _is_excluded(canonical_name):
    #             continue
    #         lookup_name = canonical_name if registry.has_technology(canonical_name) else name
    #         if lookup_name in seen_names:
    #             continue
    #         seen_names.add(lookup_name)
    #         all_technologies.append(lookup_name)
    #     localized_map = {base: _localized_name(base, district_id) for base in regional_base_names}
    #     # Technologies which supply demands
    #     for r_demand in r_demands:
    #         if r_demand.demand.technology_shares_query_params is None:
    #             continue
    #         # if r_demand.demand.technology_shares_query_params:
    #         technologies_supplying = registry.get_by_output_commodity(
    #             commodity=r_demand.demand.commodity_in,
    #             return_type="name",
    #         )
    #         localized_suppliers = []
    #         for tech_name in technologies_supplying:
    #             canonical_name = _canonical_regional_name(tech_name)
    #             if _is_other_district_clone(canonical_name, district_id):
    #                 continue
    #             base_name = canonical_name.rsplit("_D", 1)[0] if "_D" in canonical_name else canonical_name
    #             if base_name in regional_base_set and canonical_name == base_name:
    #                 localized_suppliers.append(localized_map[base_name])
    #             else:
    #                 localized_suppliers.append(canonical_name)
    #         technologies_supplying_this_demand = localized_suppliers
    #
    #         base_query = {"region": topology, "base_crs": self.base_crs}
    #         technology_shares_data = self.data_registry.query(
    #             r_demand.demand.technology_shares_query_params | base_query)
    #
    #         model_tech_shares = {}
    #         for tech_name, share in technology_shares_data.items():
    #             if not tech_name:
    #                 continue
    #             canonical_name = _canonical_regional_name(tech_name)
    #             if _is_other_district_clone(canonical_name, district_id):
    #                 continue
    #             base_name = canonical_name.rsplit("_D", 1)[0] if "_D" in canonical_name else canonical_name
    #             if base_name in regional_base_set and canonical_name == base_name:
    #                 mapped = localized_map[base_name]
    #             else:
    #                 mapped = canonical_name
    #             if mapped in technologies_supplying_this_demand:
    #                 model_tech_shares[mapped] = share
    #
    #         normalized_shares = normalize_shares_or_zero(model_tech_shares)
    #
    #         if not any(share > 0.0 for share in normalized_shares.values()):
    #             normalized_shares = {tech: 0.0 for tech in model_tech_shares}
    #             if r_demand.demand.default_supply_technology:
    #                 default_tech = r_demand.demand.default_supply_technology
    #                 canonical_default = _canonical_regional_base(default_tech)
    #                 if canonical_default in regional_base_set:
    #                     default_tech = localized_map[canonical_default]
    #                 if default_tech not in normalized_shares.keys():
    #                     raise ValueError(
    #                         f"Default supply technology {r_demand.demand.default_supply_technology} not found in "
    #                         f"the technology shares for demand {r_demand.demand.demand_type}."
    #                     )
    #                 normalized_shares[default_tech] = 1.0
    #
    #         for tech, share in normalized_shares.items():
    #             initial_energy_output = r_demand.value * share
    #             region_technology = RegionTechnology(
    #                 technology=self.technology_registry.get_by_name(tech),
    #                 initial_energy_output=initial_energy_output,
    #                 initial_capacity=max(r_demand.profile) * initial_energy_output * self.config[
    #                     "cap_factor_ind_technologies"],
    #                 output_profile=r_demand.profile,
    #             )
    #             collection.append(region_technology)
    #             technologies_with_shares.add(tech)
    #
    #             localized_heat_exchanger = localized_map.get("heat_exchanger")
    #             if (
    #                     localized_heat_exchanger
    #                     and tech == localized_heat_exchanger
    #                     and initial_energy_output > 0.0
    #             ):
    #                 central_seed_base = self._resolve_dhn_central_seed_base(topology, district_id)
    #                 localized_central_default = localized_map.get(central_seed_base)
    #                 if not localized_central_default:
    #                     raise ValueError(
    #                         f"Missing localized central technology '{central_seed_base}' for district {district_id}"
    #                     )
    #                 if not self.technology_registry.has_technology(localized_central_default):
    #                     raise ValueError(
    #                         f"Technology registry missing '{localized_central_default}' for district {district_id}"
    #                     )
    #                 central_seed_outputs[localized_central_default] = (
    #                         central_seed_outputs.get(localized_central_default, 0.0) + float(initial_energy_output)
    #                 )
    #                 central_seed_profile_peaks[localized_central_default] = max(
    #                     central_seed_profile_peaks.get(localized_central_default, 0.0),
    #                     float(max(r_demand.profile)),
    #                 )
    #
    #     for central_name, seeded_output in central_seed_outputs.items():
    #         profile_peak = central_seed_profile_peaks.get(central_name, 0.0)
    #         seeded_capacity = profile_peak * seeded_output * self.config["cap_factor_ind_technologies"]
    #         existing = next((item for item in collection if item.technology.name == central_name), None)
    #         if existing is not None:
    #             existing.initial_energy_output += seeded_output
    #             existing.initial_capacity += seeded_capacity
    #         else:
    #             collection.append(
    #                 RegionTechnology(
    #                     technology=self.technology_registry.get_by_name(central_name),
    #                     initial_energy_output=seeded_output,
    #                     initial_capacity=seeded_capacity,
    #                     output_profile=None,
    #                 )
    #             )
    #         technologies_with_shares.add(central_name)
    #
    #     other_technologies = set(all_technologies) - technologies_with_shares
    #     for tech_name in other_technologies:
    #         tech = self.technology_registry.get_by_name(tech_name)
    #         initial_output = 0.0
    #         initial_capacity = 0.0
    #         profile = None
    #         region_technology = RegionTechnology(
    #             technology=tech,
    #             initial_energy_output=initial_output,
    #             initial_capacity=initial_capacity,
    #             output_profile=profile
    #         )
    #         collection.append(region_technology)
    #
    #     return collection
    #
    # def build(self, topology: nx.Graph, region_id: int) -> Region:
    #     demands = self.build_demands(topology)
    #     self._ensure_regional_assets(region_id)
    #     technologies = self.build_technologies(topology, demands, region_id)
    #
    #     region = Region(
    #         id_=region_id,
    #         topology=topology,
    #         region_technologies=technologies,
    #         region_demands=demands,
    #     )
    #
    #     if self.rule_book:
    #         region = self.rule_book.apply(region)
    #
    #     return region
