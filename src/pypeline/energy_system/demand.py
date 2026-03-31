from dataclasses import dataclass

import pandas as pd


@dataclass
class Demand:
    # Abstract description of a demand in the energy system, i.e. valid for all regions
    demand_type: str
    commodity_in: str
    cooperation_of_technologies: bool = True
    default_supply_technology: str | None = None  # Default technology to cover this demand in the initial modeling year if no other technology is specified. Can be None if there are no residual technologies available.

    demand_query_params: dict[str, any] = None
    profile_query_params: dict[str, any] = None

    technology_shares_query_params: dict[str, any] = None


@dataclass
class RegionDemand:
    # Region specific
    demand: Demand
    value: float = None
    profile: pd.Series = None

    def __post_init__(self):
        self.check_types()
        self.normalize_profile()

    def check_types(self):
        ...

    def normalize_profile(self):

        if self.profile is not None and not self.profile.empty:
            if not all(isinstance(x, (int, float)) for x in self.profile):
                raise ValueError("All values in the profile must be numeric.")
            self.profile = self.profile / self.profile.sum()
        else:
            f"Time series profile for Demand {self.demand.demand_type} is empty or None. "

    def annual_value(self) -> float:
        """Return the annual energy requirement in MWh, defaulting to 0 when unset."""
        return float(self.value) if self.value is not None else 0.0
