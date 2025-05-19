from core.data_registry import fetch_data
from core.technologies import Technologies
import geopandas as gpd
import numpy as np


class District:
    def __init__(self,id_:int, technology_shares:dict[Technologies, float]):
        self.id = id_
        self.technology_shares = technology_shares

    @classmethod
    def from_bounding_box(cls, id_:int, bounding_box: gpd.GeoDataFrame) -> "District":
        """
        Create a District object from a polygon.
        :param id_: Identifier for the district
        :param bounding_box: Shape of the district
        :return: class instance
        """
        # Fetch data for the district
        data = fetch_data("Census2022", "HeatingType100mGrid", bounding_box)
        technology_shares = create_random_technology_shares()  # replace with actual data processing
        return cls(id_, technology_shares)

    def print_technology_shares(self):
        """
        Print the technology shares of the district.
        """
        print(f"District ID: {self.id}")
        for tech, share in self.technology_shares.items():
            print(f"{tech.value}: {share:.2%}")


def create_random_technology_shares() -> dict:
    """
    Create a random technology shares dictionary for testing purposes.
    :return: Dictionary with technology shares
    """
    # Generate random values for each technology
    shares = {tech: np.random.uniform(0, 1) for tech in Technologies}

    # Normalize the shares to sum to 1
    total = sum(shares.values())
    for tech in shares:
        shares[tech] /= total

    return shares



