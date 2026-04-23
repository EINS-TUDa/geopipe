import pandas as pd
import pytest
from pypeline.energy_system.core import Demand, EnergySystem, Region, RegionDemand, Scenario
from pypeline.energy_system.region_connection import RegionConnection
from pypeline.energy_technology.technology import RegionTechnology, Technology
from pypeline.optimization.resolved_system import resolve_system
from pypeline.units import UnitKW


def _flat_profile() -> pd.Series:
    return pd.Series([1.0 / 8760.0] * 8760)


def _scenario(start=2020, end=2025, gap=5, lockout=0) -> Scenario:
    return Scenario(name="Test", start_year=start, end_year=end, year_gap=gap, dt_hours=1, discount_rate=0.05, lockout_years=lockout)


def _es(
    regions: list,
    grid_prices=None,
    supply_prices=None,
    region_connections=None,
) -> EnergySystem:
    return EnergySystem(
        name="test",
        regions=regions,
        units=UnitKW(),
        grid_prices=grid_prices or {"electricity": 100.0, "export": 0.0},
        supply_prices=supply_prices or {"gas": 50.0},
        pipes=region_connections or [],
    )


def _demand(commodity="residential_heat", value=500.0) -> RegionDemand:
    return RegionDemand(
        demand=Demand(demand_type=commodity, commodity_in=commodity),
        value=value,
        profile=_flat_profile(),
    )


def _region(rid: int, demands=None, techs=None) -> Region:
    return Region(id_=rid, region_demands=demands or [_demand()], region_technologies=techs or [])


def _conn(id_in: int, id_out: int, **kwargs) -> RegionConnection:
    return RegionConnection(region_id_in=id_in, region_id_out=id_out, pipe_length_km=1.0, **kwargs)


def scenario_years_t():
    resolved = resolve_system(_es([_region(0)]), _scenario(2020, 2030, 5))
    assert resolved.scenario_years == [2020, 2025, 2030]


def single_region_annual_demand_t():
    resolved = resolve_system(_es([_region(0, demands=[_demand(value=1234.0)])]), _scenario())
    assert resolved.annual_demand[0][2020] == pytest.approx(1234.0)


def demand_commodity_derived_from_first_demand_t():
    resolved = resolve_system(_es([_region(0, demands=[_demand("residential_heat")])]), _scenario())
    assert resolved.demand_commodity == "residential_heat"


def multiple_commodities_only_first_counted_t():
    """When a region has two demands, only the one matching derived_commodity is summed."""
    elec = RegionDemand(
        demand=Demand(demand_type="electricity", commodity_in="electricity"),
        value=3500.0,
        profile=_flat_profile(),
    )
    heat = _demand("residential_heat", value=57.0)
    region = _region(0, demands=[heat, elec])
    resolved = resolve_system(_es([region]), _scenario())
    assert resolved.demand_commodity == "residential_heat"
    assert resolved.annual_demand[0][2020] == pytest.approx(57.0)


def demand_profile_extracted_t():
    resolved = resolve_system(_es([_region(0)]), _scenario())
    assert len(resolved.demand_profile) == 8760
    assert sum(resolved.demand_profile) == pytest.approx(1.0, rel=1e-6)


def region_metrics_t():
    tech = Technology("boiler", "gas", "residential_heat")
    rt = RegionTechnology(tech, initial_capacity=10.0, initial_energy_output=500.0)
    region = _region(0, techs=[rt])
    resolved = resolve_system(_es([region]), _scenario())
    assert resolved.region_metrics[0]["boiler"]["initial_capacity"] == pytest.approx(10.0)
    assert resolved.region_metrics[0]["boiler"]["initial_energy_output"] == pytest.approx(500.0)


def prices_passed_through_t():
    es = _es([_region(0)], grid_prices={"electricity": 150.0, "export": 5.0}, supply_prices={"gas": 40.0})
    resolved = resolve_system(es, _scenario())
    assert resolved.grid_prices["electricity"] == pytest.approx(150.0)
    assert resolved.supply_prices["gas"] == pytest.approx(40.0)


def retain_schedule_from_explicit_t():
    resolved = resolve_system(_es([_region(0)]), _scenario(), retain_existing_output_schedule=[1.0, 0.9])
    assert resolved.retain_schedule == [1.0, 0.9]


def retain_schedule_from_scenario_field_t():
    scenario = _scenario()
    scenario.retain_existing_output_schedule = [0.8, 0.7]
    resolved = resolve_system(_es([_region(0)]), scenario)
    assert resolved.retain_schedule == [0.8, 0.7]


def no_connections_gives_empty_pipe_connections_t():
    resolved = resolve_system(_es([_region(0)]), _scenario())
    assert resolved.pipe_connections == []


def pipe_connections_passed_through_t():
    conns = [_conn(0, 1), _conn(1, 0)]
    es = _es([_region(0), _region(1)], region_connections=conns)
    resolved = resolve_system(es, _scenario())
    assert len(resolved.pipe_connections) == 2
    assert resolved.pipe_connections[0].region_id_in == 0
    assert resolved.pipe_connections[1].region_id_in == 1


def below_threshold_zeroes_costs_t():
    rc = _conn(0, 1, pipe_capex_base_eur=999.0, pipe_capex_eur_per_mw=50.0,
               pipe_opex_eur_per_mwh=2.0, below_distance_threshold=True)
    assert rc.pipe_capex_base_eur == pytest.approx(0.0)
    assert rc.pipe_capex_eur_per_mw == pytest.approx(0.0)
    assert rc.pipe_opex_eur_per_mwh == pytest.approx(0.0)


def lockout_years_and_discount_rate_t():
    resolved = resolve_system(_es([_region(0)]), _scenario(lockout=3))
    assert resolved.lockout_years == 3
    assert resolved.discount_rate == pytest.approx(0.05)


def no_profile_raises_t():
    rd = RegionDemand(demand=Demand(demand_type="heat", commodity_in="heat"), value=100.0, profile=None)
    region = _region(0, demands=[rd])
    with pytest.raises(ValueError, match="No 8760-hour demand profile"):
        resolve_system(_es([region]), _scenario())
