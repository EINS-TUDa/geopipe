import math
import pytest

from pypeline.energy_technology.technology import Technology
from pypeline.optimization.cesm.conversion_rows import tech_to_cesms_row


class DummyTech(Technology):
    pass


def row_defaults_t():
    """Checks default technology-to-CESM row mapping populates expected baseline fields."""
    tech = DummyTech(
        name="TestTech",
        commodity_in="Electricity",
        commodity_out="Heat",
        efficiency=0.8,
        technical_lifetime=25,
        opex_cost_energy=1.5,
        opex_cost_power=10.0,
        capex_cost_power=1000.0,
        cap_max=10.0,
        max_units=10,
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
    assert math.isclose(row["capex_cost_base"], 0.0)
    assert row["cap_active"] is None


@pytest.mark.parametrize("src_district,dst_district", [(0, 1), (2, 5)])
def row_overrides_t(src_district: int, dst_district: int):
    """Checks explicit overrides are respected in technology-to-CESM row mapping."""
    cin = f"Heat_D{src_district}"
    cout = f"Heat_D{dst_district}"
    cp_name = f"Pipe_D{src_district}_D{dst_district}"
    tech = DummyTech(
        name="Pipe",
        commodity_in=cin,
        commodity_out=cout,
        efficiency=0.99,
        capex_cost_base=2500.0,
        cap_max=10.0,
        max_units=10,
    )
    row = tech_to_cesms_row(
        tech,
        cp_name=cp_name,
        cin=cin,
        cout=cout,
        scenario_name="S",
        cap_max=10.0,
        capex_cost_power=50000.0,
    )
    assert row["conversion_process_name"] == cp_name
    assert row["cap_max"] == 10.0
    assert row["capex_cost_power"] == 50000.0
    assert math.isclose(row["capex_cost_base"], 2500.0)
    assert row["cap_active"] is None
