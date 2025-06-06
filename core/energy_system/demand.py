from dataclasses import dataclass

import pandas as pd


@dataclass
class Demand:
    # Abstract description of a demand in the energy system, i.e. valid for all regions
    type_: str
    commodity_in: str
    cooperation_of_technologies: bool = True
    profile_name: str = None
    assigned_technology_shares: str|None = None  # contains the name of the key for shares if technology shares are assigned

    def __post_init__(self):
        if self.profile_name is None:
            self.profile_name = f"{self.type_}_profile"

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
            f"Time series profile for Demand {self.demand.type_} is empty or None. "
