from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Any, cast

import networkx as nx
import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, PositiveFloat

from ._year_dep import get_earliest_year_value

class _TechType(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DecentralTechType(_TechType):
    commodity_in: str
    commodity_out: str
    efficiency: PositiveFloat
    technical_lifetime: int
    opex_cost_energy: float
    opex_cost_power: float
    capex_cost_power: float
    existing_capacity_phase_out_years: float | None = None


class CentralTechType(_TechType):
    commodity_in: str
    commodity_out: str
    efficiency: PositiveFloat
    technical_lifetime: int
    opex_cost_energy: float
    opex_cost_power: float
    capex_cost_power: float
    capex_cost_base: float
    max_capacity_per_unit: float | None = None
    max_capacity: float | dict[int, float] | None = None
    constrain_location_to_streets: list[str] = []
    availability_profile_name: str | None = None
    output_profile_name: str | None = None
    existing_capacity_retirement_years: float | None = None


class CHPTechType(CentralTechType):
    commodity_out_2: str
    loss: float


class GridTechType(_TechType):
    commodity_in: str
    commodity_out: str
    efficiency: PositiveFloat
    capex_per_km: float
    technical_lifetime: int
    existing_capacity_retirement_years: float | None = None


class PipeTechType(_TechType):
    commodity_in: str
    commodity_out: str
    efficiency: PositiveFloat
    technical_lifetime: int
    capex_per_km: float
    distance_threshold_m: float
    existing_capacity_retirement_years: float | None = None


class TechnologyCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decentralized: dict[str, DecentralTechType] = {}
    central: dict[str, CentralTechType] = {}
    chp: dict[str, CHPTechType] = {}
    grids: dict[str, GridTechType] = {}
    pipes: dict[str, PipeTechType] = {}


class Technology(ABC):
    _registered_types: dict[str, _TechType] = {}
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
    def register(cls, name: str, type_: _TechType):
        if name in cls._registered_types:
            raise ValueError(f"Type {name} already exists")
        cls._registered_types[name] = type_

    @classmethod
    def clear_registered_types(cls):
        cls._registered_types.clear()

    @classmethod
    def _get_registered_type(cls, name: str) -> _TechType:
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
            return cls._registered_types[name].model_dump()
        for subclass in cls.__subclasses__():
            if subclass.has_type(name):
                return subclass.get_type_defaults(name)
        raise ValueError(f"Type of {name} does not exist.")

    @classmethod
    def get_type_names_by_attribute(cls, attribute: str, value: Any) -> tuple[str, ...]:
        """Return the names of registered types whose default ``attribute`` equals ``value``."""
        names = tuple(name for name, t in cls._registered_types.items()
                      if getattr(t, attribute, None) == value)
        for subclass in cls.__subclasses__():
            names = names + subclass.get_type_names_by_attribute(attribute, value)
        return names

    @classmethod
    def is_grid_commodity(cls, commodity: str) -> bool:
        return commodity in cls._region_scoped_commodities



class DecentralTechnology(Technology):

    @classmethod
    def register(cls, name: str, type_: DecentralTechType):
        super().register(name, type_)
        Technology._region_scoped_commodities.add(type_.commodity_out)

    def __init__(self, name: str,
                 existing_capacity: float = 0.0,
                 existing_energy_output: float = 0.0,
                 output_profile: Optional[pd.Series] = None):
        super().__init__(name)
        t = cast(DecentralTechType, type(self)._get_registered_type(name))

        self.commodity_in = t.commodity_in
        self.commodity_out = t.commodity_out
        self.efficiency = t.efficiency
        self.technical_lifetime = t.technical_lifetime
        self.opex_cost_energy = t.opex_cost_energy
        self.opex_cost_power = t.opex_cost_power
        self.capex_cost_power = t.capex_cost_power

        self.existing_capacity = existing_capacity
        self.existing_energy_output = existing_energy_output
        self.output_profile = output_profile

        # Years from model start by which existing_capacity has decreased linearly to 0.
        # Only required when existing_capacity is non-zero.
        self.existing_capacity_phase_out_years = t.existing_capacity_phase_out_years
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

        t = cast(CentralTechType, type(self)._get_registered_type(name))
        self.commodity_in = t.commodity_in
        self.commodity_out = t.commodity_out
        self.efficiency = t.efficiency
        self.technical_lifetime = t.technical_lifetime
        self.opex_cost_energy = t.opex_cost_energy
        self.opex_cost_power = t.opex_cost_power
        self.capex_cost_power = t.capex_cost_power
        self.capex_cost_base = t.capex_cost_base
        self.max_capacity_per_unit = t.max_capacity_per_unit
        self._max_capacity = t.max_capacity # is always overwritten by existing capacity for year 0
        self.constrain_location_to_streets = list(t.constrain_location_to_streets)
        self.output_profile_name = output_profile_name if output_profile_name is not None else t.output_profile_name
        self.availability_profile_name = availability_profile_name if availability_profile_name is not None else t.availability_profile_name

        # Years from model start by which existing_capacity has decreased linearly to 0.
        # Only required when existing_capacity is non-zero.
        self.existing_capacity_retirement_years = t.existing_capacity_retirement_years

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
            year0_max = get_earliest_year_value(self._max_capacity)
            if year0_max is not None and capacity > year0_max:
                raise ValueError(
                    f"Cannot set existing_capacity={capacity} for technology {self.name} as it "
                    f"exceeds max_capacity {year0_max} ."
                )
        self._existing_capacity = capacity

    def existing_capacity_per_year(self, start_year: int) -> dict[int, float] | None:
        if not self.existing_capacity:
            return None
        return {start_year: self.existing_capacity,
                start_year + self.existing_capacity_retirement_years - 1: self.existing_capacity,
                start_year + self.existing_capacity_retirement_years: 0.0}

    def max_capacity_per_year(self, start_year: int) -> dict[int, float | None]:
        if self._max_capacity is None:
            return {start_year: self.existing_capacity,
                    start_year + 1: None}
        elif isinstance(self._max_capacity, (int, float)):
            return {start_year: self.existing_capacity,
                    start_year + 1: self._max_capacity}
        elif isinstance(self._max_capacity, dict):
            cap_per_year = {start_year: self.existing_capacity}
            for year, cap in self._max_capacity.items():
                if year == 0 and 1 in self._max_capacity:
                    continue  # start_year+1 is owned by the explicit year-1 entry
                cap_per_year[start_year + (1 if year == 0 else year)] = cap
            return cap_per_year
        else:
            raise TypeError(f"Invalid type for max_capacity: {type(self._max_capacity)}")

class CHPTechnology(CentralTechnology):

    def __init__(self, name: str,
                 existing_capacity: float = 0.0,
                 output_profile_name: Optional[str] = None,
                 availability_profile_name: Optional[str] = None):
        super().__init__(name, existing_capacity, output_profile_name, availability_profile_name)
        t = cast(CHPTechType, type(self)._get_registered_type(name))
        self.commodity_out_2 = t.commodity_out_2
        self.loss = t.loss


class GridTechnology(Technology):
    """A GridType instantiated in a specific region."""

    @classmethod
    def register(cls, name: str, type_: GridTechType):
        super().register(name, type_)
        Technology._region_scoped_commodities.add(type_.commodity_in)
        Technology._region_scoped_commodities.add(type_.commodity_out)

    def __init__(self, name: str,
                 length_km: float,
                 existing_capacity: float = 0.0,):
        super().__init__(name)
        t = cast(GridTechType, type(self)._get_registered_type(name))

        self.commodity_in = t.commodity_in
        self.commodity_out = t.commodity_out
        self.efficiency = t.efficiency
        self.capex_per_km = t.capex_per_km
        self.technical_lifetime = t.technical_lifetime

        self.existing_capacity = existing_capacity
        self._grid_length_km = length_km

        # Years from model start at which existing_capacity drops abruptly to 0 (no linear decay).
        # Only required when existing_capacity is non-zero.
        self.existing_capacity_retirement_years = t.existing_capacity_retirement_years

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
                 topology: nx.Graph,
                 existing_capacity: float = 0.0):
        super().__init__(name)
        t = cast(PipeTechType, type(self)._get_registered_type(name))

        self.commodity_in = t.commodity_in
        self.commodity_out = t.commodity_out
        self.efficiency = t.efficiency
        self.technical_lifetime = t.technical_lifetime
        self.capex_per_km = t.capex_per_km
        self.distance_threshold_m = t.distance_threshold_m  # costs are 0 if length is below this threshold to reduce binary variables

        self.region_id_in = region_id_in
        self.region_id_out = region_id_out
        self.pipe_length_km = pipe_length_km
        self.topology = topology
        self.existing_capacity = existing_capacity

        # Years from model start at which existing_capacity drops abruptly to 0 (no linear decay).
        # Only required when existing_capacity is non-zero.
        self.existing_capacity_retirement_years = t.existing_capacity_retirement_years

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

    raw = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    catalog = TechnologyCatalog.model_validate(raw)

    if clear_registry:
        Technology._region_scoped_commodities.clear()
        for section_cls in _SECTION_TO_CLASS.values():
            section_cls.clear_registered_types()

    sections: list[tuple[type[Technology], dict[str, _TechType]]] = [
        (DecentralTechnology, catalog.decentralized),
        (CentralTechnology, catalog.central),
        (CHPTechnology, catalog.chp),
        (GridTechnology, catalog.grids),
        (PipeTechnology, catalog.pipes),
    ]
    for section_cls, types in sections:
        for name, type_ in types.items():
            section_cls.register(name, type_)