import math

import pytest
from pypeline.energy_technology.technology import Technology
from pypeline.optimization.cesm.conversion_rows import _ConversionRowsBuilder


def _profile_value_by_year(value) -> dict[int, float]:
    if value is None:
        return {}
    if isinstance(value, (int, float)):
        return {2020: float(value)}
    raw = str(value).strip()
    if not (raw.startswith("[") and raw.endswith("]")):
        return {}

    mapping: dict[int, float] = {}
    for chunk in raw[1:-1].split(";"):
        parts = chunk.strip().split()
        if len(parts) < 2:
            continue
        mapping[int(float(parts[0]))] = float(parts[1])
    return mapping


def _builder_with_targets(
    *,
    district: int,
    min_dhn_target: float = 0.0,
    min_heat_grid_target: float = 0.0,
    exchanger_target: float = 0.0,
    min_central_total: float = 0.0,
    lockout_years: int = 0,
) -> _ConversionRowsBuilder:
    min_central_totals = {district: min_central_total} if min_central_total > 0 else {}
    min_dhn_targets = {district: min_dhn_target} if min_dhn_target > 0 else {}
    min_heat_grid_targets = {district: min_heat_grid_target} if min_heat_grid_target > 0 else {}
    exchanger_targets = {district: exchanger_target} if exchanger_target > 0 else {}

    return _ConversionRowsBuilder(
        scenario_name="Base",
        scenario_years=[2020, 2025, 2030],
        demand_commodity=f"residential_heat_D{district}",
        districts=[district],
        district_index={district: 0},
        heat_names=[f"residential_heat_D{district}"],
        district_heat_in_names={district: f"district_heat_in_D{district}"},
        district_heat_out_names={district: f"district_heat_out_D{district}"},
        min_dhn_targets=min_dhn_targets,
        min_heat_grid_targets=min_heat_grid_targets,
        min_central_cap_targets={},
        min_central_cap_totals=min_central_totals,
        min_central_cap_totals_by_co={},
        exchanger_throughput_targets=exchanger_targets,
        district_to_region={district: district},
        region_metrics={},
        total_metrics={},
        retain_factor=None,
        retain_years_factor=None,
        retain_schedule=None,
        lockout_years=lockout_years,
        elec_price_eur_per_mwh=120.0,
    )


@pytest.mark.parametrize("district", [0, 6])
def min_dhn_target_t(district: int) -> None:
    """Checks min_dhn target does not force any conversion-row min_eout."""
    builder = _builder_with_targets(district=district, min_dhn_target=120.0)

    heat_grid = Technology(
        f"heat_grid_D{district}",
        f"district_heat_in_D{district}",
        f"district_heat_out_D{district}",
        cap_max=1000.0,
        max_units=10,
    )
    heat_exchanger = Technology(
        f"heat_exchanger_D{district}",
        f"district_heat_out_D{district}",
        f"residential_heat_D{district}",
        cap_max=1000.0,
        max_units=10,
    )

    heat_grid_row = builder.rows_for_technology(heat_grid)[0]
    heat_exchanger_row = builder.rows_for_technology(heat_exchanger)[0]

    assert heat_grid_row.get("min_eout") is None
    assert heat_exchanger_row.get("min_eout") is None


@pytest.mark.parametrize("district", [0, 6])
def min_grid_target_t(district: int) -> None:
    """Checks min_heat_grid target does not force any conversion-row min_eout."""
    builder = _builder_with_targets(district=district, min_heat_grid_target=80.0)

    heat_grid = Technology(
        f"heat_grid_D{district}",
        f"district_heat_in_D{district}",
        f"district_heat_out_D{district}",
        cap_max=1000.0,
        max_units=10,
    )
    heat_exchanger = Technology(
        f"heat_exchanger_D{district}",
        f"district_heat_out_D{district}",
        f"residential_heat_D{district}",
        cap_max=1000.0,
        max_units=10,
    )

    heat_grid_row = builder.rows_for_technology(heat_grid)[0]
    heat_exchanger_row = builder.rows_for_technology(heat_exchanger)[0]

    assert heat_grid_row.get("min_eout") is None
    assert heat_exchanger_row.get("min_eout") is None


@pytest.mark.parametrize("district", [0, 6])
def exchanger_target_floor_t(district: int) -> None:
    """Checks historical exchanger targets do not force conversion-row min_eout."""
    builder = _builder_with_targets(
        district=district,
        min_dhn_target=120.0,
        min_heat_grid_target=150.0,
        exchanger_target=220.0,
    )

    heat_grid = Technology(
        f"heat_grid_D{district}",
        f"district_heat_in_D{district}",
        f"district_heat_out_D{district}",
        cap_max=1000.0,
        max_units=10,
    )
    heat_exchanger = Technology(
        f"heat_exchanger_D{district}",
        f"district_heat_out_D{district}",
        f"residential_heat_D{district}",
        cap_max=1000.0,
        max_units=10,
    )

    heat_grid_row = builder.rows_for_technology(heat_grid)[0]
    heat_exchanger_row = builder.rows_for_technology(heat_exchanger)[0]

    assert heat_grid_row.get("min_eout") is None
    assert heat_exchanger_row.get("min_eout") is None


@pytest.mark.parametrize("district", [0, 6])
def zero_targets_t(district: int) -> None:
    """Checks no minimum throughput is set when DHN and heat-grid targets are absent."""
    builder = _builder_with_targets(district=district)

    heat_exchanger = Technology(
        f"heat_exchanger_D{district}",
        f"district_heat_out_D{district}",
        f"residential_heat_D{district}",
        cap_max=1000.0,
        max_units=10,
    )

    heat_exchanger_row = builder.rows_for_technology(heat_exchanger)[0]
    assert heat_exchanger_row.get("min_eout") is None


@pytest.mark.parametrize("district", [0, 6])
def central_capmin_t(district: int) -> None:
    """Checks central technologies preserve their explicit input cap_min when no floor constraint is configured."""
    builder = _builder_with_targets(district=district)

    cen_hp = Technology(
        f"cen_heat_pump_D{district}",
        "electricity",
        f"district_heat_in_D{district}",
        cap_min=0.3,
        cap_max=20.0,
        max_units=10,
    )

    row = builder.rows_for_technology(cen_hp)[0]
    assert row.get("cap_min") == 0.3


@pytest.mark.parametrize("district", [0, 6])
def lockout_capmin_t(district: int) -> None:
    """Checks lockout suppresses first-year central cap_min when no historical capacity exists."""
    builder = _builder_with_targets(district=district, lockout_years=2)

    cen_hp = Technology(
        f"cen_heat_pump_D{district}",
        "electricity",
        f"district_heat_in_D{district}",
        cap_min=0.3,
        cap_max=20.0,
        max_units=10,
    )

    row = builder.rows_for_technology(cen_hp)[0]
    cap_min_profile = _profile_value_by_year(row.get("cap_min"))

    assert cap_min_profile[2020] == 0.0
    assert cap_min_profile[2025] == 0.3


@pytest.mark.parametrize("district", [0, 6])
def retained_central_no_forced_build_t(district: int) -> None:
    """Checks retained central rows keep post-lockout cap_min but do not keep post-lockout cap_max locks."""
    tech_name = f"cen_heat_pump_D{district}"
    builder = _ConversionRowsBuilder(
        scenario_name="Base",
        scenario_years=[2020, 2025, 2030],
        demand_commodity=f"residential_heat_D{district}",
        districts=[district],
        district_index={district: 0},
        heat_names=[f"residential_heat_D{district}"],
        district_heat_in_names={district: f"district_heat_in_D{district}"},
        district_heat_out_names={district: f"district_heat_out_D{district}"},
        min_dhn_targets={},
        min_heat_grid_targets={},
        min_central_cap_targets={},
        min_central_cap_totals={},
        min_central_cap_totals_by_co={},
        exchanger_throughput_targets={},
        district_to_region={district: district},
        region_metrics={
            district: {
                tech_name: {
                    "initial_capacity": 2.0,
                    "initial_energy_output": 0.0,
                }
            }
        },
        total_metrics={},
        retain_factor=None,
        retain_years_factor=None,
        retain_schedule=None,
        lockout_years=2,
        elec_price_eur_per_mwh=120.0,
    )

    cen_hp = Technology(
        tech_name,
        "electricity",
        f"district_heat_in_D{district}",
        cap_min=0.3,
        cap_max=20.0,
        max_units=10,
    )

    row = builder.rows_for_technology(cen_hp)[0]
    cap_min_profile = _profile_value_by_year(row.get("cap_min"))
    cap_max_profile = _profile_value_by_year(row.get("cap_max"))

    assert cap_min_profile[2020] == 0.0
    assert cap_min_profile[2025] == 0.3
    assert cap_max_profile[2020] == pytest.approx(2.0)
    assert math.isnan(cap_max_profile[2025])
