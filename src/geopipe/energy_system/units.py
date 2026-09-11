from abc import ABC
from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class Unit(ABC):
    """Defines the units for the data input for the energy system model."""

    power: str
    energy: str
    co2_emissions: str
    cost_energy: str
    cost_power: str
    co2_spec: str
    money: str


@dataclass(frozen=True)
class UnitKW(Unit):
    power: str = "kW"
    energy: str = "MWh"
    co2_emissions: str = "t"
    cost_energy: str = "EUR/MWh"
    cost_power: str = "EUR/kW"
    co2_spec: str = "kg/kWh"
    money: str = "EUR"


@dataclass(frozen=True)
class UnitMW(Unit):
    power: str = "MW"
    energy: str = "GWh"
    co2_emissions: str = "kilo t"
    cost_energy: str = "EUR/MWh"
    cost_power: str = "EUR/kW"
    co2_spec: str = "kg/kWh"
    money: str = "k EUR"


@dataclass(frozen=True)
class UnitGW(Unit):
    power: str = "GW"
    energy: str = "TWh"
    co2_emissions: str = "Mio t"
    cost_energy: str = "EUR/MWh"
    cost_power: str = "EUR/kW"
    co2_spec: str = "kg/kWh"
    money: str = "Mio EUR"


class UnitEnum(Enum):
    KW = "kW"
    MW = "MW"
    GW = "GW"
    KWH = "kWh"
    MWH = "MWh"
    GWH = "GWh"
    TWH = "TWh"

    def conversion_factor(self, to: "UnitEnum") -> float:
        """Factor that converts a value in this unit into ``to``, e.g. ``KWH.conversion_factor(MWH) == 0.001``."""
        if _DIMENSION[self] != _DIMENSION[to]:
            raise ValueError(f"Cannot convert {self.value} to {to.value}.")
        return _SCALE[self] / _SCALE[to]

    @property
    def unit(self) -> Unit:
        if self is UnitEnum.KW:
            return UnitKW()
        elif self is UnitEnum.MW:
            return UnitMW()
        elif self is UnitEnum.GW:
            return UnitGW()
        else:
            raise ValueError(f"Unsupported unit: {self}")


#: Size of each unit in W or Wh.
_SCALE = {UnitEnum.KW: 1e3, UnitEnum.MW: 1e6, UnitEnum.GW: 1e9,
          UnitEnum.KWH: 1e3, UnitEnum.MWH: 1e6, UnitEnum.GWH: 1e9, UnitEnum.TWH: 1e12}
_DIMENSION = {UnitEnum.KW: "power", UnitEnum.MW: "power", UnitEnum.GW: "power",
              UnitEnum.KWH: "energy", UnitEnum.MWH: "energy", UnitEnum.GWH: "energy", UnitEnum.TWH: "energy"}
