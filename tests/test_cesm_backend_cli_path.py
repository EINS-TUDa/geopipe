from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from pypeline.energy_system.core import EnergySystem, Scenario
from pypeline.energy_system.imports import Imports
from pypeline.optimization.cesm.backend import CESMOptimizationBackend
from pypeline.optimization.solver import Results
from pypeline.units import UnitEnum


def _minimal_energy_system() -> EnergySystem:
    return EnergySystem(
        name="ES",
        regions=[],
        units=UnitEnum.GW.unit,
        imports=[
            Imports(commodity_out="electricity", price_eur_per_mwh=100.0),
            Imports(commodity_out="gas", price_eur_per_mwh=50.0),
        ],
    )


def _minimal_scenario() -> Scenario:
    return Scenario(
        name="ScenarioA",
        start_year=2020,
        end_year=2020,
        year_gap=1,
        dt_hours=1,
        lockout_years=0,
        retain_existing_output_schedule=[1.0],
    )


def solve_uses_cli_pipeline_and_db_handoff_t(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = CESMOptimizationBackend(
        model_name="ModelA",
        scenario_name="ScenarioA",
        tss_name="4Times",
        workdir=tmp_path,
    )

    calls: list[tuple[str, object]] = []

    def fake_materialize_inputs(energy_system: EnergySystem, scenario: Scenario, demand_name: str) -> None:
        calls.append(("materialize", demand_name))

    def fake_run_cli(extra_args: list[str] | None = None) -> None:
        calls.append(("run_cli", extra_args))
        run_dir = tmp_path / "Runs" / "ModelA-ScenarioA"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "db.sqlite").write_text("stub", encoding="utf-8")

    def fake_backfill(db_path: Path) -> None:
        calls.append(("backfill", Path(db_path)))

    fake_results = Results(
        active_capacities=pd.DataFrame(),
        yearly_energy_outputs=pd.DataFrame(),
        opex=None,
        capex=None,
        totex=None,
    )

    def fake_parse(db_path: Path) -> Results:
        calls.append(("parse", Path(db_path)))
        return fake_results

    monkeypatch.setattr(backend, "_materialize_inputs_from_energy_system", fake_materialize_inputs)
    monkeypatch.setattr(backend, "_run_cli", fake_run_cli)
    monkeypatch.setattr("pypeline.optimization.cesm.backend.backfill_missing_commodity_timeseries", fake_backfill)
    monkeypatch.setattr("pypeline.optimization.cesm.backend.parse_cesm_outputs", fake_parse)

    solution = backend.solve(_minimal_energy_system(), _minimal_scenario())

    assert [name for name, _ in calls] == ["materialize", "run_cli", "backfill", "parse"]
    expected_db = tmp_path / "Runs" / "ModelA-ScenarioA" / "db.sqlite"
    assert calls[2][1] == expected_db
    assert calls[3][1] == expected_db
    assert solution.results is fake_results


def expected_run_db_requires_run_subdir_t(tmp_path: Path) -> None:
    backend = CESMOptimizationBackend(
        model_name=None,
        scenario_name=None,
        tss_name="4Times",
        workdir=tmp_path,
    )

    with pytest.raises(ValueError, match="run_subdir"):
        backend._expected_run_db()


def solve_raises_when_cli_does_not_write_db_t(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = CESMOptimizationBackend(
        model_name="ModelA",
        scenario_name="ScenarioA",
        tss_name="4Times",
        workdir=tmp_path,
    )

    monkeypatch.setattr(backend, "_materialize_inputs_from_energy_system", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(backend, "_run_cli", lambda *_args, **_kwargs: None)

    with pytest.raises(FileNotFoundError, match="Expected DB not found"):
        backend.solve(_minimal_energy_system(), _minimal_scenario())
