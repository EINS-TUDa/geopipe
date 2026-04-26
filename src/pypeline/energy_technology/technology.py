from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import pandas as pd
import yaml


class Technology(ABC):
    _registered_types = {}

    @abstractmethod
    def __init__(self):
        ...  # prevent direct instantiation of Technology

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


class DecentralTechnology(Technology):

    def __init__(self, name: str,
                 existing_capacity: float = 0.0,
                 capacity_restriction: Optional[float | dict[int, float]] = None,
                 output_profile_name: Optional[str] = None,
                 output_profile: Optional[pd.Series] = None):
        super().__init__()
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
        self._capacity_restriction = capacity_restriction
        self.output_profile_name = output_profile_name
        self.output_profile = output_profile

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
        super().__init__()
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
                 capacity_restriction: Optional[float | dict[int, float]] = None,
                 output_profile_name: Optional[str] = None,
                 output_profile: Optional[pd.Series] = None,
                 availability_profile_name: Optional[str] = None,
                 availability_profile: Optional[pd.Series] = None):
        super().__init__()
        registered_type = type(self)._get_registered_type(name)

        try:
            ...  # todo: Fill when CHP-specific fields are defined
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


class GridTechnology(Technology):
    """A GridType instantiated in a specific region."""

    def __init__(self, name: str,
                 existing_capacity: float = 0.0,
                 capacity_restriction: Optional[float | dict[int, float]] = None):
        super().__init__()
        registered_type = type(self)._get_registered_type(name)

        try:
            self.commodity_in: str = registered_type["commodity_in"]
            self.commodity_out: str = registered_type["commodity_out"]
            self.efficiency: float = registered_type["efficiency"]
            self.technical_lifetime: int = registered_type["technical_lifetime"]
        except KeyError:
            raise KeyError(f"The registered type '{name}' does not provide all the data for")

        self.existing_capacity = existing_capacity
        self._capacity_restriction = capacity_restriction

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


class PipeTechnology(Technology):
    """A PipeType instantiated between two regions.

    ``below_distance_threshold`` zeroes out construction cost: regions
    considered already connected pay no pipe capex.
    """

    region_id_in: int
    region_id_out: int
    pipe_length_km: float
    existing_capacity: float = 0.0
    below_distance_threshold: bool = False
    costs_eur: float = 0.0

    def __init__(self, name: str,
                 region_id_in: int,
                 region_id_out: int,
                 pipe_length_km: float,
                 existing_capacity: float = 0.0,
                 below_distance_threshold: bool = False,
                 costs_eur: float = 0.0):
        super().__init__()
        registered_type = type(self)._get_registered_type(name)

        try:
            self.commodity_in: str = registered_type["commodity_in"]
            self.commodity_out: str = registered_type["commodity_out"]
            self.loss_percent: float = registered_type["loss_percent"]
            self.technical_lifetime: int = registered_type["technical_lifetime"]
            self.capex_per_km: float = registered_type["capex_per_km"]
        except KeyError:
            raise KeyError(f"The registered type '{name}' does not provide all the data for")

        self.region_id_in = region_id_in
        self.region_id_out = region_id_out
        self.pipe_length_km = pipe_length_km
        self.existing_capacity = existing_capacity
        self.below_distance_threshold = below_distance_threshold
        self.costs_eur = costs_eur


_SECTION_TO_CLASS: dict[str, type["Technology"]] = {
    "decentralized": DecentralTechnology,
    "central": CentralTechnology,
    "chp": CHPTechnology,
    "grids": GridTechnology,
    "pipes": PipeTechnology,
}


def register(path: str | Path, clear_registry: bool = True) -> None:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Technology catalog not found: {file_path}")

    if clear_registry:
        Technology.clear_registered_types()

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
    register(yml_path)

    ind_heat_pump = DecentralTechnology(name="ind_heat_pump", existing_capacity=0)
    CentralTechnology(name="cen_heat_pump", existing_capacity=0)
    GridTechnology(name="heat_grid", existing_capacity=0)
    PipeTechnology(name="heat_pipe", region_id_in=0, region_id_out=1, pipe_length_km=10)
