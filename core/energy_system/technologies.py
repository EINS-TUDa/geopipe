from core.energy_system.technology import Technology
from core.energy_system.technology_registry import default_technology_registry


@default_technology_registry
class IndHeatPump(Technology):
    def __init__(self):
        super().__init__(
            name ="ind_heat_pump",
            commodity_in="electricity",
            commodity_out="residential_heat",
            efficiency=3.5,
            technical_lifetime=20,
            opex_cost_energy=0.01,
            opex_cost_power=50,
            capex_cost_power=1000
        )

@default_technology_registry
class IndGasBoiler(Technology):
    def __init__(self):
        super().__init__(
            name="ind_gas_boiler",
            commodity_in="gas",
            commodity_out="residential_heat",
            efficiency=0.9,
            technical_lifetime=15,
            opex_cost_energy=0.02,
            opex_cost_power=30,
            capex_cost_power=800
        )

@default_technology_registry
class IndOilBoiler(Technology):
    def __init__(self):
        super().__init__(
            name="ind_oil_boiler",
            commodity_in="oil",
            commodity_out="residential_heat",
            efficiency=0.9,
            technical_lifetime=15,
            opex_cost_energy=0.02,
            opex_cost_power=30,
            capex_cost_power=800
        )

@default_technology_registry
class IndDistrictHeatingConnection(Technology):
    def __init__(self):
        super().__init__(
            name="ind_district_heating_connection",
            commodity_in="district_heat_out",
            commodity_out="residential_heat",
            efficiency=0.95,
            technical_lifetime=30,
            opex_cost_energy=0.005,
            opex_cost_power=20,
            capex_cost_power=5000
        )

@default_technology_registry
class CTHeatPump(Technology):
    def __init__(self):
        super().__init__(
            name="cen_heat_pump",
            commodity_in="electricity",
            commodity_out="district_heat_in",
            efficiency=3.5,
            technical_lifetime=20,
            opex_cost_energy=0.01,
            opex_cost_power=50,
            capex_cost_power=1000
        )

@default_technology_registry
class HGHeatGrid(Technology):
    def __init__(self):
        super().__init__(
            name="heat_grid",
            commodity_in="district_heat_in",
            commodity_out="district_heat_out",
            efficiency=0.95,
            technical_lifetime=30,
            opex_cost_energy=0.005,
            opex_cost_power=20,
            capex_cost_power=5000
        )

@default_technology_registry
class GridElectricity(Technology):
    def __init__(self):
        super().__init__(
            name="grid_electricity",
            commodity_in="dummy",
            commodity_out="electricity",
            efficiency=1.0,
            technical_lifetime=30,
            opex_cost_energy=0.01,
            opex_cost_power=10,
            capex_cost_power=1000
        )

