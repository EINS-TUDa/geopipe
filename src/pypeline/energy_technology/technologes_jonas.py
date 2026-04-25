from dataclasses import dataclass
from typing import Optional

import pandas as pd


# @dataclass(kw_only=True)
# class Technology:
#     """Shared base for all catalog templates.
#
#     ``existing_capacity_period`` indicates how long pre-existing capacity
#     (capacity built before the modelled period) is still available. Linear
#     decay from the first modelled year over this many years. ``None`` means
#     "available throughout the full modelled period".
#     """
#
#     name: str
#     commodity_in: str
#     technical_lifetime: int
#     existing_capacity_period: Optional[int] = None

    # def __post_init__(self) -> None:
    #     if not self.name:
    #         raise ValueError("TechnologyType.name must be non-empty")
    #     if not self.commodity_in:
    #         raise ValueError(f"{self.name}: commodity_in must be non-empty")
    #     if self.technical_lifetime <= 0:
    #         raise ValueError(
    #             f"{self.name}: technical_lifetime must be > 0 (got {self.technical_lifetime})"
    #         )
    #     if self.existing_capacity_period is not None and self.existing_capacity_period <= 0:
    #         raise ValueError(
    #             f"{self.name}: existing_capacity_period must be > 0 if set "
    #             f"(got {self.existing_capacity_period})"
    #         )


class DecentralTech:
    """A DecentralTechType instantiated in a specific region.

    If the owning demand has ``cooperation_of_technologies=True``, the
    RegionBuilder must copy the demand's profile and profile name onto this
    instance so constraints can be enforced.
    """

    _registered_types = {}

    commodity_in: str
    commodity_out: str

    def __init__(self, name: str,
                 existing_capacity: float = 0.0,
                 capacity_restriction: Optional[float | dict[int, float]] = None,  # todo: Ok to be a dict?
                 output_profile_name: Optional[str] = None,
                 output_profile: Optional[pd.Series] = None):
        if name not in type(self)._registered_types:
            raise ValueError(f"Type of {name} does not exist.")
        self.__dict__.update(type(self)._registered_types[name])
        # for key, value in type(self)._registered_types[name].items():
        #     setattr(self, key, value)

        self.existing_capacity = existing_capacity
        self.capacity_restriction = capacity_restriction
        self.output_profile_name = output_profile_name
        self.output_profile = output_profile

    @classmethod
    def register(cls, name: str, **kwargs):
        if name in cls._registered_types:
            raise ValueError("Type already exists")
        cls._registered_types[name] = kwargs


if __name__ == '__main__':
    ind_heat_pump_data = {
        "commodity_in": "electricity",
        "commodity_out": "residential_heat",
        # "efficiency": 3.9,
        # "technical_lifetime": 18,
        # "opex_cost_energy": 0,
        # "opex_cost_power": 13000,  # EUR/MW
        # "capex_cost_power": 10100  # EUR/MWh
    }
    DecentralTech.register("ind_heat_pump", **ind_heat_pump_data)

    ind_heat_pump = DecentralTech(name="ind_heat_pump", existing_capacity=0,)
    print(DecentralTech.commodity_in)
    print(ind_heat_pump.commodity_in)
