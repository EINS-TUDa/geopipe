from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from .technology_stage import (
    TechnologyStage,
    TechnologyCategory,
    DEFAULT_STAGE,
    DEFAULT_CATEGORY,
)


class Technology:
    def __init__(self,
                 name: str,
                 commodity_in: str,
                 commodity_out: str,
                 efficiency: float = 1.0,
                 technical_lifetime: int = 100,
                 opex_cost_energy: float = 0,
                 opex_cost_power: float = 0,
                 capex_cost_power: float = 0,
                 capex_cost_base: float = 0,
                 spec_co2: float | None = None,
                 is_storage: bool = False,
                 c_rate: float | None = None,
                 efficiency_charge: float | None = None,
                 pipe_capex_eur_per_km: float | None = None,
                 cap_min: float | None = None,
                 cap_max: float | None = None,
                 max_units: int | None = None,
                 out_frac_min: float | None = None,
                 out_frac_max: float | None = None,
                 in_frac_min: float | None = None,
                 in_frac_max: float | None = None,
                 availability_profile: Optional[pd.Series] = None,
                 output_profile: Optional[pd.Series] = None,
                 stage: TechnologyStage | str = DEFAULT_STAGE,
                 category: TechnologyCategory | str = DEFAULT_CATEGORY):
        self.name = name
        self.commodity_in = commodity_in
        self.commodity_out = commodity_out
        self.efficiency = efficiency
        self.technical_lifetime = technical_lifetime
        self.opex_cost_energy = opex_cost_energy
        self.opex_cost_power = opex_cost_power
        self.capex_cost_power = capex_cost_power
        self.capex_cost_base = capex_cost_base
        self.spec_co2 = spec_co2
        self.is_storage = is_storage
        self.c_rate = c_rate
        self.efficiency_charge = efficiency_charge
        self.pipe_capex_eur_per_km = pipe_capex_eur_per_km
        self.cap_min = cap_min
        self.cap_max = cap_max
        if max_units is None:
            self.max_units = None
        else:
            try:
                self.max_units = max(1, int(max_units))
            except Exception:
                self.max_units = None
        self.out_frac_min = out_frac_min
        self.out_frac_max = out_frac_max
        self.in_frac_min = in_frac_min
        self.in_frac_max = in_frac_max
        self.availability_profile = availability_profile
        self.output_profile = output_profile
        self.stage = TechnologyStage(stage) if not isinstance(stage, TechnologyStage) else stage
        self.category = TechnologyCategory(category) if not isinstance(category, TechnologyCategory) else category

    def copy_with(self, **overrides) -> "Technology":
        """Return a new Technology with the same attributes overridden by kwargs."""
        payload = {
            "name": self.name,
            "commodity_in": self.commodity_in,
            "commodity_out": self.commodity_out,
            "efficiency": self.efficiency,
            "technical_lifetime": self.technical_lifetime,
            "opex_cost_energy": self.opex_cost_energy,
            "opex_cost_power": self.opex_cost_power,
            "capex_cost_power": self.capex_cost_power,
            "capex_cost_base": self.capex_cost_base,
            "spec_co2": self.spec_co2,
            "is_storage": self.is_storage,
            "c_rate": self.c_rate,
            "efficiency_charge": self.efficiency_charge,
            "pipe_capex_eur_per_km": self.pipe_capex_eur_per_km,
            "cap_min": self.cap_min,
            "cap_max": self.cap_max,
            "max_units": self.max_units,
            "out_frac_min": self.out_frac_min,
            "out_frac_max": self.out_frac_max,
            "in_frac_min": self.in_frac_min,
            "in_frac_max": self.in_frac_max,
            "availability_profile": self.availability_profile,
            "output_profile": self.output_profile,
            "stage": self.stage,
            "category": self.category,
        }
        payload.update(overrides)
        return Technology(**payload)


DHN_TECH_BASE_NAMES: tuple[str, ...] = (
    "heat_exchanger",
    "ind_district_heating_connection",
    "heat_grid",
)
INDIRECT_TECH_PREFIX: str = "ind_"
CENTRAL_TECH_PREFIX: str = "cen_"


def extract_district_id_from_name(name: str | None) -> Optional[int]:
    if not name:
        return None
    text = str(name)
    if "_D" not in text:
        return None
    suffix = text.rsplit("_D", 1)[-1]
    digits: list[str] = []
    for char in suffix:
        if char.isdigit():
            digits.append(char)
        else:
            break
    if not digits:
        return None
    try:
        return int("".join(digits))
    except ValueError:
        return None


def split_base_and_district(name: str | None) -> tuple[str, Optional[int]]:
    if not name:
        return "", None
    district = extract_district_id_from_name(name)
    if district is None:
        return str(name), None
    return str(name).rsplit("_D", 1)[0], district


def is_central_heat_supply(name: str | None) -> bool:
    base, _ = split_base_and_district(name)
    return bool(base) and base.startswith(CENTRAL_TECH_PREFIX)

@dataclass
class RegionTechnology:
    technology: Technology
    initial_energy_output: float  = 0
    initial_capacity: float = 0
    output_profile: pd.Series | None = None


@dataclass
class TechnologyRequirement:
    technology_name: str
    capacity_factor: float = 1.0  # Capacity of this technology = capacity_factor * capacity of the demand
    share: float = 1.0  # share of capacity of dependency technology that is covered by this technology


@dataclass
class TechnologyDependencyManager:
    """Manages dependencies between technologies."""
    # Dictionary: technology_name -> List of required technologies
    dependencies: dict[str, list[TechnologyRequirement]] = field(default_factory=dict)

    def add_dependency(self, source_tech: str, requirement: TechnologyRequirement) -> None:
        if source_tech not in self.dependencies:
            self.dependencies[source_tech] = []
        self.dependencies[source_tech].append(requirement)

    def get_requirements(self, technology_name: str) -> list[TechnologyRequirement]:
        return self.dependencies.get(technology_name, [])
