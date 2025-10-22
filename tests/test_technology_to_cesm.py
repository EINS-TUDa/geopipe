import math

from pypeline.energy_system.technology import Technology
from tools.cesm_plugin import tech_to_cesms_row


class DummyTech(Technology):
    pass


def test_to_cesms_row_defaults():
    tech = DummyTech(
        name="TestTech",
        commodity_in="Electricity",
        commodity_out="Heat",
        efficiency=0.8,
        technical_lifetime=25,
        opex_cost_energy=1.5,
        opex_cost_power=10.0,
        capex_cost_power=1000.0,
    )
    row = tech_to_cesms_row(
        tech,
        cp_name="TestTech",
        cin="Electricity",
        cout="Heat",
        scenario_name="S",
    )
    assert row["conversion_process_name"] == "TestTech"
    assert row["commodity_in"] == "Electricity"
    assert row["commodity_out"] == "Heat"
    assert math.isclose(row["efficiency"], 0.8)
    assert int(row["technical_lifetime"]) == 25
    assert math.isclose(row["opex_cost_energy"], 1.5)
    assert math.isclose(row["capex_cost_power"], 1000.0)


def test_to_cesms_row_overrides():
    tech = DummyTech(name="Pipe", commodity_in="Heat_D0", commodity_out="Heat_D1", efficiency=0.99)
    row = tech_to_cesms_row(
        tech,
        cp_name="Pipe_D0_D1",
        cin="Heat_D0",
        cout="Heat_D1",
        scenario_name="S",
        cap_max=10.0,
        capex_cost_power=50000.0,
    )
    assert row["conversion_process_name"] == "Pipe_D0_D1"
    assert row["cap_max"] == 10.0
    assert row["capex_cost_power"] == 50000.0
