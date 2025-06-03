from core.energy_system.technology import IndividualTechnology, CentralTechnology, HeatGrid
from core.energy_system.technology_registry import default_technology_registry


@default_technology_registry(IndividualTechnology)
class IndHeatPump(IndividualTechnology):
    def __init__(self):
        super().__init__(
            name ="heat_pump",
            commodity_in="electricity",
            commodity_out="residential_heat",
            efficiency=3.5,
            technical_lifetime=20,
            opex_cost_energy=0.01,
            opex_cost_power=50,
            capex_cost_power=1000
        )

@default_technology_registry(IndividualTechnology)
class IndGasBoiler(IndividualTechnology):
    def __init__(self):
        super().__init__(
            name="gas_boiler",
            commodity_in="gas",
            commodity_out="residential_heat",
            efficiency=0.9,
            technical_lifetime=15,
            opex_cost_energy=0.02,
            opex_cost_power=30,
            capex_cost_power=800
        )

@default_technology_registry(CentralTechnology)
class CTHeatPump(CentralTechnology):
    def __init__(self):
        super().__init__(
            name="heat_pump",
            commodity_in="electricity",
            commodity_out="heat_grid_heat_in",
            efficiency=3.5,
            technical_lifetime=20,
            opex_cost_energy=0.01,
            opex_cost_power=50,
            capex_cost_power=1000
        )

@default_technology_registry(HeatGrid)
class HGUrban(HeatGrid):
    def __init__(self):
        super().__init__(
            name="urban_heat_grid",
            commodity_in="heat_grid_heat_in",
            commodity_out="district_heat_out",
            efficiency=0.95,
            technical_lifetime=30,
            opex_cost_energy=0.005,
            opex_cost_power=20,
            capex_cost_power=5000
        )