from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Any
import pandas as pd
import yaml


class Technology(ABC):
    _registered_types: dict[str, dict[str, Any]] = {}
    _region_scoped_commodities: set[str] = set() # Populated by GridTechnology.register; shared across all subclasses.

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
        # Strict per-class lookup. Direct construction (cls(name=...)) goes
        # through this and must fail when the name belongs to a subclass —
        # otherwise the instance would be missing subclass-specific fields.
        # Cross-hierarchy lookups go through the public classmethods below.
        if name not in cls._registered_types:
            raise ValueError(f"Type of {name} does not exist.")
        return cls._registered_types[name]

    @classmethod
    def has_type(cls, name: str) -> bool:
        if name in cls._registered_types:
            return True
        return any(subclass.has_type(name) for subclass in cls.__subclasses__())

    @classmethod
    def registered_type_names(cls) -> list[str]:
        names = list(cls._registered_types.keys())
        for subclass in cls.__subclasses__():
            names.extend(subclass.registered_type_names())
        return names

    @classmethod
    def get_type_defaults(cls, name: str) -> dict[str, Any]:
        if name in cls._registered_types:
            return cls._registered_types[name].copy()
        for subclass in cls.__subclasses__():
            if subclass.has_type(name):
                return subclass.get_type_defaults(name)
        raise ValueError(f"Type of {name} does not exist.")

    @classmethod
    def get_type_names_by_attribute(cls, attribute: str, value: Any) -> tuple[str, ...]:
        """Return the names of registered types whose default ``attribute`` equals ``value``."""
        names = tuple(name for name, data in cls._registered_types.items() if data.get(attribute) == value)
        for subclass in cls.__subclasses__():
            names = names + subclass.get_type_names_by_attribute(attribute, value)
        return names

    @classmethod
    def is_grid_commodity(cls, commodity: str) -> bool:
        return commodity in cls._region_scoped_commodities



class DecentralTechnology(Technology):

    @classmethod
    def register(cls, name: str, **kwargs):
        super().register(name, **kwargs)
        if (c := kwargs.get("commodity_out")) is not None: Technology._region_scoped_commodities.add(c)

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

    @classmethod
    def from_name(cls, name: str, *args, **kwargs) -> "CentralTechnology":
        """Construct the right concrete class for ``name``.

        If ``name`` is registered under a subclass (e.g. CHPTechnology), the
        returned instance is an instance of that subclass. Use this when the
        caller iterates over names from the central registry and does not
        know upfront whether a given name is a plain central tech or a CHP.
        """
        if name in cls._registered_types:
            return cls(name, *args, **kwargs)
        for subclass in cls.__subclasses__():
            if subclass.has_type(name):
                return subclass.from_name(name, *args, **kwargs)
        raise ValueError(f"Type of {name} does not exist.")

    def __init__(self, name: str,
                 existing_capacity: float = 0.0,
                 output_profile_name: Optional[str] = None,
                 availability_profile_name: Optional[str] = None):
        super().__init__(name)

        self.output_profile_name = output_profile_name
        self.availability_profile_name = availability_profile_name

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
            self.max_capacity_per_year_per_unit: Optional[float | dict[int, float]] = registered_type.get("max_capacity_per_year_per_unit", None)
            self.max_units: Optional[int | dict[int, int]] = registered_type.get("max_units")
            self.constrain_location_to_streets: list[str] = registered_type.get("constrain_location_to_streets", [])
            if self.availability_profile_name is None:
                self.availability_profile = registered_type.get("availability_profile")
            if self.output_profile_name is None:
                self.output_profile = registered_type.get("output_profile")
        except KeyError:
            raise KeyError(f"The registered type '{name}' does not provide all the data for")

        # Years from model start by which existing_capacity has decreased linearly to 0.
        # Only required when existing_capacity is non-zero.
        self.existing_capacity_retirement_years: float = registered_type.get("existing_capacity_retirement_years")

        # Can only be set after setting max_capacity_per_year_per_unit because of validation logic in the setter.
        self.existing_capacity = existing_capacity

        # TODO: consolidate capacity setting logic. Transfer this setter logic to other technologies.

    @property
    def existing_capacity(self) -> float:
        return self._existing_capacity

    @existing_capacity.setter
    def existing_capacity(self, capacity: float):
        if capacity and self.existing_capacity_retirement_years is None:
            raise ValueError(
                f"Technology '{self.name}' has existing_capacity={capacity} but "
                f"'existing_capacity_retirement_years' is not defined."
            )
        if capacity:
            max_per_unit = self.max_capacity_per_year_per_unit
            if isinstance(max_per_unit, (int, float)):
                year0_max = max_per_unit
            elif isinstance(max_per_unit, dict):
                year0_max = max_per_unit.get(0)
            else:
                year0_max = None
            if year0_max is not None and capacity > year0_max:
                raise ValueError(
                    f"Technology '{self.name}' has existing_capacity={capacity} which "
                    f"exceeds max_capacity_per_year_per_unit={year0_max} for year 0."
                )
        self._existing_capacity = capacity

    def max_allowed_capacity_per_unit_per_year(self, start_year: int) -> dict[int, float | None]:
        max_per_unit = self.max_capacity_per_year_per_unit
        if max_per_unit is None:
            return {start_year: self.existing_capacity,
                    start_year + 1: None}
        if isinstance(max_per_unit, (int, float)):
            return {start_year: self.existing_capacity,
                    start_year + 1: max_per_unit}
        result = {start_year + rel_year: value for rel_year, value in max_per_unit.items()}
        result[start_year] = self.existing_capacity
        return result

    def existing_capacity_per_year(self, start_year: int) -> dict[int, float] | None:
        if not self.existing_capacity:
            return None
        return {start_year: self.existing_capacity,
                start_year + self.existing_capacity_retirement_years - 1: self.existing_capacity,
                start_year + self.existing_capacity_retirement_years: 0.0}

class CHPTechnology(CentralTechnology):

    def __init__(self, name: str,
                 existing_capacity: float = 0.0,
                 output_profile_name: Optional[str] = None,
                 availability_profile_name: Optional[str] = None):
        super().__init__(name, existing_capacity, output_profile_name, availability_profile_name)
        registered_type = type(self)._get_registered_type(name)

        try:
            self.commodity_out_2: str = registered_type["commodity_out_2"]
            self.loss: float = registered_type["loss"]
        except KeyError:
            raise KeyError(f"The registered type '{name}' does not provide all the data for")


class GridTechnology(Technology):
    """A GridType instantiated in a specific region."""

    @classmethod
    def register(cls, name: str, **kwargs):
        super().register(name, **kwargs)
        if (c := kwargs.get("commodity_in"))  is not None: Technology._region_scoped_commodities.add(c)
        if (c := kwargs.get("commodity_out")) is not None: Technology._region_scoped_commodities.add(c)

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
        self._grid_length_km = length_km

        # Years from model start at which existing_capacity drops abruptly to 0 (no linear decay).
        # Only required when existing_capacity is non-zero.
        self.existing_capacity_retirement_years: float = registered_type.get("existing_capacity_retirement_years")

    @property
    def length_km(self) -> float:
        return self._grid_length_km

    @property
    def investment_costs_eur(self) -> float:
        return self._grid_length_km * self.capex_per_km

    def max_capacity_per_year(self, start_year: int) -> dict[int, float]:
        return {start_year: self.existing_capacity,
                start_year+1: None}

    def capacity_per_year(self, start_year: int) -> dict[int, float] | None:
        if self.existing_capacity and self.existing_capacity_retirement_years is None:
            raise ValueError(
                f"Technology '{self.name}' has existing_capacity={self.existing_capacity} but the "
                f"registered type does not define 'existing_capacity_retirement_years'.")
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
            self.efficiency: float = registered_type["efficiency"]
            self.technical_lifetime: int = registered_type["technical_lifetime"]
            self.capex_per_km: float = registered_type["capex_per_km"]
            self.distance_threshold_m: float = registered_type["distance_threshold_m"] # costs are 0 if length is below this threshold to reduce binary variables
        except KeyError:
            raise KeyError(f"The registered type '{name}' does not provide all the data for")

        self.region_id_in = region_id_in
        self.region_id_out = region_id_out
        self.pipe_length_km = pipe_length_km
        self.existing_capacity = existing_capacity

        # Years from model start at which existing_capacity drops abruptly to 0 (no linear decay).
        # Only required when existing_capacity is non-zero.
        self.existing_capacity_retirement_years: float = registered_type.get("existing_capacity_retirement_years")

    @property
    def below_distance_threshold(self):
        return self.pipe_length_km * 1000 < self.distance_threshold_m

    @property
    def investment_costs_eur(self):
        if self.below_distance_threshold:
            return 0
        return self.capex_per_km * self.pipe_length_km

    def max_capacity_per_year(self, start_year: int) -> dict[int, float]:
        return {start_year: self.existing_capacity,
                start_year+1: None}

    def capacity_per_year(self, start_year: int) -> dict[int, float] | None:
        if self.existing_capacity and self.existing_capacity_retirement_years is None:
            raise ValueError(
                f"Pipe '{self.name}' has existing_capacity={self.existing_capacity} but the "
                f"registered type does not define 'existing_capacity_retirement_years'.")
        if not self.existing_capacity:
            return None
        return {start_year: self.existing_capacity,
                start_year + self.existing_capacity_retirement_years -1: self.existing_capacity,
                start_year + self.existing_capacity_retirement_years: 0.0}



_SECTION_TO_CLASS: dict[str, type["Technology"]] = {
    "decentralized": DecentralTechnology,
    "central": CentralTechnology,
    "chp": CHPTechnology,
    "grids": GridTechnology,
    "pipes": PipeTechnology
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

    if clear_registry:
        Technology._region_scoped_commodities.clear()
        for section_cls in _SECTION_TO_CLASS.values():
            section_cls.clear_registered_types()

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
    register_technologies(yml_path)

    ind_heat_pump = DecentralTechnology(name="ind_heat_pump", existing_capacity=0)
    CentralTechnology(name="cen_heat_pump", existing_capacity=0)
    GridTechnology(name="heat_grid", existing_capacity=0)
    PipeTechnology(name="heat_pipe", region_id_in=0, region_id_out=1, pipe_length_km=10)
