from core.data_registry import RegistryService
from core.technology import Technology
import geopandas as gpd
import numpy as np


class District:
    def __init__(self, id_:int, technology_shares:dict[Technology, float]):
        self.id = id_
        self.technology_shares = technology_shares

    @classmethod
    def from_bounding_box(cls, id_:int, polygone) -> "District":
        """
        Create a District object from a polygon.
        :param id_: Identifier for the district
        :param polygone: Shape of the district
        :return: class instance
        """
        technology_shares = get_technology_shares(polygone=polygone)
        return cls(id_, technology_shares)

    def print_technology_shares(self):
        """
        Print the technology shares of the district.
        """
        print(f"District ID: {self.id}")
        for tech, share in self.technology_shares.items():
            print(f"{tech.value}: {share:.2%}")

def get_technology_shares(polygone: gpd.GeoDataFrame) -> dict[Technology, float]:
    # Fetch data for the district
    gpd_data = RegistryService.fetch_data("Census2022", "HeatingType100mGrid", polygone)
    technology_amounts = {}
    for tech in Technology:
        technology_amounts[tech] = gpd_data[tech].sum()
    # Calculate shares
    total_amount = sum(technology_amounts.values())
    if total_amount == 0:
        return {tech: 0 for tech in Technology}  # Avoid division by zero
    technology_shares = {tech: amount / total_amount for tech, amount in technology_amounts.items()}
    return technology_shares



def create_random_technology_shares() -> dict:
    """
    Create a random technology shares dictionary for testing purposes.
    :return: Dictionary with technology shares
    """
    # Generate random values for each technology
    shares = {tech: np.random.uniform(0, 1) for tech in Technology}

    # Normalize the shares to sum to 1
    total = sum(shares.values())
    for tech in shares:
        shares[tech] /= total

    return shares



