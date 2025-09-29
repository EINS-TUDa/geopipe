from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


def _canon_co(name: Optional[str]) -> str:
    """Canonicalize common commodity names.

    Returns the normalized string used in techmap sheets. Keeps unknown names
    unchanged.
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
    return name


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


def _scenario_df(*, scenario_name: str, start_year: int | None, end_year: int | None, year_gap: int | None, tss_name: str, discount_rate: float) -> pd.DataFrame:
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
