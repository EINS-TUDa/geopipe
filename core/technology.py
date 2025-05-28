from enum import Enum

class Technology(Enum):
    Gas = "Gas"
    Oil = "Oil"
    Wood = "Wood"
    Biomass = "Biomass"
    Renewable = "Renewable" # Solar, Geothermal, Heatpump
    Electric = "Electric"
    Coal = "Coal"
    District_Heating = "District Heating"
    NoEnergyCarrier = "No Energy Carrier"