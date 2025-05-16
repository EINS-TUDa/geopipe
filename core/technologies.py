from enum import Enum

class Technologies(Enum):
    Gas = "Gas"
    Oil = "Oil"
    Wood = "Wood"
    Biomass = "Biomass"
    Renewable = "Renewable" # Solar, Geothermal, Heatpump
    Electric = "Electric"
    Coal = "Coal"
    District_Heating = "District Heating"
    Other = "Other"
    NoEnergyCarrier = "No Energy Carrier"