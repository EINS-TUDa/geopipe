import hashlib
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional

import pandas as pd

from ...energy_system.imports_exports import Import, Export
from ...energy_system.region import Demand
from ...energy_system.technology import PipeTechnology, GridTechnology, CentralTechnology, CHPTechnology, \
    DecentralTechnology, Technology
from .conversion_sub_process import ConversionSubProcess
from .profiles import profile_hash
from .units import df_units
from ..resolved_system import ResolvedSystem

import logging

from ...energy_system.units import UnitKW, UnitMW

logger = logging.getLogger(__name__)

CONVERSION_FACTOR = 0.001 # MWh to GWh, EUR to k EUR, kW to MW

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

def _rebase_relative_years(value: float | dict[int, float | None] | None,
                           start_year: int) -> float | dict[int, float | None] | None:
    """Shift dict keys from years-relative-to-start to absolute years; pass through scalars and None."""
    if value is None or isinstance(value, (int, float)):
        return value
    return {start_year + int(rel_year): v for rel_year, v in value.items()}

def _scale(value: Optional[float]) -> float | None:
    if value is None:
        return None
    return value * CONVERSION_FACTOR

def year_dep_value_to_cesm_string(value: float | dict[int, float | None] | None, scale: bool = False) -> float | str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if scale:
            return _scale(value)
        return value
    if isinstance(value, dict):
        if all(v is None for v in value.values()): # if all values in dict are None, return None
            return None
        segments = []
        for year, year_value in sorted(value.items()):
            year_i = int(year)
            if year_value is None:
                segments.append(f"{year_i} NaN")
            else:
                if scale:
                    year_value = _scale(year_value)
                segments.append(f"{year_i} {year_value:.5g}")
        return "[" + ";".join(segments) + "]"
    raise TypeError(f"Unsupported type for year-dependent value: {type(value).__name__}")


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
            "co2_price": year_dep_value_to_cesm_string(s.co2_price, True),
        }]
    )

def _import_to_conversion_sub_process(imp: Import, scenario_name: str, start_year: int) -> ConversionSubProcess:
    return ConversionSubProcess(
        conversion_process_name=imp.name,
        commodity_in="Dummy",
        commodity_out=imp.commodity_out,
        scenario=scenario_name,
        opex_cost_energy=year_dep_value_to_cesm_string(_rebase_relative_years(imp.price_eur_per_mwh, start_year)),
        spec_co2=year_dep_value_to_cesm_string(_rebase_relative_years(imp.co2_emissions_ton_per_mwh, start_year)),
        cap_max=year_dep_value_to_cesm_string(_rebase_relative_years(imp.max_cap_per_year, start_year), True),
        max_eout=year_dep_value_to_cesm_string(_rebase_relative_years(imp.max_energy_out_per_year, start_year), True),
    )

def _export_to_conversion_sub_process(exp: Export, scenario_name: str, start_year: int) -> ConversionSubProcess:
    return ConversionSubProcess(
        conversion_process_name=exp.name,
        commodity_in=exp.commodity_in,
        commodity_out="Dummy",
        scenario=scenario_name,
        opex_cost_energy=year_dep_value_to_cesm_string(_rebase_relative_years(exp.price_eur_per_mwh, start_year)),
        spec_co2=year_dep_value_to_cesm_string(_rebase_relative_years(exp.co2_emissions_ton_per_mwh, start_year)),
        cap_max=year_dep_value_to_cesm_string(_rebase_relative_years(exp.max_cap_per_year, start_year), True),
        max_eout=year_dep_value_to_cesm_string(_rebase_relative_years(exp.max_energy_in_per_year, start_year), True),
    )

def _pipe_to_conversion_sub_process(pipe: PipeTechnology, scenario_name: str, start_year: int) -> ConversionSubProcess:
    return ConversionSubProcess(
        conversion_process_name=f"{pipe.name}_D{pipe.region_id_in}_D{pipe.region_id_out}",
        commodity_in=commodity_name(pipe.commodity_in, pipe.region_id_in),
        commodity_out=commodity_name(pipe.commodity_out, pipe.region_id_out),
        scenario=scenario_name,
        efficiency=pipe.efficiency,
        technical_lifetime=pipe.technical_lifetime,
        capex_cost_base=_scale(pipe.investment_costs_eur),
        cap_max=year_dep_value_to_cesm_string(pipe.max_capacity_per_year(start_year), True),
        cap_res_min=year_dep_value_to_cesm_string(pipe.capacity_per_year(start_year), True),
        cap_res_max=year_dep_value_to_cesm_string(pipe.capacity_per_year(start_year), True),
    )

def _demand_to_conversion_sub_process(
    demand: Demand,
    region_id: int,
    scenario_name: str,
    scenario_years: list[int],
    profile_names: dict[str, str]) -> ConversionSubProcess:
    return ConversionSubProcess(
        conversion_process_name=f"{demand.name}_D{region_id}",
        commodity_in=commodity_name(demand.demand_type.commodity_in, region_id),
        commodity_out="Dummy",
        scenario=scenario_name,
        min_eout=year_dep_value_to_cesm_string(demand.values_per_year(scenario_years), True),
        output_profile=profile_names[profile_hash(demand.profile)],
    )

def _decentralized_tech_to_conversion_sub_process(tech: DecentralTechnology, region_id: int, scenario_name: str,
                                                  start_year: int,
                                                  profile_names: dict[str, str]) -> ConversionSubProcess:
    output_profile = None
    if tech.output_profile is not None:
        output_profile = profile_names.get(profile_hash(tech.output_profile))
        if output_profile is None:
            raise ValueError(f"The output profile of {tech.name} in region {region_id} matches no demand profile.")
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
        cap_res_min=year_dep_value_to_cesm_string(tech.capacity_per_year(start_year), True),
        cap_res_max=year_dep_value_to_cesm_string(tech.capacity_per_year(start_year), True),
        cap_max=year_dep_value_to_cesm_string(tech.max_capacity_per_year(start_year), True),
        output_profile=output_profile,
    )

def _grid_to_conversion_sub_process(grid: GridTechnology, region_id, scenario_name: str, start_year: int) -> ConversionSubProcess:
    return ConversionSubProcess(
        conversion_process_name=f"{grid.name}_D{region_id}",
        commodity_in=commodity_name(grid.commodity_in, region_id),
        commodity_out=commodity_name(grid.commodity_out, region_id),
        scenario=scenario_name,
        efficiency=grid.efficiency,
        technical_lifetime=grid.technical_lifetime,
        capex_cost_base=_scale(grid.investment_costs_eur),
        cap_max=year_dep_value_to_cesm_string(grid.max_capacity_per_year(start_year), True),
        cap_res_min=year_dep_value_to_cesm_string(grid.capacity_per_year(start_year), True),
        cap_res_max=year_dep_value_to_cesm_string(grid.capacity_per_year(start_year), True),
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
        capex_cost_base=year_dep_value_to_cesm_string(tech.capex_cost_base, True),
        cap_max_unit=_scale(tech.max_capacity_per_unit),
        cap_max=year_dep_value_to_cesm_string(tech.max_capacity_per_year(start_year), True),
        cap_res_min=year_dep_value_to_cesm_string(tech.existing_capacity_per_year(start_year), True),
        cap_res_max=year_dep_value_to_cesm_string(tech.existing_capacity_per_year(start_year), True),
        output_profile=tech.output_profile_name,
        availability_profile=tech.availability_profile_name
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
        capex_cost_base=year_dep_value_to_cesm_string(chp.capex_cost_base, True),
        cap_max_unit=_scale(chp.max_capacity_per_unit),
        cap_max=year_dep_value_to_cesm_string(chp.max_capacity_per_year(start_year), True),
        cap_res_max=year_dep_value_to_cesm_string(chp.existing_capacity_per_year(start_year), True),
        cap_res_min=year_dep_value_to_cesm_string(chp.existing_capacity_per_year(start_year), True),
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

def _conversion_sub_process_df(resolved: ResolvedSystem, profile_names: dict[str, str]) -> pd.DataFrame:
    cs_list: list[ConversionSubProcess] = []
    scenario_name = resolved.scenario.name
    scenario_years = resolved.scenario.years
    start_year = resolved.scenario.start_year

    # imports
    for imp in resolved.imports:
        cs_list.append(_import_to_conversion_sub_process(imp, scenario_name, start_year))

    # exports
    for exp in resolved.exports:
        cs_list.append(_export_to_conversion_sub_process(exp, scenario_name, start_year))

    # pipes
    for pipe in resolved.pipe_connections:
        cs_list.append(_pipe_to_conversion_sub_process(pipe, scenario_name, start_year))

    # demands
    for region_id, demands in resolved.demands.items():
        for demand in demands:
            cs_list.append(_demand_to_conversion_sub_process(demand, region_id, scenario_name, scenario_years,
                                                             profile_names))

    # decentralized techs
    for region_id, techs in resolved.decentralized_technologies.items():
        for tech in techs:
            cs_list.append(_decentralized_tech_to_conversion_sub_process(tech, region_id, scenario_name, start_year,
                                                                         profile_names))

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
    colors = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
        "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5",
        "#c49c94", "#f7b6d2", "#c7c7c7", "#dbdb8d", "#9edae5",
    ]
    # remove suffix like _D, _D1, _Dxyz at the end of the string
    base = re.sub(r"_D.*$", "", name)
    h = hashlib.md5(base.encode()).hexdigest()
    index = int(h, 16) % len(colors)
    return colors[index]

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

def create_techmap(resolved: ResolvedSystem, profile_names: dict[str, str]) -> Techmap:
    """``profile_names`` maps a profile's content hash to its timeseries name (see :func:`profile_names`)."""
    # CONVERSION_FACTOR (0.001) converts the source values into the UnitMW Units sheet
    # below; it is only valid if the EnergySystem is expressed in UnitKW (kW/MWh/t/EUR).
    if not isinstance(resolved.units, UnitKW):
        raise ValueError(
            f"techmap scaling assumes UnitKW source data, got {type(resolved.units).__name__}"
        )
    df_cs = _conversion_sub_process_df(resolved, profile_names)
    df_co = _commodity_df(set(df_cs["commodity_in"]))
    df_cp = _conversion_process_df(set(df_cs["conversion_process_name"]))

    return Techmap(
        Units=df_units(unit=UnitMW()),
        Scenario=_df_scenario(resolved),
        Commodity=df_co,
        ConversionProcess=df_cp,
        ConversionSubProcess=df_cs,
        TSS=_df_tss(resolved.scenario.tss, resolved.scenario.dt_hours),
    )
