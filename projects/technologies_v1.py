from core.energy_system.technologies import TechnologyService, IndividualTechnology, CentralTechnology, HeatGrid, GridConnection

@TechnologyService.register_individual_technology("HP")
class HeatPump(IndividualTechnology):
    ...



@TechnologyService.register_individual_technology("GB")
class GasBoiler(IndividualTechnology):
    ...