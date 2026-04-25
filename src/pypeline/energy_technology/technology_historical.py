from abc import ABC
from typing import Optional

from dataclasses import dataclass


@dataclass
class TechnologyType(ABC):
    pass

@dataclass
class DecentralizedTechnologyType(TechnologyType):
    name: str
    commodity_in: str
    commodity_out: str
    efficiency: float
    technical_lifetime: int
    opex_cost_energy: float
    opex_cost_power: float
    capex_cost_power: float
    capex_cost_base: float
    existing_capacity_period: Optional[int] = None #  indicates the period during which existing capacity from before the modelled period is available. We assume linear decrease in capacity from first year of modelled period until first year + existing_capacity_period, after which existing capacity is fully unavailable. If None, existing capacity is fully available during the whole modelled period.

@dataclass
class CentralTechnologyType(TechnologyType):
    # TODO: same as decentralized -> better to have a category attribute instead of separate classes?
    name: str
    commodity_in: str
    commodity_out: str
    efficiency: float
    technical_lifetime: int
    opex_cost_energy: float
    opex_cost_power: float
    capex_cost_power: float
    capex_cost_base: float
    existing_capacity_period: Optional[int]  # indicates the period during which technology is available. Existing capacity is fully available in the indicated period. # TODO: In the future also accept dict for more sophisticated schedules

@dataclass
class CHPType(CentralTechnologyType):
    # doesn't need commodity_out, efficiency of CentralTechnology TODO: How to deal with that?
    commodity_out_1: str
    commodity_out_2: str
    max_efficiency_to_commodity_out_1: float # in percent of input energy
    min_loss: float # in percent of input energy

@dataclass
class GridType(TechnologyType):
    name: str
    commodity_in: str
    commodity_out: str
    efficiency: float
    technical_lifetime: int
    capex_per_km: float
    existing_capacity_period: Optional[int]  # indicates the period during which technology is available. Existing capacity is fully available in the indicated period. # TODO: In the future also accept dict for more sophisticated schedules

@dataclass
class PipeTechnology(TechnologyType):
    name: str
    commodity_in: str
    commodity_out: str
    loss_percent: float # in percent of input energy
    technical_lifetime: int
    capex_per_km: float
    existing_capacity_period: Optional[int]  # indicates the period during which technology is available. Existing capacity is fully available in the indicated period. # TODO: In the future also accept dict for more sophisticated schedules

@dataclass
class Pipe:
    # TODO: this class should replace Pipe in energy_system.pipe
    pipe: PipeTechnology
    region_id_in: int
    region_id_out: int
    pipe_length_km: float
    costs_eur: float
    below_distance_threshold: bool = False

    def __post_init__(self) -> None:
        if self.below_distance_threshold:
            self.costs_eur = 0.0

# Replace RegionTechnology with DecentralizedTechnology, CentralTechnology, PipeTechnology
# Technology shouldn't have TechnologyType as attributes but instead have their own attributes, so that users can later overwrite values for specific technologies, e.g. if they have more detailed information for some technologies
@dataclass
class DecentralizedTechnology(DecentralizedTechnologyType):
    # TODO: What's the best way? This should have extend attributes of DecentralizedTechnologyType, but also have attributes for existing capacity and capacity restrictions, which are not part of the type but of the specific technology
    existing_capacity: float = 0.0 # from before modelled period TODO: In ResolvedSystem, this has to be converted to a dict of year -> capacity based on existing_capacity_period
    capacity_restriction: Optional[float | dict[int, float]] = None # max capacity in each year, either as a fixed value or as a dict of year -> TODO: can only be resolved in ResolvedSystem if years are known?

@dataclass
class Grid(GridType):
    existing_capacity: float

"""
Changes:
For every TechnologyType, there should be a corresponding Technology class.

Per Region:
1. Identify all Demands. For each Demand:
2. + 3 Identify all DecentralizedTechnologies that can satisfy the demand (i.e. have commodity_out = demand.commodity_in).
    From the data on technology shares, derive the share of demand that is satisfied by each technology. This can be used to derive the existing capacity for each DecentralizedTechnology (Total required capacity is Demand.value * max(Demand.profile), then account of technology shares). In the User config it should be possible to indicate
    a default technology for each demand, which is used if no data on technology shares is available. If no default technology is indicated, and no data on technology shares is available, thrown an error.
4. See, if there is a grid with the respective commodity_out for each commodity_in of decentralized technologies with existing capacity. If there are multiple grids, throw an error.
    The user can indicate a considered_connected distance in the config. If the distance between regions which have existing capacity for the same type of grid is below the threshold, we can assume that they are connected. This means, that there are pipes between the regions with respective
    existing capacity to transport the respective commodity.

5. Derive the existing capacity for CentralTechnologies based on the existing capacity of the grid. If regions are considered_connected, existing_capacity for a centralTechnology should only be available in one of the connected regions.
    The existing capacity for central technologies in this region has to suffice to satisfy the demand of all regions supplied by the grid. Account for losses in grid an decentralized technologies. This also has to be accounted for
    when identify the existing_capacity for the grids. If the location of the existing capacity for the central technology of considered_connected regions requires transport through individual regions within the considered_connected regions,
    it's grid needs to have sufficient existing capacity to transport the required amount of energy. If the user didn't indicate a preferred location for the existing capacity in the config, any region within the considered_connected regions can be chosen.
    In the config the user HAS to indicate a default CentralTechnologyType for each defined gridtype. The existing capacity will be built for this type. If the user didn't indicate a default CentralTechnologyType for a gridtype, and there are decentralized technologies with existing capacity that are connected by a grid of the respective gridtype, thrown an error.

All created Technologies should live as a Technology in Region. Does it make sense to have separate attributes in Region for decentralized technologies, central technologies and grids?
The pipes connecting the regions live on EnergySystem. Here we also need to identify the "Considered_connected" for regions.

Storages shouldn't be considered at all. If the demand has cooperation_of_technologies = True, then the output profile of decentralized technologies has to match the demands profile. We not only need the profile values but also the profile name on the technologies. For central technologies it should be
possible to indicate output_profile names or availability_profile names. out_frac_min/out_frac_in is to CESM specific and will only be used in the pypeline.cesm module. co2 emissions shouldn't live in Technologies.
technologies without existing capacity should be instantiated with existing_capacity = 0, so that they are still available for expansion in the optimization.
We only consider grids, if they are defined. If the grid is not defined, there has to be a Import Instance which imports the respective commodity. For electricity and gas we assume only imports and neglect the grids. But for future
extensions the grid should be generic, so that we can also consider grids for other commodities.
I am not sure how to best deal with considered_connected.
The Flow computation across connected regions is non-trivial. For now, take shortest path to chosen central location. The user can indicate an additional capacity percentage in the config,
to allow grid expansion without having to invest in a new grid and pipes.
If no region in connected_regions is indicated as the user as preferred location for existing capacity, choose region with highest demand.
Also, the user can indicate in the config a minimum_dhn_share in a region. If the technology_share data is below this value for a region, we assume that there are no heat exchangers and there is no heat grid. The shares of the
other technologies are then normalized to 100%. This grid is then also not part of connected regions.
We don't need cap_factor_ind_technologies additionally.
I don't know how to best consolidate DecentralizedTechnologyType, DecentralizedTechnology, Spec. Propose something.

Let's do a two-step migration (a) introduce the new types alongside, adapt RegionBuilder to emit them; (b) update consumers; (c) delete old Technology.
The current technologies.yaml needs to be adjusted to the new required attributes in technology_new.py. Maybe it can be combined with imports.yaml.

"""
