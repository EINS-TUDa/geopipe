# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple, List, Union

import numpy as np
import pandas as pd

import geopandas as gpd

from pypeline.energy_system.technology import Technology
from pypeline.energy_system.technology_registry import TechnologyRegistry

PathLike = Union[str, Path]


def _canon_co(name: str) -> str:
    """
    Canonicalize common commodity names to match this writer's Techmap conventions.
    (Only map the ones already used by the writer; otherwise pass-through.)
    """
    if name is None:
        return ""
    m = name.strip().lower()
    if m == "electricity":
        return "Electricity"
    if m == "dummy":
        return "Dummy"
    if m == "external":
        return "External"
    return name  # leave custom names (e.g., gas, oil, district_heat_in/out) as-is


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
    heat_unit: str = "MWH",
    elec_profile_file: str = "corrected_eletricity_demand_2016.txt",
    heat_commodity_base: str = "Heat",

    eb_lifetime_years: int = 30,

    eb_small_eta: float = 0.95,
    eb_small_capex_eur_per_mw: float = 1_200_000.0,
    eb_small_opex_eur_per_mw: float = 15_000.0,
    eb_small_opex_eur_per_mwh: float = 0.5,
    eb_small_cap_max_mw: Optional[float] = None,
    eb_small_max_eout_mwh: Optional[float] = None,

    eb_large_eta: float = 0.96,
    eb_large_capex_eur_per_mw: float = 600_000.0,
    eb_large_opex_eur_per_mw: float = 10_000.0,
    eb_large_opex_eur_per_mwh: float = 0.2,
    eb_large_out_frac_min: float = 0.25,
    eb_large_cap_min_mw: float = 0.0,
    eb_large_cap_max_mw: Optional[float] = None,
    eb_large_max_eout_mwh: Optional[float] = None,

    elec_price_eur_per_mwh: float = 100.0,
    export_price_eur_per_mwh: float = -10.0,

    pipe_loss_fraction: float = 0.05,
    pipe_cap_max_mw: float = 1e9,
    pipe_opex_eur_per_mwh: float = 1.0,
    pipe_capex_eur_per_mw: float = 1_000.0,
    pipe_lifetime_years: int = 30,

    edge_strategy: str = "mst",
    min_shared_border_m: float = 0.0,
    max_pipes_per_district: Optional[int] = None,
    neighbor_distance_m: Optional[float] = None,

    selected_techs: list[Technology] | TechnologyRegistry | None = None,
    include_default_components: bool = True,

) -> None:

    workdir = Path(workdir)
    techmap_dir = workdir / "Data" / "Techmap"
    ts_dir = workdir / "Data" / "TimeSeries"
    techmap_dir.mkdir(parents=True, exist_ok=True)
    ts_dir.mkdir(parents=True, exist_ok=True)

    om_scenario = getattr(om, "scenario", None)
    if om_scenario is not None:
        start_year = getattr(om_scenario, "start_year", start_year)
        end_year = getattr(om_scenario, "end_year", end_year)
        year_gap = getattr(om_scenario, "year_gap", year_gap)

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

    tss_file = ts_dir / f"{tss_name}.txt"
    if not tss_file.exists():
        tss_file.write_text("\n".join(str(i) for i in range(1, 225)), encoding="utf-8")
    tss_vals = [int(x) for x in tss_file.read_text(encoding="utf-8").strip().splitlines() if x]
    max_idx = max(tss_vals) if tss_vals else 0
    if max_idx >= len(profile_full):
        raise ValueError(f"TSS requires index {max_idx} but profile length is {len(profile_full)}.")
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
            raise RuntimeError("geopandas required for polygons_path.")
        poly_path = Path(polygons_path)
        if not poly_path.exists():
            raise FileNotFoundError(f"polygons not found: {poly_path}")

        gdf = gpd.read_file(poly_path)
        try:
            if gdf.crs is None or getattr(gdf.crs, "is_geographic", False):
                gdf = gdf.to_crs(3035)
        except Exception:
            pass

        n = int(len(gdf))
        if n < 1:
            raise ValueError("No polygons found.")

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
            undirected_edges = _edges_pruned(
                gdf,
                edge_strategy=edge_strategy,
                min_shared_border_m=min_shared_border_m,
                max_pipes_per_district=max_pipes_per_district,
                neighbor_distance_m=neighbor_distance_m,
            )
            for i, j in undirected_edges:
                directed_edges.append((i, j))
                directed_edges.append((j, i))

    annual_heat_by_d = (area_weights * annual_heat_mwh_total).tolist()

    if eb_small_cap_max_mw is None:
        eb_small_cap_max_mw = max(1.0, annual_heat_mwh_total / (365 * 24)) * 100.0
    if eb_small_max_eout_mwh is None:
        eb_small_max_eout_mwh = annual_heat_mwh_total * 100.0
    if eb_large_cap_max_mw is None:
        eb_large_cap_max_mw = max(1.0, annual_heat_mwh_total / (365 * 24)) * 100.0
    if eb_large_max_eout_mwh is None:
        eb_large_max_eout_mwh = annual_heat_mwh_total * 100.0

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

    base = ["Electricity", "External", "Dummy"]
    commodity_list = base + heat_names

    sel_list: list[Technology] = []
    if isinstance(selected_techs, TechnologyRegistry):
        sel_list = selected_techs.get_all(return_type="instance")
    elif isinstance(selected_techs, list):
        sel_list = selected_techs or []

    # Add commodities from selected technologies
    for tech in sel_list:
        cin = _canon_co(tech.commodity_in)
        if tech.commodity_out == "residential_heat":
            # mapped later to heat_names
            pass
        else:
            cout = _canon_co(tech.commodity_out)
            if cout not in commodity_list:
                commodity_list.append(cout)
        if cin not in commodity_list:
            commodity_list.append(cin)

    commodity_df = pd.DataFrame(
        [{"commodity_name": c, "order": i + 1, "color": ""} for i, c in enumerate(commodity_list)],
        columns=["commodity_name", "order", "color"],
    )

    conv_procs: List[str] = ["GridImportElec", "GridExportElec"]
    if len(districts) == 1:
        conv_procs += ["ElectricBoiler", "HeatDemand"]
    else:
        conv_procs += [f"EB_small_D{i}" for i in districts]
        conv_procs += [f"EB_large_D{i}" for i in districts]
        conv_procs += [f"HeatDemand_D{i}" for i in districts]
        conv_procs += [f"Pipe_D{i}_D{j}" for (i, j) in directed_edges]

    for tech in sel_list:
        if len(districts) == 1 or tech.commodity_out != "residential_heat":
            name = f"{tech.name}"
            if name not in conv_procs:
                conv_procs.append(name)
        else:
            # heat-producing tech: replicate per district
            for i in districts:
                name = f"{tech.name}_D{i}"
                if name not in conv_procs:
                    conv_procs.append(name)

    convproc_df = pd.DataFrame(
        [{"conversion_process_name": t, "order": i + 1, "color": ""} for i, t in enumerate(conv_procs)],
        columns=["conversion_process_name", "order", "color"],
    )


    cs_rows: List[dict] = []

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

    if include_default_components:
        for i in districts:
            heat_comm = heat_names[i]
            cs_rows.append({
                "conversion_process_name": f"EB_small_D{i}",
                "commodity_in": "Electricity",
                "commodity_out": heat_comm,
                "scenario": scenario_name,
                "efficiency": float(eb_small_eta),
                "technical_availability": 1.0,
                "technical_lifetime": int(eb_lifetime_years),
                "cap_min": 0.0,
                "cap_max": float(eb_small_cap_max_mw),
                "max_eout": float(eb_small_max_eout_mwh),
                "capex_cost_power": float(eb_small_capex_eur_per_mw),
                "opex_cost_power": float(eb_small_opex_eur_per_mw),
                "opex_cost_energy": float(eb_small_opex_eur_per_mwh),
            })

            cs_rows.append({
                "conversion_process_name": f"EB_large_D{i}",
                "commodity_in": "Electricity",
                "commodity_out": heat_comm,
                "scenario": scenario_name,
                "efficiency": float(eb_large_eta),
                "technical_availability": 1.0,
                "technical_lifetime": int(eb_lifetime_years),
                "cap_min": float(eb_large_cap_min_mw),
                "cap_max": float(eb_large_cap_max_mw),
                "max_eout": float(eb_large_max_eout_mwh),
                "capex_cost_power": float(eb_large_capex_eur_per_mw),
                "opex_cost_power": float(eb_large_opex_eur_per_mw),
                "opex_cost_energy": float(eb_large_opex_eur_per_mwh),
                "out_frac_min": float(eb_large_out_frac_min),
            })

            cs_rows.append({
                "conversion_process_name": f"HeatDemand_D{i}",
                "commodity_in": heat_comm,
                "commodity_out": "Dummy",
                "scenario": scenario_name,
                "efficiency": 1.0,
                "technical_availability": 1.0,
                "min_eout": float(annual_heat_by_d[i]),
                "output_profile": demand_profile_name,
            })

        pipe_eff = max(0.0, 1.0 - float(pipe_loss_fraction))
        for (i, j) in directed_edges:
            cs_rows.append({
                "conversion_process_name": f"Pipe_D{i}_D{j}",
                "commodity_in": heat_names[i],
                "commodity_out": heat_names[j],
                "scenario": scenario_name,
                "efficiency": pipe_eff,
                "technical_availability": 1.0,
                "technical_lifetime": int(pipe_lifetime_years),
                "cap_max": float(pipe_cap_max_mw),
                "max_eout": 1e12,
                "opex_cost_energy": float(pipe_opex_eur_per_mwh),
                "capex_cost_power": float(pipe_capex_eur_per_mw),
            })

    def _add_cs_row(cp_name: str, cin: str, cout: str, tech: Technology):
        cs_rows.append({
            "conversion_process_name": cp_name,
            "commodity_in": _canon_co(cin),
            "commodity_out": _canon_co(cout),
            "scenario": scenario_name,
            # uniform schema; leave N/A as NaN
            "efficiency": float(tech.efficiency) if tech.efficiency is not None else np.nan,
            "technical_lifetime": int(tech.technical_lifetime) if tech.technical_lifetime is not None else np.nan,
            "technical_availability": 1.0,
            "opex_cost_energy": float(tech.opex_cost_energy) if tech.opex_cost_energy is not None else np.nan,
            "opex_cost_power": float(tech.opex_cost_power) if tech.opex_cost_power is not None else np.nan,
            "capex_cost_power": float(tech.capex_cost_power) if tech.capex_cost_power is not None else np.nan,
            # constraints/capacity left empty by default
        })

    for tech in sel_list:
        if len(districts) == 1:
            cout = heat_names[0] if tech.commodity_out == "residential_heat" else tech.commodity_out
            _add_cs_row(f"{tech.name}", tech.commodity_in, cout, tech)
        else:
            if tech.commodity_out == "residential_heat":
                for i in districts:
                    _add_cs_row(f"{tech.name}_D{i}", tech.commodity_in, heat_names[i], tech)
            else:
                # non-heat techs: single CP (global)
                _add_cs_row(f"{tech.name}", tech.commodity_in, tech.commodity_out, tech)

    base_cols = ["conversion_process_name", "commodity_in", "commodity_out", "scenario"]
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
        pd.DataFrame(columns=convsubproc_df.columns).to_excel(xw, sheet_name=cs_sheet, index=False)
        convsubproc_df.to_excel(xw, sheet_name=cs_sheet, index=False, header=False, startrow=3)

    print(f"[writer] Wrote techmap: {xlsx}")
    print("[writer] Sheets: Units, Scenario, TSS, Commodity, ConversionProcess, ConversionSubProcess")
    print(f"[writer] Commodities={len(commodity_list)} | CPs={len(conv_procs)} | CS rows={len(convsubproc_df)}")


def write_cesm_inputs_multidistrict_from_data(**kwargs) -> None:
    return write_cesm_inputs_from_data(**kwargs)

def _edges_pruned(
    gdf,
    *,
    edge_strategy="mst",
    min_shared_border_m: float = 0.0,
    max_pipes_per_district: Optional[int] = None,
    neighbor_distance_m: Optional[float] = None,
) -> List[Tuple[int, int]]:
    cand = _candidate_edges(
        gdf,
        min_shared_border_m=min_shared_border_m,
        neighbor_distance_m=neighbor_distance_m,
    )
    if not cand:
        return []

    n = len(gdf)
    if edge_strategy == "all":
        return [(i, j) for (i, j, _) in cand]

    if edge_strategy == "k_nearest":
        if not max_pipes_per_district or max_pipes_per_district <= 0:
            return [(i, j) for (i, j, _) in cand]
        by_i: List[List[Tuple[float, int]]] = [[] for _ in range(n)]
        for i, j, d in cand:
            by_i[i].append((d, j))
            by_i[j].append((d, i))
        keep = set()
        for i in range(n):
            by_i[i].sort(key=lambda x: x[0])
            for _, j in by_i[i][:max_pipes_per_district]:
                keep.add(tuple(sorted((i, j))))
        return sorted(list(keep))

    parent = list(range(n))
    rank = [0] * n

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return False
        if rank[ra] < rank[rb]:
            parent[ra] = rb
        elif rank[ra] > rank[rb]:
            parent[rb] = ra
        else:
            parent[rb] = ra
            rank[ra] += 1
        return True

    cand_sorted = sorted(cand, key=lambda t: t[2])
    mst = []
    for i, j, _ in cand_sorted:
        if union(i, j):
            mst.append((i, j))
    return mst


def _candidate_edges(
    gdf,
    *,
    min_shared_border_m=0.0,
    neighbor_distance_m=None,
):
    edges = []
    geoms = list(gdf.geometry)
    cents = [g.centroid for g in geoms]
    n = len(geoms)
    for i, gi in enumerate(geoms):
        if gi is None or gi.is_empty:
            continue
        for j in range(i + 1, n):
            gj = geoms[j]
            if gj is None or gj.is_empty:
                continue
            if not (gi.touches(gj) or gi.intersects(gj)):
                continue
            shared = gi.boundary.intersection(gj.boundary)
            length = float(getattr(shared, "length", 0.0) or 0.0)
            if length < float(min_shared_border_m):
                continue
            dist = float(cents[i].distance(cents[j]))
            if neighbor_distance_m is not None and dist > float(neighbor_distance_m):
                continue
            edges.append((i, j, dist))
    return edges


def _load_numeric_txt(path: Path) -> np.ndarray:
    txt = Path(path).read_text(encoding="utf-8").strip().replace("\n", " ")
    vals = [v for v in txt.split(" ") if v != ""]
    return np.asarray([float(x) for x in vals], dtype=float)


def _units_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"quantity": "energy", "scale_factor": 1.0, "input": "MWh", "output": "MWh"},
            {"quantity": "power", "scale_factor": 1.0, "input": "MW", "output": "MW"},
            {"quantity": "cost_energy", "scale_factor": 1.0, "input": "EUR/MWh", "output": "EUR/MWh"},
            {"quantity": "cost_power", "scale_factor": 1.0, "input": "EUR/MW", "output": "EUR/MW"},
            {"quantity": "co2_spec", "scale_factor": 1.0, "input": "tCO2/MWh", "output": "tCO2/MWh"},
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
    if start_year is None:
        start_year = 2020
    if end_year is None:
        end_year = start_year
    if year_gap is None:
        year_gap = 1
    return pd.DataFrame(
        [{
            "scenario_name": scenario_name,
            "from_year": int(start_year),
            "until_year": int(end_year),
            "year_step": int(year_gap),
            "discount_rate": float(discount_rate),
            "TSS": tss_name,
            "annual_co2_limit": np.nan,
            "co2_price": np.nan,
        }],
        columns=[
            "scenario_name", "from_year", "until_year", "year_step",
            "discount_rate", "TSS", "annual_co2_limit", "co2_price",
        ],
    )




def _tss_df(*, tss_name: str, dt_hours: int) -> pd.DataFrame:
    return pd.DataFrame([{"TSS_name": tss_name, "dt": int(dt_hours)}], columns=["TSS_name", "dt"])


def _param_cols() -> list[str]:
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
