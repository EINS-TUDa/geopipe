from dataclasses import asdict, dataclass, fields

import pandas as pd

from pypeline.energy_system.core import RegionDemand
from pypeline.energy_system.imports import Imports
from pypeline.optimization.cesm.conversion_sub_process import ConversionSubProcess
from pypeline.optimization.cesm.io_utils_new import year_dep_value_to_cesm_string
from pypeline.optimization.resolved_system import ResolvedSystem


@dataclass(frozen=True)
class Techmap:
    Units: pd.DataFrame
    Scenario: pd.DataFrame
    Commodity: pd.DataFrame
    ConversionProcess: pd.DataFrame
    ConversionSubProcess: pd.DataFrame
    TSS: pd.DataFrame

    def to_excel(self, path: str):
        # the attribute names are used as sheet names
        with pd.ExcelWriter(path) as writer:
            for field in fields(self):
                df = getattr(self, field.name)
                df.to_excel(writer, sheet_name=field.name, index=False)


def _units_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"quantity": "energy",      "scale_factor": 1.0, "input": "MWh",      "output": "MWh"},
            {"quantity": "power",       "scale_factor": 1.0, "input": "MW",        "output": "MW"},
            {"quantity": "cost_energy", "scale_factor": 1.0, "input": "EUR/MWh",   "output": "EUR/MWh"},
            {"quantity": "cost_power",  "scale_factor": 1.0, "input": "EUR/MW",    "output": "EUR/MW"},
            {"quantity": "co2_spec",    "scale_factor": 1.0, "input": "tCO2/MWh",  "output": "tCO2/MWh"},
        ],
        columns=["quantity", "scale_factor", "input", "output"],
    )


def _tss_df(tss_name: str, dt_hours: int) -> pd.DataFrame:
    return pd.DataFrame([{"TSS_name": tss_name, "dt": int(dt_hours)}], columns=["TSS_name", "dt"])

def _scenario_df(resolved: ResolvedSystem) -> pd.DataFrame:
    s = resolved.scenario
    return pd.DataFrame(
        [{
            "scenario_name": s.name,
            "from_year": s.start_year,
            "until_year": s.end_year,
            "year_step": s.year_gap,
            "discount_rate": s.discount_rate,
            "TSS": s.tss,
            "annual_co2_limit": year_dep_value_to_cesm_string(s.co2_limit),
            "co2_price": year_dep_value_to_cesm_string(s.co2_price),
        }]
    )

def _demand_to_conversion_sub_process(
    rd: RegionDemand,
    region_id: int,
    scenario_name: str,
) -> ConversionSubProcess:
    return ConversionSubProcess(
        conversion_process_name=f"{rd.name}_{region_id}",
        commodity_in=rd.demand.commodity_in,
        commodity_out="Dummy",
        scenario=scenario_name,
        min_eout=year_dep_value_to_cesm_string(rd.value),
        output_profile=rd.profile_name,
    )


def _import_to_conversion_sub_process(imp: Imports, scenario_name: str) -> ConversionSubProcess:
    return ConversionSubProcess(
        conversion_process_name=imp.name,
        commodity_in="Dummy",
        commodity_out=imp.commodity_out,
        scenario=scenario_name,
        opex_cost_energy=year_dep_value_to_cesm_string(imp.price_eur_per_mwh),
        spec_co2=year_dep_value_to_cesm_string(imp.co2_emissions_ton_per_mwh),
        cap_max=year_dep_value_to_cesm_string(imp.max_capacity_mw),
    )


def _conversion_sub_process_df(resolved: ResolvedSystem) -> pd.DataFrame:
    cs_list: list[ConversionSubProcess] = []
    scenario_name = resolved.scenario.name

    for region_id, demands in resolved.region_demands.items():
        for rd in demands:
            cs_list.append(_demand_to_conversion_sub_process(rd, region_id, scenario_name))

    for imp in resolved.imports:
        cs_list.append(_import_to_conversion_sub_process(imp, scenario_name))

    # heat grids

    return pd.DataFrame([asdict(cs) for cs in cs_list])





def create_techmap(resolved: ResolvedSystem) -> Techmap:
    return Techmap(
        Units=_units_df(),
        Scenario=_scenario_df(resolved),
        Commodity=pd.DataFrame(),
        ConversionProcess=pd.DataFrame(),
        ConversionSubProcess=_conversion_sub_process_df(resolved),
        TSS=_tss_df(resolved.scenario.tss, resolved.scenario.dt_hours),
    )
