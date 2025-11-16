import math
from pathlib import Path

import geopandas as gpd
import pandas as pd

from pypeline import DataRegistry, EnergySystemBuilder, TechnologyRegistry
from pypeline.energy_system.rule_book import EnergySystemRuleBook, MinimumDHNThroughputRule
from pypeline.energy_system.scenario import Scenario
from pypeline.optimization.om_adapter import build_om_from_es
from tools.cesm_plugin import write_cesm_inputs_from_data


def _build_bensheim_om():
    tech_reg = TechnologyRegistry()
    tech_reg.load_from_default()
    data_reg = DataRegistry()
    data_reg.load_from_default()

    polygons_path = Path("data") / "wah_bensheim_4_districts.geojson"

    polygons = gpd.read_file(polygons_path)

    esb = EnergySystemBuilder(energy_system_name="BensheimTest")
    esb.set_polygons(polygons)
    esb.set_technology_dependency_manager(default=True)
    esb.set_demands(default=True)
    esb.set_technology_registry(tech_reg)
    esb.set_data_registry(data_reg)

    esb.set_default_region_builder_config()
    esb.region_builder_config.update({
        "min_heat_grid_share": 0.35,
        "heat_grid_names": ("heat_exchanger",),
    })

    rulebook = EnergySystemRuleBook()
    rulebook.add_rule(MinimumDHNThroughputRule(demand_name="residential_heat", min_share=0.20))
    esb.set_energy_system_rule_book(rulebook)

    es = esb.build()

    scenario = Scenario(name="BensheimTestScenario", start_year=2020, end_year=2030, year_gap=5, tss="4ThinWeeks")
    om = build_om_from_es(es, scenario, demand_name="residential_heat")
    return om, tech_reg, polygons_path


def _profile_peak(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip()
    if not raw:
        return None
    if not raw.startswith("[") or not raw.endswith("]"):
        try:
            return float(raw)
        except ValueError:
            return None
    peaks = []
    body = raw[1:-1]
    for chunk in body.split(";"):
        parts = chunk.strip().split()
        if len(parts) < 2:
            continue
        try:
            peaks.append(float(parts[1]))
        except ValueError:
            continue
    return max(peaks) if peaks else None


def test_cap_reserves_do_not_exceed_cap_max(tmp_path):
    om, tech_reg, polygons_path = _build_bensheim_om()

    workdir = tmp_path / "cesm"
    write_cesm_inputs_from_data(
        om,
        workdir=workdir,
        model_name="BensheimTest",
        scenario_name="Base",
        tss_name="4ThinWeeks",
        polygons_path=polygons_path,
        data_dir=Path("data"),
        retain_existing_output_factor=0.0,
        technology_registry=tech_reg,
    )

    xlsx_path = workdir / "Data" / "Techmap" / "BensheimTest.xlsx"
    assert xlsx_path.exists()

    df = pd.read_excel(xlsx_path, sheet_name="ConversionSubProcess")

    for _, row in df.iterrows():
        cap_max = row.get("cap_max")
        cap_max_peak = _profile_peak(cap_max)
        if cap_max_peak is None:
            continue

        cap_min_peak = _profile_peak(row.get("cap_min"))
        if cap_min_peak is not None:
            assert cap_min_peak <= cap_max_peak + 1e-6

        for key in ("cap_res_min", "cap_res_max"):
            res_peak = _profile_peak(row.get(key))
            if res_peak is not None:
                assert res_peak <= cap_max_peak + 1e-6

    central_rows = df[df["conversion_process_name"].str.contains("cen_heat_pump", na=False)]
    if not central_rows.empty:
        central_caps = sorted({round(_profile_peak(value) or 0.0, 6) for value in central_rows["cap_max"].dropna()})
        assert central_caps == [20.0]
