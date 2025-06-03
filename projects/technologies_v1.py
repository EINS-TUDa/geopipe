from core.energy_system.technology import TechnologyRegistry, IndividualTechnology, CentralTechnology, HeatGrid, GridConnection

@TechnologyRegistry.register_individual_technology("HP")
class HeatPump(IndividualTechnology):
    ...



@TechnologyRegistry.register_individual_technology("GB")
class GasBoiler(IndividualTechnology):
    ...