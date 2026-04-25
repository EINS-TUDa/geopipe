from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
import networkx as nx
import pandas as pd
from shapely.geometry import MultiPoint

from pypeline.energy_technology.technology_registry import TechnologyRegistry
from pypeline.energy_system_my.imports import Imports
from pypeline.energy_system_my.pipe import Pipe
from pypeline.energy_system_my.region import Region, DemandType
from pypeline.units import Unit

from __future__ import annotations
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

import logging

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
    pipes: list[Pipe] = field(default_factory=list)


class EnergySystemBuilder:

    def __init__(self, energy_system_name: str = "Default", base_crs: str = "EPSG:25832"):
        self.energy_system_name = energy_system_name
        self.base_crs = base_crs
        self._system_topology: Optional[Topology] = None
        # self.rule_book: EnergySystemRuleBook | None = None
        # self.region_rule_book: RegionRuleBook | None = None
        self._data_registry: DataRegistry | None = None
        self._technology_registry: TechnologyRegistry | None = None
        self._unit: Unit = UnitEnum.GW.unit
        # self.region_builder_config: dict[str, Any] | None = None
        self._demand_types: list[DemandType] = []
        # self.imports: list[Imports] | None = None
        # self.pipe_technology_name: str = "heat_pipe"
        # self._dhn_central_seed_cache: dict[int, str] = {}
        self.__region_topologies = None

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

    def set_region_builder_config(self, config: dict[str, Any] | None, *, merge: bool = True):
        if config is None:
            return self
        if not isinstance(config, dict):
            raise TypeError("region_builder_config must be a mapping")

        if self.region_builder_config is None or not merge:
            self.region_builder_config = dict(config)
        else:
            merged = dict(self.region_builder_config)
            merged.update(config)
            self.region_builder_config = merged
        return self

    @property
    def _region_topologies(self):
        if self.__region_topologies is None:
            self.__region_topologies = self._system_topology.sub_topologies_by_edge_property("id")
        return self.__region_topologies

    def _build_demands(self, topology: nx.Graph) -> list[Demand]:
        collection = []
        for demand_type in self._demand_types:
            # base_query = {"region": topology, "base_crs": self.base_crs}
            profile = pd.read_csv(demand_type.profile_path)
            if profile is None or profile.empty:
                raise ValueError(f"Demand profile for {demand_type.name} not found in data registry.")

            value = sum(self._system_topology.property_from_edges(demand_type.demand_column_name))
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

    def _build_decentralized(self):
        ...

    def build(self) -> EnergySystem:
        if not self._demand_types:
            logger.info("No demand types given. Using the default values.")
            self.add_demand_types(*self._default_demand_types())

        self.verify()

        demands: dict[int, list[Demand]] = {}
        decentralized = {}
        for region_id, topology in self._region_subgraphs().items():
            demands[region_id] = self._build_demands(topology)
            decentralized[region_id] = self._build_decentralized()







        self._dhn_central_seed_cache = {}

        rb = RegionBuilder(
            base_crs=self.base_crs,
            data_registry=self.data_registry,
            technology_registry=self.technology_registry,
        )
        rb.set_demands(self.demands)
        if not self.region_builder_config:
            self.set_default_region_builder_config()
        rb.set_config(self.region_builder_config)
        rb.set_dhn_central_seed_base_resolver(self._infer_dhn_central_seed_base)

        self._ensure_heat_grid_rules()
        if self.region_rule_book:
            rb.set_rule_book(self.region_rule_book)

        regions: list[Region] = []
        for region_id, topology in self._region_subgraphs():
            regions.append(rb.build(topology=topology, region_id=region_id))

        pipe_tech = self.technology_registry.get_by_name(self.pipe_technology_name)
        for region in regions:
            cost = build_district_heat_grid_from_topology(
                topology=region.topology,
                pipe_capex_eur_per_km=float(pipe_tech.pipe_capex_eur_per_km),
            )
            region.local_dhn_capex_base_eur = float(cost["local_grid_capex_base_eur"])

        pipes = []
        if len(regions) > 1:
            distance_threshold_m = (self.region_builder_config or {}).get("interdistrict_free_pipe_max_length_m")
            pipe_eff = max(0.0, min(1.0, float(pipe_tech.efficiency)))
            pipes = build_inter_dhn_pipes_from_topologies(
                regions=regions,
                full_network=self.street_network,
                pipe_capex_eur_per_km=float(pipe_tech.pipe_capex_eur_per_km),
                below_distance_threshold_m=(None if distance_threshold_m is None else float(distance_threshold_m)),
                pipe_loss_fraction=max(0.0, 1.0 - pipe_eff),
                pipe_capex_eur_per_mw=float(pipe_tech.capex_cost_power),
                pipe_opex_eur_per_mwh=float(pipe_tech.opex_cost_energy),
                pipe_cap_max_mw=float(pipe_tech.cap_max),
                pipe_lifetime_years=int(pipe_tech.technical_lifetime),
            )

        es = EnergySystem(
            name=self.energy_system_name,
            regions=regions,
            street_network=self.street_network,
            units=self.unit,
            technology_registry=self.technology_registry,
            imports=self.imports,
            pipes=pipes,
        )

        if self.rule_book:
            es = self.rule_book.apply(es)

        return es

    def verify(self):
        if not isinstance(self.base_crs, str):
            raise ValueError(f"Base CRS must be set and a string and not {type(self.base_crs)}")
        if not isinstance(self.street_network, nx.Graph):
            raise ValueError("street_network must be set as a NetworkX graph before building")
        region_ids = set()
        for _, _, d in self.street_network.edges(data=True):
            raw = d.get("id")
            try:
                region_ids.add(int(float(raw)))
            except (TypeError, ValueError):
                pass
        if not region_ids:
            raise ValueError(
                "street_network has no edges with a valid 'id' attribute. "
                "Every edge must carry an integer 'id' indicating its region. "
                "Use set_street_network() with a graph produced by gdf_to_nx() after "
                "assigning region ids to the street GeoDataFrame."
            )
        if self.rule_book is not None and not isinstance(self.rule_book, EnergySystemRuleBook):
            raise ValueError(f"RuleBook must be None or EnergySystemRuleBook and not {type(self.rule_book)}")
        if not isinstance(self.data_registry, DataRegistry):
            raise ValueError(f"DataRegistry must be set and of type DataRegistry and not {type(self.data_registry)}")
        if not isinstance(self.technology_registry, TechnologyRegistry):
            raise ValueError(
                f"TechnologyRegistry must be set and of type TechnologyRegistry and not {type(self.technology_registry)}"
            )
        if not self.imports:
            raise ValueError("Imports must be set using set_imports() with a non-empty imports.yaml")
