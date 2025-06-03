from enum import Enum

class CensusTechnology(Enum): # todo: Rename to CensusTechnologies
    Gas = "Gas"
    Oil = "Oil"
    Wood = "Wood"
    Biomass = "Biomass"
    Renewable = "Renewable" # Solar, Geothermal, Heatpump
    Electric = "Electric"
    Coal = "Coal"
    District_Heating = "District Heating"
    NoEnergyCarrier = "No Energy Carrier"