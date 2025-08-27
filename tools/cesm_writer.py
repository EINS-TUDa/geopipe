# tools/cesm_writer.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pandas as pd

def write_cesm_inputs_from_es(
    es,
    scenario,
    techmap_dir: Path,
    ts_dir: Path,
    model_name: str,
    scenario_name: str,
    tss_name: str,
    *,
    discount_rate: float = 0.05,
    dt_hours: int = 1,
) -> None:
    """
    Produce a CESM Techmap workbook + TSS file from the EnergySystem/Scenario.

    Sheets (names exactly like DEModel.xlsx):
      - Units
      - Scenario
      - TSS
      - Commodity
      - ConversionProcess
      - ConversionSubProcess  (header at row 0, meta rows at 1–2, data from row 3)
    """
    techmap_dir.mkdir(parents=True, exist_ok=True)
    ts_dir.mkdir(parents=True, exist_ok=True)

    xlsx = techmap_dir / f"{model_name}.xlsx"

    # ---- Build data frames -------------------------------------------------
    units_df = _units_df()

    scenario_df = _scenario_df(
        scenario_name=scenario_name,
        start_year=getattr(scenario, "start_year", None),
        end_year=getattr(scenario, "end_year", None),
        year_gap=getattr(scenario, "year_gap", None),
        tss_name=tss_name,
        discount_rate=discount_rate,
    )

    tss_df = _tss_df(tss_name=tss_name, dt_hours=dt_hours)

    commodities = _infer_commodities(es)
    techs       = _infer_techs(es)

    commodity_df = pd.DataFrame(
        [{"commodity_name": c, "order": i + 1, "color": "", "description": ""} for i, c in enumerate(commodities)],
        columns=["commodity_name", "order", "color", "description"],
    )

    convproc_df = pd.DataFrame(
        [{"conversion_process_name": t, "order": i + 1, "color": "", "description": ""} for i, t in enumerate(techs)],
        columns=["conversion_process_name", "order", "color", "description"],
    )

    # fallback
    convsubproc_df = _convsubproc_df(techs=techs, commodities=commodities, scenario_name=scenario_name)

    with pd.ExcelWriter(xlsx, engine="openpyxl", mode="w") as xw:
        units_df.to_excel(xw, sheet_name="Units", index=False)
        scenario_df.to_excel(xw, sheet_name="Scenario", index=False)
        tss_df.to_excel(xw, sheet_name="TSS", index=False)
        commodity_df.to_excel(xw, sheet_name="Commodity", index=False)
        convproc_df.to_excel(xw, sheet_name="ConversionProcess", index=False)

        # ConversionSubProcess:
        #   row 0: header
        #   row 1: description meta row
        #   row 2: units meta row
        #   row 3+: data
        cs_sheet = "ConversionSubProcess"
        header = list(convsubproc_df.columns)
        meta_desc, meta_units = _cs_meta_rows(header)

        # header only
        pd.DataFrame(columns=header).to_excel(xw, sheet_name=cs_sheet, index=False)
        # meta rows
        pd.DataFrame([meta_desc, meta_units], columns=header).to_excel(
            xw, sheet_name=cs_sheet, index=False, header=False, startrow=1
        )
        # data from row 3
        convsubproc_df.to_excel(xw, sheet_name=cs_sheet, index=False, header=False, startrow=3)

    ts_path = ts_dir / f"{tss_name}.txt"
    if not ts_path.exists():
        ts_path.write_text("\n".join(str(i) for i in range(1, 225)), encoding="utf-8")

    print(f"[writer] Wrote techmap: {xlsx}")
    print("[writer] Sheets: Units, Scenario, TSS, Commodity, ConversionProcess, ConversionSubProcess")
    print(f"[writer] Techs: {len(techs)} | Commodities: {len(commodities)} | CS rows: {len(convsubproc_df)}")


def write_cesm_inputs_from_data(
    om,
    *,
    workdir: Path,
    model_name: str,
    scenario_name: str,
    tss_name: str,
    data_dir: Optional[Path] = None,
    start_year: int = 2020,
    end_year: int = 2030,
    year_gap: int = 5,
    discount_rate: float = 0.05,
    dt_hours: int = 1,
    heat_file: str = "D_Heat_Household_J.txt",                    
    heat_unit: str = "J",                                          
    elec_profile_file: str = "corrected_eletricity_demand_2016.txt",
    electric_boiler_eta: float = 0.95,
    elec_price_eur_per_mwh: float = 80.0,      
    export_price_eur_per_mwh: float = 0.0,     
    cap_max_mw: Optional[float] = None,
    max_eout_mwh: Optional[float] = None,
) -> None:
    """
    Build a Techmap using Bensheim data.

    Commodities -> ["Heat", "Electricity", "External"]
    Processes   -> ["GridImportElec", "ElectricBoiler", "HeatDemand", "GridExportElec"]
    """
    workdir     = Path(workdir)
    techmap_dir = workdir / "Data" / "Techmap"
    ts_dir      = workdir / "Data" / "TimeSeries"
    techmap_dir.mkdir(parents=True, exist_ok=True)
    ts_dir.mkdir(parents=True, exist_ok=True)

    es = getattr(om, "energy_system", None) or getattr(om, "es", None)
    om_scenario = getattr(om, "scenario", None)
    if om_scenario is not None:
        start_year = getattr(om_scenario, "start_year", start_year)
        end_year   = getattr(om_scenario, "end_year", end_year)
        year_gap   = getattr(om_scenario, "year_gap", year_gap)

    data_dir = Path(data_dir) if data_dir is not None else Path("data")

    heat_path = data_dir / heat_file
    if not heat_path.exists():
        raise FileNotFoundError(f"Heat demand file not found: {heat_path}")
    heat_series = _load_numeric_txt(heat_path)
    if heat_unit.upper() == "J":
        annual_heat_mwh = float(heat_series.sum() / 3.6e9)
    elif heat_unit.upper() == "MWH":
        annual_heat_mwh = float(heat_series.sum())
    else:
        raise ValueError(f"Unsupported heat_unit='{heat_unit}'. Use 'J' or 'MWh'.")

    elec_path = data_dir / elec_profile_file
    if not elec_path.exists():
        raise FileNotFoundError(f"Electricity profile file not found: {elec_path}")
    profile_full = _load_numeric_txt(elec_path)
    s = float(profile_full.sum())
    if s <= 0:
        raise ValueError(f"Electricity profile {elec_path} sums to zero; cannot normalize.")
    profile_full = profile_full / s

    tss_file = ts_dir / f"{tss_name}.txt"
    if not tss_file.exists():
        tss_file.write_text("\n".join(str(i) for i in range(1, 225)), encoding="utf-8")
    tss_vals = [int(x) for x in tss_file.read_text(encoding="utf-8").strip().splitlines() if x]
    max_idx = max(tss_vals) if tss_vals else 0
    if max_idx >= len(profile_full):
        raise ValueError(
            f"TSS requires index {max_idx} but profile length is {len(profile_full)}. "
            f"Provide a full-year profile (e.g., 8760 values)."
        )

    demand_profile_name = "HeatDemandProfile"
    (ts_dir / f"{demand_profile_name}.txt").write_text(
        " ".join(f"{x:.8f}" for x in profile_full.tolist()), encoding="utf-8"
    )

    if cap_max_mw is None:
        cap_max_mw = max(1.0, annual_heat_mwh) * 10.0
    if max_eout_mwh is None:
        max_eout_mwh = annual_heat_mwh * 100.0

    print(
        f"[writer] Annual heat={annual_heat_mwh:,.1f} MWh | profile len={len(profile_full)} | "
        f"TSS max={max_idx} | EB cap_max={cap_max_mw} MW | EB max_eout={max_eout_mwh} MWh | "
        f"import={elec_price_eur_per_mwh} €/MWh | export={export_price_eur_per_mwh} €/MWh"
    )

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

    commodities = _infer_commodities(es)
    for must in ("Heat", "Electricity", "External"):
        if must not in commodities:
            commodities.append(must)

    commodity_df = pd.DataFrame(
        [{"commodity_name": c, "order": i + 1, "color": "", "description": ""} for i, c in enumerate(commodities)],
        columns=["commodity_name", "order", "color", "description"],
    )

    conv_procs  = ["GridImportElec", "ElectricBoiler", "HeatDemand", "GridExportElec"]
    convproc_df = pd.DataFrame(
        [{"conversion_process_name": t, "order": i + 1, "color": "", "description": ""} for i, t in enumerate(conv_procs)],
        columns=["conversion_process_name", "order", "color", "description"],
    )

    cs_rows = [
        # Grid import: External -> Electricity (priced supply)
        {
            "conversion_process_name": "GridImportElec",
            "commodity_in":  "External",
            "commodity_out": "Electricity",
            "scenario":      scenario_name,
            "efficiency":    1.0,
            "technical_availability": 1.0,
            "is_storage":    0.0,
            "max_eout":      1e12,
            "cap_max":       1e6,
            "opex_cost_energy": float(elec_price_eur_per_mwh),
        },
        # Electric boiler: Electricity -> Heat (supply)
        {
            "conversion_process_name": "ElectricBoiler",
            "commodity_in":  "Electricity",
            "commodity_out": "Heat",
            "scenario":      scenario_name,
            "efficiency":    float(electric_boiler_eta),
            "technical_availability": 1.0,
            "is_storage":    0.0,
            "cap_min":       0.0,
            "cap_max":       float(cap_max_mw),
            "max_eout":      float(max_eout_mwh),
        },
        # Heat demand (SINK): Heat -> External (forces heat to be met)
        {
            "conversion_process_name": "HeatDemand",
            "commodity_in":  "Heat",
            "commodity_out": "External",
            "scenario":      scenario_name,
            "efficiency":    1.0,
            "technical_availability": 1.0,
            "is_storage":    0.0,
            "min_eout":      float(annual_heat_mwh),
            "output_profile": "HeatDemandProfile",
        },
        # Grid export: Electricity -> External (dump any excess elec)
        {
            "conversion_process_name": "GridExportElec",
            "commodity_in":  "Electricity",
            "commodity_out": "External",
            "scenario":      scenario_name,
            "efficiency":    1.0,
            "technical_availability": 1.0,
            "is_storage":    0.0,
            "max_eout":      1e12,
            "cap_max":       1e6,
            "opex_cost_energy": float(export_price_eur_per_mwh),
        },
    ]

    base_cols  = ["conversion_process_name", "commodity_in", "commodity_out", "scenario"]
    param_cols = _param_cols_demodel_order()
    convsubproc_df = pd.DataFrame(cs_rows)
    for col in base_cols + param_cols:
        if col not in convsubproc_df.columns:
            convsubproc_df[col] = np.nan
    convsubproc_df = convsubproc_df[base_cols + param_cols]

    with pd.ExcelWriter(xlsx, engine="openpyxl", mode="w") as xw:
        units_df.to_excel(xw, sheet_name="Units", index=False)
        scenario_df.to_excel(xw, sheet_name="Scenario", index=False)
        tss_df.to_excel(xw, sheet_name="TSS", index=False)
        commodity_df.to_excel(xw, sheet_name="Commodity", index=False)
        convproc_df.to_excel(xw, sheet_name="ConversionProcess", index=False)

        cs_sheet = "ConversionSubProcess"
        header = list(convsubproc_df.columns)
        meta_desc, meta_units = _cs_meta_rows(header)
        pd.DataFrame(columns=header).to_excel(xw, sheet_name=cs_sheet, index=False)
        pd.DataFrame([meta_desc, meta_units], columns=header).to_excel(
            xw, sheet_name=cs_sheet, index=False, header=False, startrow=1
        )
        convsubproc_df.to_excel(xw, sheet_name=cs_sheet, index=False, header=False, startrow=3)

    ts_tss = ts_dir / f"{tss_name}.txt"
    if not ts_tss.exists():
        ts_tss.write_text("\n".join(str(i) for i in range(1, 225)), encoding="utf-8")

    print(f"[writer] Wrote techmap: {xlsx}")
    print("[writer] Sheets: Units, Scenario, TSS, Commodity, ConversionProcess, ConversionSubProcess")
    print(f"[writer] Techs: {len(conv_procs)} | Commodities: {len(commodities)} | CS rows: {len(convsubproc_df)}")


def _load_numeric_txt(path: Path) -> np.ndarray:
    txt = path.read_text(encoding="utf-8").strip().replace("\n", " ")
    vals = [v for v in txt.split() if v != ""]
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
            {"quantity": "energy",        "scale_factor": 0.001, "input": "GWh",     "output": "GWh"},
            {"quantity": "power",         "scale_factor": 0.001, "input": "GW",      "output": "GW"},
            {"quantity": "cost_energy",   "scale_factor": 0.001, "input": "Mio EUR/GWh", "output": "Mio EUR/GWh"},
            {"quantity": "cost_power",    "scale_factor": 0.001, "input": "Mio EUR/GW",  "output": "Mio EUR/GW"},
            {"quantity": "co2_spec",      "scale_factor": 1.0,   "input": "tco2/MWh","output": "tco2/MWh"},
            {"quantity": "co2_emissions", "scale_factor": 0.001, "input": "ktco2",   "output": "ktco2"},
            {"quantity": "money",         "scale_factor": 0.001, "input": "Mio EUR", "output": "Mio EUR"},
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
            "discount_rate":     float(discount_rate),
            "annual_co2_limit":  np.nan,
            "co2_price":         np.nan,
            "from_year":         int(start_year),
            "until_year":        int(end_year),
            "year_step":         int(year_gap),
            "TSS":               tss_name,
        }],
        columns=[
            "scenario_name", "discount_rate", "annual_co2_limit", "co2_price",
            "from_year", "until_year", "year_step", "TSS",
        ],
    )

def _tss_df(*, tss_name: str, dt_hours: int) -> pd.DataFrame:
    return pd.DataFrame([{"TSS_name": tss_name, "dt": int(dt_hours)}], columns=["TSS_name", "dt"])


def _infer_commodities(es) -> list[str]:
    candidates: list[str] = []

    def _as_names(obj) -> list[str]:
        if isinstance(obj, dict):               return [str(k) for k in obj.keys()]
        if isinstance(obj, (list, tuple, set)): return [str(x) for x in obj]
        for sub in ("commodity_names", "commodities"):
            if hasattr(obj, sub):
                val = getattr(obj, sub)
                val = val() if callable(val) else val
                return _as_names(val)
        return []

    for attr in ("commodities", "commodity_names", "get_commodities"):
        if hasattr(es, attr):
            try:
                v = getattr(es, attr)
                v = v() if callable(v) else v
                names = _as_names(v)
                if names:
                    candidates = names
                    break
            except Exception:
                pass

    if not candidates:
        candidates = ["Heat", "Electricity"]

    seen = set(); out: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c); out.append(str(c))
    return out

def _infer_techs(es) -> list[str]:
    techs: list[str] = []

    def _names_from_obj(obj) -> list[str]:
        if isinstance(obj, dict):               return [str(k) for k in obj.keys()]
        if isinstance(obj, (list, tuple, set)): return [getattr(t, "name", str(t)) for t in obj]
        for sub in ("all", "names"):
            if hasattr(obj, sub):
                col = getattr(obj, sub)
                col = col() if callable(col) else col
                return [getattr(t, "name", str(t)) for t in col]
        return []

    for attr in ("technologies", "technology_names", "get_technologies", "tech_registry", "technology_registry"):
        if hasattr(es, attr):
            try:
                obj = getattr(es, attr)
                obj = obj() if callable(obj) else obj
                techs = _names_from_obj(obj)
                if techs:
                    break
            except Exception:
                pass

    if not techs:
        techs = ["Boiler"]

    seen = set(); out: list[str] = []
    for t in techs:
        if t not in seen:
            seen.add(t); out.append(str(t))
    return out


def _param_cols_demodel_order() -> list[str]:
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

def _cs_meta_rows(header: list[str]) -> tuple[list[str], list[str]]:
    """
    Build the two meta rows (“description”, “units”).
    Unknown columns get empty meta.
    """
    desc_map = {
        "conversion_process_name": "process name",
        "commodity_in":            "commodity input",
        "commodity_out":           "commodity output",
        "scenario":                "name of scenario",
        "spec_co2":                "specific co2 emissions",
        "efficiency":              "efficiency",
        "technical_lifetime":      "technical lifetime",
        "technical_availability":  "technical availability factor",
        "c_rate":                  "charge rate",
        "efficiency_charge":       "charge efficiency",
        "is_storage":              "is storage? 1/0",
        "opex_cost_energy":        "variable energy OPEX",
        "opex_cost_power":         "variable power OPEX",
        "capex_cost_power":        "CAPEX per unit power",
        "max_eout":                "max energy output per year",
        "min_eout":                "min energy output per year",
        "cap_min":                 "minimum capacity",
        "cap_max":                 "maximum capacity",
        "cap_res_min":             "min reserve capacity",
        "cap_res_max":             "max reserve capacity",
        "out_frac_min":            "minimum output fraction",
        "out_frac_max":            "maximum output fraction",
        "in_frac_min":             "minimum input fraction",
        "in_frac_max":             "maximum input fraction",
        "availability_profile":    "availability profile name",
        "output_profile":          "output profile name",
        "color":                   "plot color",
        "order":                   "plot order",
        "description":             "description",
    }
    units_map = {
        "spec_co2":               "tco2/MWh",
        "efficiency":             "–",
        "technical_lifetime":     "y",
        "technical_availability": "–",
        "c_rate":                 "1/h",
        "efficiency_charge":      "–",
        "is_storage":             "0/1",
        "opex_cost_energy":       "Mio EUR/GWh",
        "opex_cost_power":        "Mio EUR/GW",
        "capex_cost_power":       "Mio EUR/GW",
        "max_eout":               "GWh",
        "min_eout":               "GWh",
        "cap_min":                "GW",
        "cap_max":                "GW",
        "cap_res_min":            "GW",
        "cap_res_max":            "GW",
        "out_frac_min":           "–",
        "out_frac_max":           "–",
        "in_frac_min":            "–",
        "in_frac_max":            "–",
        "availability_profile":   "name",
        "output_profile":         "name",
        "conversion_process_name":"name",
        "commodity_in":           "name",
        "commodity_out":          "name",
        "scenario":               "name",
        "color":                  "hex",
        "order":                  "int",
        "description":            "text",
    }
    desc  = [desc_map.get(c, "") for c in header]
    units = [units_map.get(c, "") for c in header]
    return desc, units

def _convsubproc_df(techs: list[str], commodities: list[str], scenario_name: str) -> pd.DataFrame:
    """
    Fallback data for write_cesm_inputs_from_es:
    make each tech convert Electricity -> Heat with empty params.
    """
    base_cols  = ["conversion_process_name", "commodity_in", "commodity_out", "scenario"]
    param_cols = _param_cols_demodel_order()

    rows = []
    cout = "Heat" if "Heat" in commodities else commodities[0]
    cin  = "Electricity" if "Electricity" in commodities and "Heat" in commodities else commodities[0]
    for t in techs:
        base = {
            "conversion_process_name": t,
            "commodity_in":  cin,
            "commodity_out": cout,
            "scenario":      scenario_name,
        }
        for k in param_cols:
            base.setdefault(k, np.nan)
        rows.append(base)

    cols = base_cols + param_cols
    return pd.DataFrame(rows, columns=cols)
