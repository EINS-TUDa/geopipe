from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any
import pandas as pd
import yaml


class Technology(ABC):
    _registered_types: dict[str, dict[str, Any]] = {}

    @abstractmethod
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._registered_types = {}

    @classmethod
    def register(cls, name: str,  **kwargs):
        if name in cls._registered_types:
            raise ValueError(f"Type {name} already exists")
        cls._registered_types[name] = kwargs

    @classmethod
    def clear_registered_types(cls):
        cls._registered_types.clear()

    @classmethod
    def _get_registered_type(cls, name: str):
        if name not in cls._registered_types:
            raise ValueError(f"Type of {name} does not exist.")
        return cls._registered_types[name]

    @classmethod
    def has_type(cls, name: str) -> bool:
        return name in cls._registered_types

    @classmethod
    def registered_type_names(cls) -> list[str]:
        return list(cls._registered_types.keys())

    @classmethod
    def get_type_defaults(cls, name: str) -> dict[str, Any]:
        return cls._get_registered_type(name).copy()

    @classmethod
    def get_type_names_by_attribute(cls, attribute: str, value: Any) -> tuple[str, ...]:
        """Return the names of registered types whose default ``attribute`` equals ``value``."""
        return tuple(name for name, data in cls._registered_types.items() if data.get(attribute) == value)


class DecentralTechnology(Technology):

    def __init__(self, name: str,
                 existing_capacity: float = 0.0,
                 output_profile_name: Optional[str] = None):
        super().__init__(name)
        registered_type = type(self)._get_registered_type(name)

        try:
            self.commodity_in: str = registered_type["commodity_in"]
            self.commodity_out: str = registered_type["commodity_out"]
            self.efficiency: float = registered_type["efficiency"]
            self.technical_lifetime: int = registered_type["technical_lifetime"]
            self.opex_cost_energy: float = registered_type["opex_cost_energy"]
            self.opex_cost_power: float = registered_type["opex_cost_power"]
            self.capex_cost_power: float = registered_type["capex_cost_power"]
        except KeyError:
            raise KeyError(f"The registered type '{name}' does not provide all the data for")

        self.existing_capacity = existing_capacity
        self.output_profile_name = output_profile_name

        # Years from model start by which existing_capacity has decreased linearly to 0.
        # Only required when existing_capacity is non-zero.
        self.existing_capacity_phase_out_years: float = registered_type.get("existing_capacity_phase_out_years")
        if existing_capacity and self.existing_capacity_phase_out_years is None:
            raise ValueError(
                f"Technology '{name}' has existing_capacity={existing_capacity} but the "
                f"registered type does not define 'existing_capacity_phase_out_years'.")

    def max_capacity_per_year(self, start_year: int) -> dict[int, float]:
        return {start_year: self.existing_capacity,
                start_year+1: None}

    def capacity_per_year(self, start_year: int) -> dict[int, float] | None:
        if not self.existing_capacity:
            return None
        return {start_year: self.existing_capacity,
                start_year + self.existing_capacity_phase_out_years: 0.0}


class CentralTechnology(Technology):
    """A CentralTechType instantiated in a specific region.

    Central techs can reference either an output profile (fixed dispatch)
    or an availability profile (max dispatch per hour), by name and/or by
    explicit series. Names are looked up in the data registry at build time.
    """

    def __init__(self, name: str,
                 existing_capacity: float = 0.0,
                 capacity_restriction: Optional[float | dict[int, float]] = None,
                 output_profile_name: Optional[str] = None,
                 output_profile: Optional[pd.Series] = None,
                 availability_profile_name: Optional[str] = None,
                 availability_profile: Optional[pd.Series] = None):
        super().__init__(name)
        registered_type = type(self)._get_registered_type(name)

        try:
            self.commodity_in: str = registered_type["commodity_in"]
            self.commodity_out: str = registered_type["commodity_out"]
            self.efficiency: float = registered_type["efficiency"]
            self.technical_lifetime: int = registered_type["technical_lifetime"]
            self.opex_cost_energy: float = registered_type["opex_cost_energy"]
            self.opex_cost_power: float = registered_type["opex_cost_power"]
            self.capex_cost_power: float = registered_type["capex_cost_power"]
            self.capex_cost_base: float = registered_type["capex_cost_base"]
        except KeyError:
            raise KeyError(f"The registered type '{name}' does not provide all the data for")

        self.existing_capacity = existing_capacity
        self._capacity_restriction = capacity_restriction
        self.output_profile_name = output_profile_name
        self.output_profile = output_profile
        self.availability_profile_name = availability_profile_name
        self.availability_profile = availability_profile

    def set_capacity_restriction(self, capacity_restriction: float | dict[int, float]):
        self._capacity_restriction = capacity_restriction

    def capacity_restriction(self, year_period: int):
        if isinstance(self._capacity_restriction, dict):
            return self._capacity_restriction[year_period]
        return self._capacity_restriction

    def capacity_restriction_per_year(self, start_year: int):
        if isinstance(self._capacity_restriction, dict):
            return {key + start_year: value for key, value in self._capacity_restriction.items()}
        return self._capacity_restriction


class CHPTechnology(Technology):
    """A CHPType instantiated in a specific region."""

    def __init__(self, name: str,
                 existing_capacity: float = 0.0,
                 output_profile_name: Optional[str] = None,
                 output_profile: Optional[pd.Series] = None,
                 availability_profile_name: Optional[str] = None,
                 availability_profile: Optional[pd.Series] = None):
        super().__init__(name)
        registered_type = type(self)._get_registered_type(name)

        try:
            ...  # todo: Fill when CHP-specific fields are defined
        except KeyError:
            raise KeyError(f"The registered type '{name}' does not provide all the data for")

        self.existing_capacity = existing_capacity
        self.output_profile_name = output_profile_name
        self.output_profile = output_profile
        self.availability_profile_name = availability_profile_name
        self.availability_profile = availability_profile




class GridTechnology(Technology):
    """A GridType instantiated in a specific region."""

    def __init__(self, name: str,
                 length_km: float,
                 existing_capacity: float = 0.0,):
        super().__init__(name)
        registered_type = type(self)._get_registered_type(name)

        try:
            self.commodity_in: str = registered_type["commodity_in"]
            self.commodity_out: str = registered_type["commodity_out"]
            self.efficiency: float = registered_type["efficiency"]
            self.capex_per_km: float = registered_type["capex_per_km"]
            self.technical_lifetime: int = registered_type["technical_lifetime"]
        except KeyError:
            raise KeyError(f"The registered type '{name}' does not provide all the data for")

        self.existing_capacity = existing_capacity
        self._length_km = length_km

        # Years from model start at which existing_capacity drops abruptly to 0 (no linear decay).
        # Only required when existing_capacity is non-zero.
        self.existing_capacity_retirement_years: float = registered_type.get("existing_capacity_retirement_years")
        if existing_capacity and self.existing_capacity_retirement_years is None:
            raise ValueError(
                f"Technology '{name}' has existing_capacity={existing_capacity} but the "
                f"registered type does not define 'existing_capacity_retirement_years'.")

    @property
    def investment_costs(self) -> float:
        return self._length_km * self.capex_per_km

    def max_capacity_per_year(self, start_year: int) -> dict[int, float]:
        return {start_year: self.existing_capacity,
                start_year+1: None}

    def capacity_per_year(self, start_year: int) -> dict[int, float] | None:
        if not self.existing_capacity:
            return None
        return {start_year: self.existing_capacity,
                start_year + self.existing_capacity_retirement_years -1: self.existing_capacity,
                start_year + self.existing_capacity_retirement_years: 0.0}




class PipeTechnology(Technology):
    """A PipeType instantiated between two regions.

    ``below_distance_threshold`` zeroes out construction cost: regions
    considered already connected pay no pipe capex.
    """

    def __init__(self, name: str,
                 region_id_in: int,
                 region_id_out: int,
                 pipe_length_km: float,
                 existing_capacity: float = 0.0):
        super().__init__(name)
        registered_type = type(self)._get_registered_type(name)

        try:
            self.commodity_in: str = registered_type["commodity_in"]
            self.commodity_out: str = registered_type["commodity_out"]
            self.loss_percent: float = registered_type["loss_percent"]
            self.technical_lifetime: int = registered_type["technical_lifetime"]
            self.capex_per_km: float = registered_type["capex_per_km"]
            self.distance_threshold_m: float = registered_type["distance_threshold_m"] # costs are 0 if length is below this threshold to reduce binary variables
        except KeyError:
            raise KeyError(f"The registered type '{name}' does not provide all the data for")

        self.region_id_in = region_id_in
        self.region_id_out = region_id_out
        self.pipe_length_km = pipe_length_km
        self.existing_capacity = existing_capacity

    @property
    def below_distance_threshold(self):
        return self.pipe_length_km * 1000 < self.distance_threshold_m

    @property
    def costs_eur(self):
        if self.below_distance_threshold:
            return 0
        return self.capex_per_km * self.pipe_length_km


_SECTION_TO_CLASS: dict[str, type["Technology"]] = {
    "decentralized": DecentralTechnology,
    "central": CentralTechnology,
    "chp": CHPTechnology,
    "grids": GridTechnology,
    "pipes": PipeTechnology,
}


def register_technologies(path: str | Path, clear_registry: bool = True) -> None:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Technology catalog not found: {file_path}")

    raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    if raw is None:
        return

    if not isinstance(raw, dict):
        raise ValueError(f"{file_path}: top-level YAML must be a mapping of sections, got {type(raw).__name__}")

    unknown_sections = set(raw) - set(_SECTION_TO_CLASS)
    if unknown_sections:
        raise ValueError(f"{file_path}: unknown section(s) {sorted(unknown_sections)}. "
                         f"Valid sections: {sorted(_SECTION_TO_CLASS)}")

    for section_name, section_cls in _SECTION_TO_CLASS.items():
        if clear_registry:
            section_cls.clear_registered_types()

        entries = raw.get(section_name)
        if entries is None:
            continue
        if not isinstance(entries, dict):
            raise ValueError(f"{file_path}: section '{section_name}' must be a mapping, "
                             f"got {type(entries).__name__}")

        for name, entry in entries.items():
            section_cls.register(name, **entry)


if __name__ == '__main__':
    yml_path = Path(
        "src/pypeline/energy_technology/configs/technologies_new.yaml")
    register_technologies(yml_path)

    ind_heat_pump = DecentralTechnology(name="ind_heat_pump", existing_capacity=0)
    CentralTechnology(name="cen_heat_pump", existing_capacity=0)
    GridTechnology(name="heat_grid", existing_capacity=0)
    PipeTechnology(name="heat_pipe", region_id_in=0, region_id_out=1, pipe_length_km=10)
