from dataclasses import asdict, dataclass, fields
from pathlib import Path

import pandas as pd

from ...energy_system.units import Unit
from ...energy_system.region import Demand
from ...energy_system.imports import Import
from ...energy_system.technology import PipeTechnology, GridTechnology, CentralTechnology, CHPTechnology, \
    DecentralTechnology, Technology
from ...energy_system.units import UnitKW, UnitGW, UnitMW
from ...optimization.cesm.conversion_sub_process import ConversionSubProcess
from ...optimization.resolved_system import ResolvedSystem

import logging

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class Techmap:
    Units: pd.DataFrame
    Scenario: pd.DataFrame
    Commodity: pd.DataFrame
    ConversionProcess: pd.DataFrame
    ConversionSubProcess: pd.DataFrame
    TSS: pd.DataFrame

    def to_excel(self, path: Path):
        # the attribute names are used as sheet names
        logger.info(f"Writing techmap to {path}")
        with pd.ExcelWriter(path) as writer:
            for field in fields(self):
                df = getattr(self, field.name)
                if field.name == "ConversionSubProcess":
                    # Write 2 empty rows before the actual data
                    empty_df = pd.DataFrame(index=range(2), columns=df.columns)
                    empty_df.to_excel(writer, sheet_name=field.name, index=False, header=True)
                    df.to_excel(writer, sheet_name=field.name, index=False, header=False, startrow=3)
                else:
                    df.to_excel(writer, sheet_name=field.name, index=False)

def commodity_name(name: str, region_id: int) -> str:
    """Return the region-suffixed commodity name if it is transported via a grid; bare name otherwise."""
    if Technology.is_grid_commodity(name):
        return f"{name}_D{region_id}"
    return name

def year_dep_value_to_cesm_string(value: float | dict[int, float | None] | None) -> float | str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        if all(v is None for v in value.values()): # if all values in dict are None, return None
            return None
        segments = []
        for year, year_value in sorted(value.items()):
            year_i = int(year)
            if year_value is None:
                segments.append(f"{year_i} NaN")
            else:
                segments.append(f"{year_i} {float(year_value):.5g}")
        return "[" + ";".join(segments) + "]"
    raise TypeError(f"Unsupported type for year-dependent value: {type(value).__name__}")


def _df_units(unit: Unit) -> pd.DataFrame:
    if isinstance(unit, UnitKW):
        return pd.DataFrame(
            [
                {"quantity": "power", "input": "kW", "scale_factor": 1, "output": "kW"},
                {"quantity": "energy", "input": "MWh", "scale_factor": 1000, "output": "kWh"},
                {"quantity": "co2_emissions", "input": "t", "scale_factor": 1, "output": "t"},
                {"quantity": "cost_energy", "input": "EUR/MWh", "scale_factor": 0.001, "output": "EUR/kWh"},
                {"quantity": "cost_power", "input": "EUR/kW", "scale_factor": 1, "output": "EUR/kW"},
                {"quantity": "co2_spec", "input": "kg/kWh", "scale_factor": 0.001, "output": "t/kWh"},
                {"quantity": "money", "input": "EUR", "scale_factor": 1, "output": "EUR"},
            ],
            columns=["quantity", "input", "scale_factor", "output"],
        )
    elif isinstance(unit, UnitGW):
        return pd.DataFrame(
            [
                {"quantity": "power", "input": "GW", "scale_factor": 1, "output": "GW"},
                {"quantity": "energy", "input": "TWh", "scale_factor": 1000, "output": "GWh"},
                {"quantity": "co2_emissions", "input": "Mio t", "scale_factor": 1, "output": "Mio t"},
                {"quantity": "cost_energy", "input": "EUR/MWh", "scale_factor": 0.001, "output": "Mio EUR/GWh"},
                {"quantity": "cost_power", "input": "EUR/kW", "scale_factor": 1, "output": "Mio EUR/GW"},
                {"quantity": "co2_spec", "input": "kg/kWh", "scale_factor": 0.001, "output": "Mio t/GWh"},
                {"quantity": "money", "input": "Mio EUR", "scale_factor": 1, "output": "Mio EUR"},
            ],
            columns=["quantity", "input", "scale_factor", "output"],
        )
    elif isinstance(unit, UnitMW):
        return pd.DataFrame(
            [
                {"quantity": "power", "input": "MW", "scale_factor": 1, "output": "MW"},
                {"quantity": "energy", "input": "GWh", "scale_factor": 1000, "output": "MWh"},
                {"quantity": "co2_emissions", "input": "kilo t", "scale_factor": 1, "output": "kilo t"},
                {"quantity": "cost_energy", "input": "EUR/MWh", "scale_factor": 0.001, "output": "k EUR/MWh"},
                {"quantity": "cost_power", "input": "EUR/kW", "scale_factor": 1, "output": "k EUR/MW"},
                {"quantity": "co2_spec", "input": "kg/kWh", "scale_factor": 0.001, "output": "kilo t/MWh"},
                {"quantity": "money", "input": "k EUR", "scale_factor": 1, "output": "k EUR"},
            ],
            columns=["quantity", "input", "scale_factor", "output"],
        )
    raise TypeError(f"Unsupported unit type: {type(unit).__name__}")


def _df_tss(tss_name: str, dt_hours: int) -> pd.DataFrame:
    return pd.DataFrame([{"TSS_name": tss_name, "dt": int(dt_hours)}], columns=["TSS_name", "dt"])

def _df_scenario(resolved: ResolvedSystem) -> pd.DataFrame:
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

def _import_to_conversion_sub_process(imp: Import, scenario_name: str) -> ConversionSubProcess:
    return ConversionSubProcess(
        conversion_process_name=imp.name,
        commodity_in="Dummy",
        commodity_out=imp.commodity_out,
        scenario=scenario_name,
        opex_cost_energy=year_dep_value_to_cesm_string(imp.price_eur_per_mwh),
        spec_co2=year_dep_value_to_cesm_string(imp.co2_emissions_ton_per_mwh),
        cap_max=year_dep_value_to_cesm_string(imp.max_capacity_mw),
    )

def _pipe_to_conversion_sub_process(pipe: PipeTechnology, scenario_name: str, start_year: int) -> ConversionSubProcess:
    return ConversionSubProcess(
        conversion_process_name=f"{pipe.name}_D{pipe.region_id_in}_D{pipe.region_id_out}",
        commodity_in=commodity_name(pipe.commodity_in, pipe.region_id_in),
        commodity_out=commodity_name(pipe.commodity_out, pipe.region_id_out),
        scenario=scenario_name,
        efficiency=pipe.efficiency,
        technical_lifetime=pipe.technical_lifetime,
        capex_cost_base=pipe.investment_costs_eur,
        cap_max=year_dep_value_to_cesm_string(pipe.max_capacity_per_year(start_year)),
        cap_res_min=year_dep_value_to_cesm_string(pipe.capacity_per_year(start_year)),
        cap_res_max=year_dep_value_to_cesm_string(pipe.capacity_per_year(start_year)),
    )

def _demand_to_conversion_sub_process(
    demand: Demand,
    region_id: int,
    scenario_name: str,
    scenario_years: list[int]) -> ConversionSubProcess:
    return ConversionSubProcess(
        conversion_process_name=f"{demand.name}_D{region_id}",
        commodity_in=commodity_name(demand.demand_type.commodity_in, region_id),
        commodity_out="Dummy",
        scenario=scenario_name,
        min_eout=year_dep_value_to_cesm_string(demand.values_per_year(scenario_years)),
        output_profile=demand.profile_name,
    )

def _decentralized_tech_to_conversion_sub_process(tech: DecentralTechnology, region_id: int, scenario_name: str,
                                                  start_year: int) -> ConversionSubProcess:
    return ConversionSubProcess(
        conversion_process_name=f"{tech.name}_D{region_id}",
        commodity_in=commodity_name(tech.commodity_in, region_id),
        commodity_out=commodity_name(tech.commodity_out, region_id),
        scenario=scenario_name,
        efficiency=tech.efficiency,
        technical_lifetime=tech.technical_lifetime,
        opex_cost_energy=year_dep_value_to_cesm_string(tech.opex_cost_energy),
        opex_cost_power=year_dep_value_to_cesm_string(tech.opex_cost_power),
        capex_cost_power=year_dep_value_to_cesm_string(tech.capex_cost_power),
        cap_res_min=year_dep_value_to_cesm_string(tech.capacity_per_year(start_year)),
        cap_res_max=year_dep_value_to_cesm_string(tech.capacity_per_year(start_year)),
        cap_max=year_dep_value_to_cesm_string(tech.max_capacity_per_year(start_year)),
        output_profile=tech.output_profile_name,
    )

def _grid_to_conversion_sub_process(grid: GridTechnology, region_id, scenario_name: str, start_year: int) -> ConversionSubProcess:
    return ConversionSubProcess(
        conversion_process_name=f"{grid.name}_D{region_id}",
        commodity_in=commodity_name(grid.commodity_in, region_id),
        commodity_out=commodity_name(grid.commodity_out, region_id),
        scenario=scenario_name,
        efficiency=grid.efficiency,
        technical_lifetime=grid.technical_lifetime,
        capex_cost_base=grid.investment_costs_eur,
        cap_max=year_dep_value_to_cesm_string(grid.max_capacity_per_year(start_year)),
        cap_res_min=year_dep_value_to_cesm_string(grid.capacity_per_year(start_year)),
        cap_res_max=year_dep_value_to_cesm_string(grid.capacity_per_year(start_year)),
    )



def _central_tech_to_conversion_sub_process(tech: CentralTechnology, region_id: int, scenario_name: str, start_year: int) -> list[ConversionSubProcess]:
    if isinstance(tech, CHPTechnology):
        return _chp_to_conversion_sub_process(tech, region_id, scenario_name, start_year)
    return [ConversionSubProcess(
        conversion_process_name=f"{tech.name}_D{region_id}",
        commodity_in=commodity_name(tech.commodity_in, region_id),
        commodity_out=commodity_name(tech.commodity_out, region_id),
        scenario=scenario_name,
        efficiency=tech.efficiency,
        technical_lifetime=tech.technical_lifetime,
        opex_cost_energy=year_dep_value_to_cesm_string(tech.opex_cost_energy),
        opex_cost_power=year_dep_value_to_cesm_string(tech.opex_cost_power),
        capex_cost_power=year_dep_value_to_cesm_string(tech.capex_cost_power),
        capex_cost_base=year_dep_value_to_cesm_string(tech.capex_cost_base),
        cap_max=year_dep_value_to_cesm_string(tech.max_capacity_per_year(start_year)),
        cap_res_min=year_dep_value_to_cesm_string(tech.capacity_per_year(start_year)),
        cap_res_max=year_dep_value_to_cesm_string(tech.capacity_per_year(start_year)),
        output_profile=tech.output_profile_name,
        availability_profile=tech.availability_profile_name,
    )]

def _chp_to_conversion_sub_process(chp: CHPTechnology, region_id: int, scenario_name: str, start_year) -> list[ConversionSubProcess]:
    chp_name = f"{chp.name}_D{region_id}"
    cs_import = ConversionSubProcess(
        conversion_process_name=chp_name,
        commodity_in=commodity_name(chp.commodity_in, region_id),
        commodity_out=f"Help_{chp_name}",
        scenario=scenario_name,
    )
    cs_loss = ConversionSubProcess(
        conversion_process_name=chp_name,
        commodity_in=f"Help_{chp_name}",
        commodity_out="Dummy",
        scenario=scenario_name,
        in_frac_min=chp.loss,
    )
    cs_commodity_out = ConversionSubProcess(
        conversion_process_name=chp_name,
        commodity_in=f"Help_{chp_name}",
        commodity_out=commodity_name(chp.commodity_out, region_id),
        scenario=scenario_name,
        technical_lifetime=chp.technical_lifetime,
        in_frac_min=chp.efficiency,
        in_frac_max=chp.efficiency,
        opex_cost_energy=year_dep_value_to_cesm_string(chp.opex_cost_energy),
        opex_cost_power=year_dep_value_to_cesm_string(chp.opex_cost_power),
        capex_cost_power=year_dep_value_to_cesm_string(chp.capex_cost_power),
        capex_cost_base=year_dep_value_to_cesm_string(chp.capex_cost_base),
        cap_max=year_dep_value_to_cesm_string(chp.max_capacity_per_year(start_year)),
        cap_res_max=year_dep_value_to_cesm_string(chp.capacity_per_year(start_year)),
        cap_res_min=year_dep_value_to_cesm_string(chp.capacity_per_year(start_year)),
        output_profile=chp.output_profile_name,
        availability_profile=chp.availability_profile_name,
    )
    cs_commodity_out_2 = ConversionSubProcess(
        conversion_process_name=chp_name,
        commodity_in=f"Help_{chp_name}",
        commodity_out=commodity_name(chp.commodity_out_2, region_id),
        scenario=scenario_name
    )
    return [cs_import, cs_loss, cs_commodity_out, cs_commodity_out_2]

def _conversion_sub_process_df(resolved: ResolvedSystem) -> pd.DataFrame:
    cs_list: list[ConversionSubProcess] = []
    scenario_name = resolved.scenario.name
    scenario_years = resolved.scenario.years
    start_year = resolved.scenario.start_year

    # imports
    for imp in resolved.imports:
        cs_list.append(_import_to_conversion_sub_process(imp, scenario_name))

    # pipes
    for pipe in resolved.pipe_connections:
        cs_list.append(_pipe_to_conversion_sub_process(pipe, scenario_name, start_year))

    # demands
    for region_id, demands in resolved.demands.items():
        for demand in demands:
            cs_list.append(_demand_to_conversion_sub_process(demand, region_id, scenario_name, scenario_years))

    # decentralized techs
    for region_id, techs in resolved.decentralized_technologies.items():
        for tech in techs:
            cs_list.append(_decentralized_tech_to_conversion_sub_process(tech, region_id, scenario_name, start_year))

    # grids
    for region_id, grids in resolved.grid_technologies.items():
        for grid in grids:
            cs_list.append(_grid_to_conversion_sub_process(grid, region_id, scenario_name, start_year))

    # central techs
    for region_id, techs in resolved.central_technologies.items():
        for tech in techs:
            cs_list.extend(_central_tech_to_conversion_sub_process(tech, region_id, scenario_name, start_year))

    # create DataFrame with attributes of ConversionSubProcess as columns
    columns = [f.name for f in fields(ConversionSubProcess)]
    return pd.DataFrame([asdict(cs) for cs in cs_list], columns=columns)


def _color_from_name(name: str) -> str:
    # simple deterministic color assignment based on name
    colors = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
        "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5",
        "#c49c94", "#f7b6d2", "#c7c7c7", "#dbdb8d", "#9edae5",
    ]
    return colors[hash(name) % len(colors)]

def _commodity_df(commodity_names: set[str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"commodity_name": name, "order": i, "color": _color_from_name(name)}
            for i, name in enumerate(sorted(commodity_names))
        ],
        columns=["commodity_name", "order", "color"],
    )

def _conversion_process_df(conversion_process_names: set[str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"conversion_process_name": name, "order": i, "color": _color_from_name(name)}
            for i, name in enumerate(sorted(conversion_process_names))
        ],
        columns=["conversion_process_name", "order", "color"],
    )

def create_techmap(resolved: ResolvedSystem) -> Techmap:
    df_cs = _conversion_sub_process_df(resolved)
    df_co = _commodity_df(set(df_cs["commodity_in"]))
    df_cp = _conversion_process_df(set(df_cs["conversion_process_name"]))

    return Techmap(
        Units=_df_units(unit=resolved.units),
        Scenario=_df_scenario(resolved),
        Commodity=df_co,
        ConversionProcess=df_cp,
        ConversionSubProcess=df_cs,
        TSS=_df_tss(resolved.scenario.tss, resolved.scenario.dt_hours),
    )
