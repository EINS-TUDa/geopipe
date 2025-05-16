from core.data_registry import fetch_data
from core.technologies import Technologies


class District:
    def __init__(self,id_, technology_shares):
        self.id = id_
        self.technology_shares = technology_shares

    @classmethod
    def from_bounding_box(cls, id_:int, bounding_box):
        """
        Create a District object from a polygon.
        :param id_: Identifier for the district
        :param bounding_box: Shape of the district
        :return: class instance
        """
        # Fetch data for the district
        data = fetch_data("Census2022", "HeatingType100mGrid", bounding_box)
        ...
        """
        add: to retrieve technology shares from the data
        technology_shares should be a diction in the form of
        {
            Technologies.Gas: 0.3,
            Technologies.Oil: 0.2,
            Technologies.Wood: 0.1,
            Technologies.Biomass: 0.1,
            Technologies.Renewable: 0.2,
            Technologies.Electric: 0.05,
            Technologies.Coal: 0.01,
            Technologies.District_Heating: 0.01,
            Technologies.NoEnergyCarrier: 0.01
        }
        """
        technology_shares = data
        return cls(id_, technology_shares)

    def print_technology_shares(self):
        """
        Print the technology shares of the district.
        """
        print(f"District ID: {self.id}")
        for tech, share in self.technology_shares.items():
            print(f"{tech.value}: {share:.2%}")



