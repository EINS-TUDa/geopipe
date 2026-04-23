from __future__ import annotations

from pathlib import Path

import pytest

from pypeline.energy_system.core import Demand, EnergySystem, Region, RegionDemand, Scenario
from pypeline.optimization.cesm.backend import CESMOptimizationBackend
from pypeline.optimization.resolved_system import ResolvedSystem, resolve_system
from pypeline.units import UnitEnum


def _minimal_energy_system() -> EnergySystem:
    region = Region(
        0,
        region_demands=[RegionDemand(Demand("residential_heat", "residential_heat"), value=1.0)],
    )
    return EnergySystem(
        name="ES",
        regions=[region],
        units=UnitEnum.GW.unit,
        grid_prices={"electricity": 100.0, "export": 0.0},
        supply_prices={"gas": 50.0},
    )


def _minimal_scenario(*, with_schedule: bool) -> Scenario:
    return Scenario(
        name="ScenarioA",
        start_year=2020,
        end_year=2020,
        year_gap=1,
        discount_rate=0.05,
        lockout_years=0,
        retain_existing_output_schedule=([1.0] if with_schedule else None),
    )


def resolve_system_builds_payload_t() -> None:
    energy_system = _minimal_energy_system()
    scenario = _minimal_scenario(with_schedule=True)

    resolved = resolve_system(energy_system, scenario)

    assert isinstance(resolved, ResolvedSystem)
    assert resolved.scenario_years == [2020]
    assert resolved.region_ids == [0]
    assert resolved.demand_commodity == "residential_heat"
    assert resolved.annual_demand[0][2020] == pytest.approx(1.0)
    assert resolved.retain_schedule == [1.0]
    assert resolved.lockout_until_year == 2020
    assert resolved.grid_prices["electricity"] == pytest.approx(100.0)
    assert resolved.supply_prices["gas"] == pytest.approx(50.0)


def resolve_system_requires_schedule_t() -> None:
    energy_system = _minimal_energy_system()
    scenario = _minimal_scenario(with_schedule=False)

    with pytest.raises(ValueError, match="retain_existing_output_schedule"):
        resolve_system(energy_system, scenario)


def backend_materialize_uses_resolved_pipeline_t(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = CESMOptimizationBackend(
        model_name="ModelA",
        scenario_name="ScenarioA",
        tss_name="4Times",
        workdir=tmp_path,
    )
    energy_system = _minimal_energy_system()
    scenario = _minimal_scenario(with_schedule=True)

    calls: list[str] = []

    fake_resolved = ResolvedSystem(
        scenario_years=[2020],
        region_ids=[0],
        demand_commodity="residential_heat",
        annual_demand={0: {2020: 1.0}},
        demand_profile=[1.0 / 8760.0] * 8760,
        technologies={},
        constraints={},
        region_metrics={},
        historical_exchanger_targets_mwh={},
        retain_schedule=[1.0],
        discount_rate=0.05,
        lockout_until_year=2020,
        grid_prices={"electricity": 100.0, "export": 0.0},
        supply_prices={"gas": 50.0},
        pipe_connections=[],
        local_dhn_costs={},
        data_dir=None,
    )

    def _fake_resolve(*_args, **_kwargs):
        calls.append("resolve")
        return fake_resolved

    def _fake_write(*_args, **_kwargs):
        calls.append("write")

    monkeypatch.setattr("pypeline.optimization.cesm.backend.resolve_system", _fake_resolve)
    monkeypatch.setattr("pypeline.optimization.cesm.backend._write_cesm_inputs", _fake_write)

    backend._materialize_inputs_from_energy_system(energy_system, scenario, "residential_heat")

    assert calls == ["resolve", "write"]
