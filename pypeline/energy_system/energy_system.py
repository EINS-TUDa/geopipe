from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence
import geopandas as gpd
import pandas as pd
import yaml

from pypeline.data.data_registry import DataRegistry
from pypeline.data.dataset import CensusTechnology
from pypeline.energy_system.demand import Demand
from pypeline.energy_system.region import Region, RegionBuilder
from pypeline.energy_system.region_connection import RegionConnection
from pypeline.energy_system.rule_book import (
    EnergySystemRuleBook,
    HeatExchangerCostAdjustmentRule,
    MinimumHeatGridConstraintRule,
    MinimumHeatGridOutputRule,
    RegionRuleBook,
)
from pypeline.energy_system.unit import Unit, UnitEnum
from pypeline.energy_technology.technology import (
    TechnologyDependencyManager,
    TechnologyRequirement,
)
from pypeline.energy_technology.technology_registry import TechnologyRegistry


__all__ = (
    "EnergySystem",
    "EnergySystemBuilder",
    "JsonEnergySystemBuilder",
    "NameEnergySystemBuilder",
)

HEAT_EXCHANGER_NAMES: tuple[str, ...] = ("heat_exchanger", "ind_district_heating_connection")
PRIMARY_HEAT_EXCHANGER: str = HEAT_EXCHANGER_NAMES[0]


@dataclass(slots=True)
class EnergySystem:
    name: str
    regions: list[Region]
    units: Unit
    connections: list[RegionConnection]
    commodity_config: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, dict[int, float]] = field(default_factory=dict)
    data_dir: str | Path | None = None

class EnergySystemBuilder:
    def __init__(self, energy_system_name: str = "Default", base_crs: str = "EPSG:25832"):
        self.energy_system_name = energy_system_name
        self.base_crs = base_crs
        self.polygons: gpd.GeoDataFrame | None = None
        self.rule_book: EnergySystemRuleBook | None = None
        self.region_rule_book: RegionRuleBook | None = None
        self.data_registry: DataRegistry | None = None
        self.technology_registry: TechnologyRegistry | None = None
        self.connections: list[RegionConnection] | None = None
        self.unit: Unit = UnitEnum.GW.unit
        self.technology_dependency_manager: TechnologyDependencyManager | None = None
        self.region_builder_config: dict[str, Any] | None = None
        self.demands: list[Demand] | None = None
        self.commodity_config: dict[str, Any] | None = self._load_default_commodity_config()

    def set_unit(self, input_unit: UnitEnum):
        self.unit = input_unit.unit
        return self

    def set_polygons(self, polygons: gpd.GeoDataFrame):
        if "id" not in polygons.columns or polygons["id"].isnull().any():
            polygons = polygons.copy()
            if "id" not in polygons.columns:
                polygons["id"] = range(len(polygons))
            else:
                missing = polygons["id"].isnull()
                max_existing_id = polygons["id"].dropna().max()
                next_id = 0 if pd.isna(max_existing_id) else int(max_existing_id) + 1
                polygons.loc[missing, "id"] = range(next_id, next_id + missing.sum())
        self.polygons = polygons.to_crs(self.base_crs)
        return self

    def set_region_rule_book(self, rule_book: RegionRuleBook):
        self.region_rule_book = rule_book
        return self

    def set_energy_system_rule_book(self, rule_book: EnergySystemRuleBook):
        self.rule_book = rule_book
        return self

    def set_data_registry(self, data_registry: DataRegistry):
        self.data_registry = data_registry
        return self

    def set_technology_registry(self, technology_registry: TechnologyRegistry):
        self.technology_registry = technology_registry
        return self

    def set_demands(self, demands: list[Demand] | None = None, default: bool = False):
        if default:
            self._set_default_demands()
            return self
        if not isinstance(demands, list) or not all(isinstance(d, Demand) for d in demands):
            raise TypeError("demands must be a list of Demand instances.")
        self.demands = demands
        return self

    def set_technology_dependency_manager(
        self,
        manager: TechnologyDependencyManager | None = None,
        default: bool = False,
    ):
        if default:
            self._set_default_technology_dependency_manager()
            return self
        if not isinstance(manager, TechnologyDependencyManager):
            raise TypeError("manager must be an instance of TechnologyDependencyManager.")
        self.technology_dependency_manager = manager
        return self

    def _set_default_demands(self):
        self.demands = [
            Demand(
                demand_type="residential_heat",
                commodity_in="residential_heat",
                cooperation_of_technologies=False,
                demand_query_params={"key": "residential_heat_demand"},
                profile_query_params={"key": "residential_heat_demand_profile"},
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
                        CensusTechnology.District_Heating: PRIMARY_HEAT_EXCHANGER,
                        CensusTechnology.NoEnergyCarrier: None,
                    },
                },
                default_supply_technology="ind_oil_boiler",
            ),
            Demand(
                demand_type="residential_electricity",
                commodity_in="electricity",
                cooperation_of_technologies=True,
                demand_query_params={"key": "residential_electricity_demand"},
                profile_query_params={"key": "residential_electricity_demand_profile"},
                default_supply_technology=None,
            ),
        ]

    def set_connections(self, connections: Sequence[RegionConnection]):
        self.connections = list(connections)
        return self

    def build_connections(self) -> list[RegionConnection]:
        """
        Different logics should be implemented here to build connections between regions.
        1. If a region touches another region, a connection should be created.
        2. If there is a connection via a street, a connection should be created.
        Or: Take user defined connections from a file.

        Also, it should be possible to define the commodities that are transported via the connection.
        """
        return []

    def _set_default_technology_dependency_manager(self):
        dependencies = {
            PRIMARY_HEAT_EXCHANGER: [
                TechnologyRequirement(technology_name="heat_grid", capacity_factor=1.5)
            ],
            "heat_grid": [
                TechnologyRequirement(technology_name="cen_heat_pump", share=0.5, capacity_factor=1.05)
            ]
        }
        self.technology_dependency_manager = TechnologyDependencyManager(dependencies=dependencies)

    def _load_default_commodity_config(self) -> dict[str, Any]:
        root = Path(__file__).resolve().parents[1]
        candidates = [root / "energy_technology" / "configs" / "commodities.yaml", root.parent / "configs" / "commodities.yaml"]
        for candidate in candidates:
            if candidate.exists():
                cfg = yaml.safe_load(candidate.read_text(encoding="utf-8"))
                if isinstance(cfg, dict):
                    return cfg
        return {}

    def set_commodity_config(self, config: dict[str, Any] | None):
        if config is None:
            self.commodity_config = {}
            return self
        if not isinstance(config, dict):
            raise TypeError("commodity_config must be a mapping")
        self.commodity_config = dict(config)
        return self

    def set_commodity_prices(
        self,
        *,
        grid_prices: dict[str, Any],
        supply_prices_eur_per_mwh: dict[str, Any],
        edge_defaults: dict[str, Any] | None = None,
    ):
        if not isinstance(grid_prices, dict):
            raise TypeError("grid_prices must be a mapping")
        if not isinstance(supply_prices_eur_per_mwh, dict):
            raise TypeError("supply_prices_eur_per_mwh must be a mapping")
        config: dict[str, Any] = {
            "grid_prices": dict(grid_prices),
            "supply_prices_eur_per_mwh": dict(supply_prices_eur_per_mwh),
        }
        if edge_defaults is not None:
            if not isinstance(edge_defaults, dict):
                raise TypeError("edge_defaults must be a mapping if provided")
            config["edge_defaults"] = dict(edge_defaults)
        self.commodity_config = config
        return self

    def set_default_region_builder_config(self):
        self.region_builder_config = {
            "cap_factor_ind_technologies": 1.1,  # Factor to increase the capacity of individual technologies over the minimum required capacity
            "min_heat_grid_output_mwh": 0.0,
            "min_heat_grid_share": None,
            "heat_grid_demand_name": "residential_heat",
            "heat_grid_names": HEAT_EXCHANGER_NAMES,
            "heat_grid_cost_dataset": "residential_heat_technology_shares",
            "heat_grid_cost_scaling": 1.0,
            "heat_grid_cost_min_factor": 1.0,
            "heat_grid_decentralized_keys": tuple(
                ct.value for ct in CensusTechnology if ct is not CensusTechnology.District_Heating
            ),
        }
        return self

    def _heat_grid_rule_settings(self) -> tuple[float, float | None, str, tuple[str, ...]] | None:
        config = self.region_builder_config or {}
        min_output = float(config.get("min_heat_grid_output_mwh", 0.0) or 0.0)
        min_share_raw = config.get("min_heat_grid_share")
        demand_name = config.get("heat_grid_demand_name", "residential_heat")
        heat_grid_names = config.get("heat_grid_names", HEAT_EXCHANGER_NAMES)

        min_share = None
        if min_share_raw is not None:
            min_share = float(min_share_raw)

        if min_output <= 0.0 and not (min_share is not None and min_share > 0.0):
            return None

        return min_output, min_share, demand_name, tuple(heat_grid_names)

    def _heat_grid_cost_settings(self) -> tuple[str, float, float, tuple[str, ...]]:
        config = self.region_builder_config or {}
        dataset = config.get("heat_grid_cost_dataset", "residential_heat_technology_shares")
        scale = float(config.get("heat_grid_cost_scaling", 1.0))
        min_factor = float(config.get("heat_grid_cost_min_factor", 1.0))
        raw_keys = config.get("heat_grid_decentralized_keys")
        if raw_keys:
            decentralized = tuple(str(key) for key in raw_keys)
        else:
            decentralized = tuple(
                ct.value for ct in CensusTechnology if ct is not CensusTechnology.District_Heating
            )
        return dataset, scale, min_factor, decentralized

    def _ensure_heat_grid_rules(self) -> None:
        settings = self._heat_grid_rule_settings()
        if not settings:
            return

        min_output, min_share, demand_name, heat_grid_names = settings

        if self.region_rule_book is None:
            self.region_rule_book = RegionRuleBook()
        if not any(isinstance(rule, MinimumHeatGridOutputRule) for rule in getattr(self.region_rule_book, "rules", [])):
            self.region_rule_book.add_rule(
                MinimumHeatGridOutputRule(
                    demand_name=demand_name,
                    min_output_mwh=min_output,
                    min_share=min_share,
                    heat_grid_names=heat_grid_names,
                )
            )

        if self.rule_book is None:
            self.rule_book = EnergySystemRuleBook()
        if not any(isinstance(rule, MinimumHeatGridConstraintRule) for rule in getattr(self.rule_book, "rules", [])):
            self.rule_book.add_rule(
                MinimumHeatGridConstraintRule(
                    demand_name=demand_name,
                    min_output_mwh=min_output,
                    min_share=min_share,
                    heat_grid_names=heat_grid_names,
                )
            )

        dataset, scale, min_factor, decentralized_keys = self._heat_grid_cost_settings()
        if (
            self.data_registry
            and not any(isinstance(rule, HeatExchangerCostAdjustmentRule) for rule in getattr(self.region_rule_book, "rules", []))
        ):
            self.region_rule_book.add_rule(
                HeatExchangerCostAdjustmentRule(
                    data_registry=self.data_registry,
                    dataset_type=dataset,
                    heat_exchanger_names=heat_grid_names,
                    decentralized_keys=decentralized_keys,
                    scale_factor=scale,
                    min_factor=min_factor,
                )
            )

    def build(self) -> EnergySystem:
        # verify the required attributes are set
        self.verify()

        # setup region builder
        rb = RegionBuilder(
            base_crs=self.base_crs,
            data_registry=self.data_registry,
            technology_registry=self.technology_registry)
        rb.set_technology_dependency_manager(self.technology_dependency_manager)
        rb.set_demands(self.demands)
        if not self.region_builder_config:
            self.set_default_region_builder_config()
        rb.set_config(self.region_builder_config)

        self._ensure_heat_grid_rules()
        if self.region_rule_book:
            rb.set_rule_book(self.region_rule_book)

        # build regions from polygons
        regions: list[Region] = []
        for row in self.polygons.itertuples(index=False):
            polygon = gpd.GeoDataFrame([{"geometry": row.geometry, "id": getattr(row, "id")}],
                                       crs=self.polygons.crs)
            regions.append(rb.build(polygon=polygon))

        # build connections if not provided
        if self.connections is None:
            connections = self.build_connections()
        else:
            connections = self.connections

        # create the energy system
        es = EnergySystem(
            name=self.energy_system_name,
            regions=regions,
            units=self.unit,
            connections=connections,
            commodity_config=self.commodity_config or {},
        )

        # apply energy system rule book if set
        if self.rule_book:
            es = self.rule_book.apply(es)

        return es

    def verify(self):
        if not isinstance(self.base_crs, str):
            raise ValueError(f"Base CRS must be set and a string and not {type(self.base_crs)}")
        if not isinstance(self.polygons, gpd.GeoDataFrame):
            raise ValueError(f"Geometry must be set and a GeoDataFrame and not {type(self.polygons)}")
        if self.rule_book is not None and not isinstance(self.rule_book, EnergySystemRuleBook):
            raise ValueError(f"RuleBook must be None or EnergySystemRuleBook and not {type(self.rule_book)}")
        if not isinstance(self.data_registry, DataRegistry):
            raise ValueError(f"DataRegistry must be set and of type DataRegistry and not {type(self.data_registry)}")
        if not isinstance(self.technology_registry, TechnologyRegistry):
            raise ValueError(
                f"TechnologyRegistry must be set and of type TechnologyRegistry and not {type(self.technology_registry)}")
        if not self.connections:
            print("Warning: No connections set, building default connections.")
        # Technology Dependency Manager
        if self.technology_dependency_manager:
            for key, value in self.technology_dependency_manager.dependencies.items():
                if not self.technology_registry.has_technology(key):
                    raise ValueError(
                        f"Technology {key} in the TechnologyDependencyManager is not registered in the TechnologyRegistry.")
                for requirement in value:
                    if not self.technology_registry.has_technology(requirement.technology_name):
                        raise ValueError(
                            f"Technology {requirement.technology_name} in the TechnologyDependencyManager of {key} is not registered in the TechnologyRegistry.")


class JsonEnergySystemBuilder(EnergySystemBuilder):
    def set_geometry_from_json(self, json_file_path: str):
        self.polygons = gpd.read_file(json_file_path)
        self.polygons = self.polygons.to_crs(self.base_crs)


class NameEnergySystemBuilder(EnergySystemBuilder):
    def set_geometry_from_city_name(self, city_name: str):
        self.polygons = DataRegistry.fetch_data("GeoportalHessen", "CityBoundaries",
                                                {"city_name": city_name, "base_crs": self.base_crs})
        self.polygons = self.polygons.to_crs(self.base_crs)
