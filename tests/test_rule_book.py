import pandas as pd
import pytest

from pypeline.energy_system.demand import Demand, RegionDemand
from pypeline.energy_system.region import Region
from pypeline.energy_system.rule_book import (
    MinimumHeatGridOutputRule,
    MinimumHeatGridConstraintRule,
)
from pypeline.energy_system.energy_system import EnergySystem
from pypeline.energy_technology.technology import RegionTechnology, Technology


def _build_region(heat_output, other_output, include_extra=False):
    demand = Demand(
        demand_type="residential_heat",
        commodity_in="Heat",
    )
    profile = pd.Series([1.0, 1.0])
    region_demand = RegionDemand(demand=demand, value=heat_output + other_output, profile=profile)

    heat_grid = Technology(name="heat_grid", commodity_in="Heat", commodity_out="Heat")
    heat_region_tech = RegionTechnology(
        technology=heat_grid,
        initial_energy_output=heat_output,
        initial_capacity=heat_output,
        output_profile=profile,
    )

    gas_boiler = Technology(name="ind_gas_boiler", commodity_in="Gas", commodity_out="Heat")
    gas_region_tech = RegionTechnology(
        technology=gas_boiler,
        initial_energy_output=other_output,
        initial_capacity=other_output,
        output_profile=profile,
    )

    region_technologies = [heat_region_tech, gas_region_tech]

    if include_extra:
        pv = Technology(name="pv_rooftop", commodity_in="Solar", commodity_out="Electricity")
        pv_region_tech = RegionTechnology(
            technology=pv,
            initial_energy_output=10.0,
            initial_capacity=10.0,
            output_profile=None,
        )
        region_technologies.append(pv_region_tech)

    region = Region(
        id_=1,
        polygon=None,
        region_demands=[region_demand],
        region_technologies=region_technologies,
    )
    return region


def test_min_heat_grid_rule_removes_small_output_and_rescales():
    region = _build_region(heat_output=5.0, other_output=95.0, include_extra=True)

    rule = MinimumHeatGridOutputRule(min_output_mwh=10.0, heat_grid_names=("heat_grid",))
    updated = rule.apply(region)

    heat_grid = next(rt for rt in updated.region_technologies if rt.technology.name == "heat_grid")
    gas = next(rt for rt in updated.region_technologies if rt.technology.name == "ind_gas_boiler")
    pv = next(rt for rt in updated.region_technologies if rt.technology.name == "pv_rooftop")

    assert heat_grid.initial_energy_output == pytest.approx(10.0)
    assert heat_grid.initial_capacity == pytest.approx(10.0)

    assert gas.initial_energy_output == pytest.approx(90.0)
    assert gas.initial_capacity == pytest.approx(90.0)

    assert pv.initial_energy_output == pytest.approx(10.0)
    assert pv.initial_capacity == pytest.approx(10.0)

    total_heat = sum(
        rt.initial_energy_output
        for rt in updated.region_technologies
        if rt.technology.commodity_out == "Heat"
    )
    assert total_heat == pytest.approx(100.0)


def test_min_heat_grid_rule_keeps_large_heat_grid():
    region = _build_region(heat_output=30.0, other_output=70.0)

    rule = MinimumHeatGridOutputRule(min_output_mwh=10.0, heat_grid_names=("heat_grid",))
    updated = rule.apply(region)

    heat_grid = next(rt for rt in updated.region_technologies if rt.technology.name == "heat_grid")
    gas = next(rt for rt in updated.region_technologies if rt.technology.name == "ind_gas_boiler")

    assert heat_grid.initial_energy_output == pytest.approx(30.0)
    assert gas.initial_energy_output == pytest.approx(70.0)


def test_min_heat_grid_rule_no_other_supply():
    demand = Demand(demand_type="residential_heat", commodity_in="Heat")
    profile = pd.Series([1.0, 1.0])
    region_demand = RegionDemand(demand=demand, value=5.0, profile=profile)

    heat_grid = Technology(name="heat_grid", commodity_in="Heat", commodity_out="Heat")
    heat_region_tech = RegionTechnology(
        technology=heat_grid,
        initial_energy_output=5.0,
        initial_capacity=5.0,
        output_profile=profile,
    )

    region = Region(
        id_=2,
        polygon=None,
        region_demands=[region_demand],
        region_technologies=[heat_region_tech],
    )

    rule = MinimumHeatGridOutputRule(min_output_mwh=10.0, heat_grid_names=("heat_grid",))
    updated = rule.apply(region)

    heat_grid_after = next(rt for rt in updated.region_technologies if rt.technology.name == "heat_grid")
    assert heat_grid_after.initial_energy_output == pytest.approx(5.0)
    assert heat_grid_after.initial_capacity == pytest.approx(5.0)


def test_min_heat_grid_constraint_rule_sets_targets():
    region = _build_region(heat_output=5.0, other_output=95.0)
    es = EnergySystem(name="TestES", regions=[region], units=None, connections=[])

    rule = MinimumHeatGridConstraintRule(min_share=0.1, heat_grid_names=("heat_grid",))
    updated = rule.apply(es)

    constraints = getattr(updated, "constraints", {})
    key = "min_heat_grid_residential_heat"
    assert key in constraints
    assert constraints[key][1] == pytest.approx(10.0)
