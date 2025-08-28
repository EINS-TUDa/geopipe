# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence, Tuple, List, Union

import numpy as np
import pandas as pd

try:
    import geopandas as gpd  
except Exception:  
    gpd = None

PathLike = Union[str, Path]

def write_cesm_inputs_from_data(
    om,
    *,
    workdir: PathLike,
    model_name: str,
    scenario_name: str,
    tss_name: str,
    polygons_path: Optional[PathLike] = None,
    data_dir: Optional[PathLike] = None,
    start_year: int = 2020,
    end_year: int = 2030,
    year_gap: int = 5,
    discount_rate: float = 0.05,
    dt_hours: int = 1,
    heat_file: str = "D_Heat_Household_J.txt",                     
    heat_unit: str = "J",                                          
    elec_profile_file: str = "corrected_eletricity_demand_2016.txt", 
    heat_commodity_base: str = "Heat",
    electric_boiler_eta: float = 0.95,
    elec_price_eur_per_mwh: float = 80.0,      
    export_price_eur_per_mwh: float = 0.0,     
    eb_cap_max_mw: Optional[float] = None,
    eb_max_eout_mwh: Optional[float] = None,
    pipe_loss_fraction: float = 0.05,          
    pipe_cap_max_mw: float = 1e6,
    pipe_opex_eur_per_mwh: float = 0.0,
) -> None:
    """
    One writer for both single and multi-district systems.

    - If polygons_path is None or contains 1 polygon:
        Commodities: ["Electricity", "Dummy", "<heat_commodity_base>"]
        Processes : GridImportElec, ElectricBoiler, HeatDemand, GridExportElec
        Demand   : Dummy -> <heat_commodity_base> with min_eout and time profile

    - If polygons_path contains N>1 polygons:
        Commodities: ["Electricity", "Dummy"] + ["<base>_D0..D{N-1}"]
        Processes : GridImportElec, GridExportElec,
                    ElectricBoiler_Di (Electricity->Heat_Di),
                    HeatDemand_Di    (Heat_Di->Dummy) for each district i,
                    Pipe_Di_Dj       (Heat_Di->Heat_Dj) for each adjacent (i,j)
        District annual heat split by polygon area; pipes only between adjacent.

    Sheet layout matches CESM parser expectations:
      Units, Scenario, TSS,
      Commodity(columns: commodity_name, order, color),
      ConversionProcess(columns: conversion_process_name, order, color),
      ConversionSubProcess (header row 0; rows 1–2 blank; data from row 3)
    """
    workdir     = Path(workdir)
    techmap_dir = workdir / "Data" / "Techmap"
    ts_dir      = workdir / "Data" / "TimeSeries"
    techmap_dir.mkdir(parents=True, exist_ok=True)
    ts_dir.mkdir(parents=True, exist_ok=True)

    om_scenario = getattr(om, "scenario", None)
    if om_scenario is not None:
        start_year = getattr(om_scenario, "start_year", start_year)
        end_year   = getattr(om_scenario, "end_year", end_year)
        year_gap   = getattr(om_scenario, "year_gap", year_gap)

    data_dir = Path(data_dir) if data_dir is not None else Path("data")

    heat_series = _load_numeric_txt(data_dir / heat_file)
    if heat_unit.upper() == "J":
        annual_heat_mwh_total = float(heat_series.sum() / 3.6e9)
    elif heat_unit.upper() == "MWH":
        annual_heat_mwh_total = float(heat_series.sum())
    else:
        raise ValueError("heat_unit must be 'J' or 'MWh'.")

    profile_full = _load_numeric_txt(data_dir / elec_profile_file)
    prof_sum = float(profile_full.sum())
    if prof_sum <= 0:
        raise ValueError(f"Electricity profile {elec_profile_file} sums to zero; cannot normalize.")
    profile_full = profile_full / prof_sum

    # ensure we can sample profile with provided TSS
    tss_file = ts_dir / f"{tss_name}.txt"
    if not tss_file.exists():
        tss_file.write_text("\n".join(str(i) for i in range(1, 225)), encoding="utf-8")
    tss_vals = [int(x) for x in tss_file.read_text(encoding="utf-8").strip().splitlines() if x]
    max_idx = max(tss_vals) if tss_vals else 0
    if max_idx >= len(profile_full):
        raise ValueError(
            f"TSS requires index {max_idx} but profile length is {len(profile_full)}. "
            f"Provide a long profile (e.g., 8760 values)."
        )

    demand_profile_name = "HeatDemandProfile"
    (ts_dir / f"{demand_profile_name}.txt").write_text(
        " ".join(f"{x:.8f}" for x in profile_full.tolist()), encoding="utf-8"
    )

    districts: List[int]
    heat_names: List[str]
    directed_edges: List[Tuple[int, int]] = []

    if polygons_path is None:
        districts = [0]
        heat_names = [f"{heat_commodity_base}"]
        area_weights = np.asarray([1.0], dtype=float)
    else:
        if gpd is None:
            raise RuntimeError("geopandas not available: polygons/adjacency require GeoPandas.")
        poly_path = Path(polygons_path)
        if not poly_path.exists():
            raise FileNotFoundError(f"polygons (districts) not found: {poly_path}")

        gdf = gpd.read_file(poly_path)
        try:
            # ensure projected CRS for areas
            if gdf.crs is None or getattr(gdf.crs, "is_geographic", False):
                gdf = gdf.to_crs(3035)  
        except Exception:
            pass

        n = int(len(gdf))
        if n < 1:
            raise ValueError("No polygons found in polygons file.")

        areas = gdf.geometry.area.values.astype(float)
        if not np.isfinite(areas).all() or areas.sum() <= 0:
            area_weights = np.ones(n, dtype=float) / n
        else:
            area_weights = areas / areas.sum()

        districts = list(range(n))
        if n == 1:
            heat_names = [f"{heat_commodity_base}"]
        else:
            heat_names = [f"{heat_commodity_base}_D{i}" for i in range(n)]
            undirected_edges = _adjacency_edges(gdf)
            for i, j in undirected_edges:
                directed_edges.append((i, j))
                directed_edges.append((j, i))

    annual_heat_by_d = (area_weights * annual_heat_mwh_total).tolist()

    # EB defaults
    if eb_cap_max_mw is None:
        eb_cap_max_mw = max(1.0, annual_heat_mwh_total / (365 * 24)) * 100.0
    if eb_max_eout_mwh is None:
        eb_max_eout_mwh = annual_heat_mwh_total * 100.0

    xlsx = techmap_dir / f"{model_name}.xlsx"
    units_df = _units_df()
    scenario_df = _scenario_df(
        scenario_name=scenario_name,
        start_year=start_year,
        end_year=end_year,
        year_gap=year_gap,
        tss_name=tss_name,
        discount_rate=discount_rate,
    )
    tss_df = _tss_df(tss_name=tss_name, dt_hours=dt_hours)

    # NOTE: columns must be 'order' and 'color')
    base = ["Electricity", "External", "Dummy"]
    commodity_list = base + heat_names
    commodity_df = pd.DataFrame(
        [{"commodity_name": c, "order": i + 1, "color": ""} for i, c in enumerate(commodity_list)],
        columns=["commodity_name", "order", "color"],
    )

    conv_procs: List[str] = ["GridImportElec", "GridExportElec"]
    if len(districts) == 1:
        conv_procs += ["ElectricBoiler", "HeatDemand"]
    else:
        conv_procs += [f"ElectricBoiler_D{i}" for i in districts]
        conv_procs += [f"HeatDemand_D{i}"   for i in districts]
        conv_procs += [f"Pipe_D{i}_D{j}" for (i, j) in directed_edges]

    convproc_df = pd.DataFrame(
        [{"conversion_process_name": t, "order": i + 1, "color": ""} for i, t in enumerate(conv_procs)],
        columns=["conversion_process_name", "order", "color"],
    )

    cs_rows: List[dict] = []

    # grid import/export
    cs_rows.append({
        "conversion_process_name": "GridImportElec",
        "commodity_in": "Dummy",
        "commodity_out": "Electricity",
        "scenario": scenario_name,
        "efficiency": 1.0,
        "technical_availability": 1.0,
        "max_eout": 1e12,
        "cap_max": 1e9,
        "opex_cost_energy": float(elec_price_eur_per_mwh),
    })
    cs_rows.append({
        "conversion_process_name": "GridExportElec",
        "commodity_in": "Electricity",
        "commodity_out": "Dummy",
        "scenario": scenario_name,
        "efficiency": 1.0,
        "technical_availability": 1.0,
        "max_eout": 1e12,
        "cap_max": 1e9,
        "opex_cost_energy": float(export_price_eur_per_mwh),
    })

    # single-district EnergyBalance + Demand
    if len(districts) == 1:
        heat_comm = heat_names[0]
        cs_rows.append({
            "conversion_process_name": "ElectricBoiler",
            "commodity_in": "Electricity",
            "commodity_out": heat_comm,
            "scenario": scenario_name,
            "efficiency": float(electric_boiler_eta),
            "technical_availability": 1.0,
            "cap_min": 0.0,
            "cap_max": float(eb_cap_max_mw),
            "max_eout": float(eb_max_eout_mwh),
        })
        # Demand
        cs_rows.append({
            "conversion_process_name": "HeatDemand",
            "commodity_in":  heat_comm,   # consume Heat
            "commodity_out": "Dummy",     # dump to Dummy
            "scenario": scenario_name,
            "efficiency": 1.0,
            "technical_availability": 1.0,
            "min_eout": float(annual_heat_by_d[0]),
            "output_profile": demand_profile_name,
        })
    else:
        for i in districts:
            heat_comm = heat_names[i]
            cs_rows.append({
                "conversion_process_name": f"ElectricBoiler_D{i}",
                "commodity_in": "Electricity",
                "commodity_out": heat_comm,
                "scenario": scenario_name,
                "efficiency": float(electric_boiler_eta),
                "technical_availability": 1.0,
                "cap_min": 0.0,
                "cap_max": float(eb_cap_max_mw),
                "max_eout": float(eb_max_eout_mwh),
            })
            cs_rows.append({
                "conversion_process_name": f"HeatDemand_D{i}",
                "commodity_in":  heat_comm,   # consume Heat
                "commodity_out": "Dummy",     # dump to Dummy
                "scenario": scenario_name,
                "efficiency": 1.0,
                "technical_availability": 1.0,
                "min_eout": float(annual_heat_by_d[i]),
                "output_profile": demand_profile_name,
            })

        # pipes for adjacent districts (directed)
        pipe_eff = max(0.0, 1.0 - float(pipe_loss_fraction))
        for (i, j) in directed_edges:
            cs_rows.append({
                "conversion_process_name": f"Pipe_D{i}_D{j}",
                "commodity_in": heat_names[i],
                "commodity_out": heat_names[j],
                "scenario": scenario_name,
                "efficiency": pipe_eff,
                "technical_availability": 1.0,
                "cap_max": float(pipe_cap_max_mw),
                "max_eout": 1e12,
                "opex_cost_energy": float(pipe_opex_eur_per_mwh),
            })

    # shape to expected columns
    base_cols  = ["conversion_process_name", "commodity_in", "commodity_out", "scenario"]
    param_cols = _param_cols()
    convsubproc_df = pd.DataFrame(cs_rows)
    for col in base_cols + param_cols:
        if col not in convsubproc_df.columns:
            convsubproc_df[col] = np.nan
    convsubproc_df = convsubproc_df[base_cols + param_cols]

    with pd.ExcelWriter(xlsx, engine="openpyxl", mode="w") as xw:
        _units_df().to_excel(xw, sheet_name="Units", index=False)
        scenario_df.to_excel(xw, sheet_name="Scenario", index=False)
        tss_df.to_excel(xw, sheet_name="TSS", index=False)
        commodity_df.to_excel(xw, sheet_name="Commodity", index=False)             
        convproc_df.to_excel(xw, sheet_name="ConversionProcess", index=False)      
        cs_sheet = "ConversionSubProcess"
        pd.DataFrame(columns=convsubproc_df.columns).to_excel(xw, sheet_name=cs_sheet, index=False)  # header row 0
        convsubproc_df.to_excel(xw, sheet_name=cs_sheet, index=False, header=False, startrow=3)      # data from row 3

    print(f"[writer] Wrote techmap: {xlsx}")
    print("[writer] Sheets: Units, Scenario, TSS, Commodity, ConversionProcess, ConversionSubProcess")
    print(f"[writer] Commodities={len(commodity_list)} | CPs={len(conv_procs)} | CS rows={len(convsubproc_df)}")


# Backward-compat wrapper for single-district case
def write_cesm_inputs_multidistrict_from_data(**kwargs) -> None:
    return write_cesm_inputs_from_data(**kwargs)


def _adjacency_edges(gdf) -> List[Tuple[int, int]]:
    """Undirected adjacency (i<j) when polygons touch or intersect."""
    edges: List[Tuple[int, int]] = []
    if len(gdf) <= 1:
        return edges
    try:
        sindex = gdf.sindex
    except Exception:
        sindex = None

    for i, geom in enumerate(gdf.geometry):
        if geom is None or geom.is_empty:
            continue
        candidates = range(i + 1, len(gdf)) if sindex is None else sindex.intersection(geom.bounds)
        for j in candidates:
            if j <= i:
                continue
            other = gdf.geometry.iloc[j]
            if other is None or other.is_empty:
                continue
            if geom.touches(other) or geom.intersects(other):
                edges.append((i, j))
    return edges

def _load_numeric_txt(path: Path) -> np.ndarray:
    txt = Path(path).read_text(encoding="utf-8").strip().replace("\n", " ")
    vals = [v for v in txt.split(" ") if v != ""]
    return np.asarray([float(x) for x in vals], dtype=float)

def _compress_profile(x: np.ndarray, n: int) -> np.ndarray:
    if x.ndim != 1 or len(x) == 0:
        return np.ones(n, dtype=float) / n
    chunks: Sequence[np.ndarray] = np.array_split(x, n)
    s = np.array([float(c.sum()) for c in chunks], dtype=float)
    return s

def _units_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"quantity": "energy",       "scale_factor": 1.0, "input": "MWh",      "output": "MWh"},
            {"quantity": "power",        "scale_factor": 1.0, "input": "MW",       "output": "MW"},
            {"quantity": "cost_energy",  "scale_factor": 1.0, "input": "EUR/MWh",  "output": "EUR/MWh"},
            {"quantity": "cost_power",   "scale_factor": 1.0, "input": "EUR/MW",   "output": "EUR/MW"},
            {"quantity": "co2_spec",     "scale_factor": 1.0, "input": "tCO2/MWh", "output": "tCO2/MWh"},
        ],
        columns=["quantity", "scale_factor", "input", "output"],
    )

def _scenario_df(
    *,
    scenario_name: str,
    start_year: int | None,
    end_year: int | None,
    year_gap: int | None,
    tss_name: str,
    discount_rate: float,
) -> pd.DataFrame:
    if start_year is None: start_year = 2020
    if end_year   is None: end_year   = start_year
    if year_gap   is None: year_gap   = 1
    return pd.DataFrame(
        [{
            "scenario_name":     scenario_name,
            "from_year":         int(start_year),
            "until_year":        int(end_year),
            "year_step":         int(year_gap),
            "discount_rate":     float(discount_rate),
            "TSS":               tss_name,
            "annual_co2_limit":  np.nan,
            "co2_price":         np.nan,
        }],
        columns=[
            "scenario_name", "from_year", "until_year", "year_step",
            "discount_rate", "TSS", "annual_co2_limit", "co2_price",
        ],
    )


def _tss_df(*, tss_name: str, dt_hours: int) -> pd.DataFrame:
    return pd.DataFrame([{"TSS_name": tss_name, "dt": int(dt_hours)}], columns=["TSS_name", "dt"])


def _param_cols() -> list[str]:
    # Parameters CESM recognizes (maps to param_cs / param_cs_y / param_cs_t)
    return [
        "spec_co2",
        "efficiency",
        "technical_lifetime",
        "technical_availability",
        "c_rate",
        "efficiency_charge",
        "is_storage",
        "opex_cost_energy",
        "opex_cost_power",
        "capex_cost_power",
        "max_eout",
        "min_eout",
        "cap_min",
        "cap_max",
        "cap_res_min",
        "cap_res_max",
        "out_frac_min",
        "out_frac_max",
        "in_frac_min",
        "in_frac_max",
        "availability_profile",
        "output_profile",
    ]
