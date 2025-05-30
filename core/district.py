from core.data_registry import RegistryService
from core.technology import Technology
import geopandas as gpd
import numpy as np


class District:
    def __init__(self, id_:int, technology_shares:dict[Technology, float], residential_yearly_heat_demand: float = None):
        self.id = id_
        self.technology_shares = technology_shares
        self.residential_yearly_heat_demand = residential_yearly_heat_demand

    @classmethod
    def from_polygone(cls, id_: int, polygone: gpd.GeoDataFrame, base_crs: str = "EPSG:25832") -> "District":
        """
        Create a District object from a polygon.
        :param id_: Identifier for the district
        :param polygone: Shape of the district
        :param base_crs: Coordinate reference system for the polygon, default is "EPSG:25832"
        :return: class instance
        """
        technology_shares = get_technology_shares(polygone=polygone, base_crs=base_crs)
        residential_yearly_heat_demand = get_residential_yearly_heat_demand(polygone=polygone, base_crs=base_crs)
        return cls(id_, technology_shares, residential_yearly_heat_demand)



    def print(self):
        print(f"District ID: {self.id}")
        for tech, share in self.technology_shares.items():
            print(f"{tech.value}: {share:.2%}")

        print(f"Residential Yearly Heat Demand: {self.residential_yearly_heat_demand:.2f} kWh" if self.residential_yearly_heat_demand is not None else "Residential Yearly Heat Demand: Not available")



def get_technology_shares(polygone: gpd.GeoDataFrame, base_crs: str) -> dict[Technology, float]:
    query = {"polygone": polygone, "base_crs": base_crs}
    # Fetch data for the district
    gpd_data = RegistryService.fetch_data("Census2022", "HeatingType100mGrid", query)
    technology_amounts = {}
    for tech in Technology:
        technology_amounts[tech] = gpd_data[tech].sum()
    # Calculate shares
    total_amount = sum(technology_amounts.values())
    if total_amount == 0:
        return {tech: 0 for tech in Technology}  # Avoid division by zero
    technology_shares = {tech: amount / total_amount for tech, amount in technology_amounts.items()}
    return technology_shares

def get_residential_yearly_heat_demand(polygone: gpd.GeoDataFrame, base_crs: str) -> float:
    query = {"polygone": polygone, "base_crs": base_crs, "column": "qnutzwaerme_2020_kwh"}
    # Fetch data for the district
    gpd_data = RegistryService.fetch_data("WaermeatlasHessen", "WaermeatlasHessen", query)
    # Sum the yearly heat demand
    total_heat_demand = gpd_data["qnutzwaerme_2020_kwh"].sum()
    return total_heat_demand





