from __future__ import annotations
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING
import math
import logging
import numpy as np
import pandas as pd
from pypeline.validation import sanitize_price_map

logger = logging.getLogger(__name__)

CONV_SUBPROC_BASE_COLS: tuple[str, ...] = (
    "conversion_process_name",
    "commodity_in",
    "commodity_out",
    "scenario",
)

CONV_SUBPROC_PARAM_COLS: tuple[str, ...] = (
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
    "capex_cost_base",
    "cap_active",
    "max_units",
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
)

if TYPE_CHECKING:
    from pypeline.energy_system.core import EnergySystem
    from pypeline.energy_system.core import Scenario


def _canon_co(name: Optional[str]) -> str:
    """Canonicalize common commodity names.

    Returns the normalized string used in techmap sheets. Keeps unknown names unchanged.
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


def _write_standard_sheets(
    writer: pd.ExcelWriter,
    *,
    units_df: pd.DataFrame,
    scenario_df: pd.DataFrame,
    tss_df: pd.DataFrame,
    commodity_df: pd.DataFrame,
    convproc_df: pd.DataFrame,
) -> None:
    """Write common workbook sheets."""
    units_df.to_excel(writer, sheet_name="Units", index=False)
    scenario_df.to_excel(writer, sheet_name="Scenario", index=False)
    tss_df.to_excel(writer, sheet_name="TSS", index=False)
    commodity_df.to_excel(writer, sheet_name="Commodity", index=False)
    convproc_df.to_excel(writer, sheet_name="ConversionProcess", index=False)


def _convsubproc_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    convsubproc_df = pd.DataFrame(rows)
    base_cols = list(CONV_SUBPROC_BASE_COLS)
    param_cols = list(CONV_SUBPROC_PARAM_COLS)
    missing_cols = [col for col in base_cols + param_cols if col not in convsubproc_df.columns]
    for col in missing_cols:
        convsubproc_df[col] = None
    return convsubproc_df[base_cols + param_cols]


def _format_year_profile(pairs: list[tuple[int, float]]) -> str | None:
    if not pairs:
        return None
    segments: list[str] = []
    for year, value in pairs:
        year_i = int(year)
        value_f = float(value)
        segments.append(f"{year_i} {value_f:.10g}")
    return "[" + " ; ".join(segments) + "]"


def _profile_to_map_and_scalar(
    value: Any,
    *,
    ignore_invalid: bool = False,
) -> tuple[dict[int, float], float | None]:
    mapping: dict[int, float] = {}
    scalar: float | None = None
    if value is None:
        return mapping, scalar
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("[") and raw.endswith("]"):
            body = raw[1:-1]
            for chunk in body.split(";"):
                parts = chunk.strip().split()
                if len(parts) < 2:
                    continue
                try:
                    mapping[int(float(parts[0]))] = float(parts[1])
                except (TypeError, ValueError):
                    if ignore_invalid:
                        continue
                    raise
            return mapping, None
        try:
            scalar = float(raw)
        except (TypeError, ValueError):
            if ignore_invalid:
                scalar = None
            else:
                raise
        return mapping, scalar
    try:
        scalar = float(value)
    except (TypeError, ValueError):
        if ignore_invalid:
            scalar = None
        else:
            raise
    return mapping, scalar


def _value_for_year(
    mapping: dict[int, float],
    scalar: float | None,
    year: int,
) -> float | None:
    if mapping:
        if year in mapping:
            return float(mapping[year])
        return None
    return scalar


def _write_techmap_workbook(
    xlsx: Path,
    *,
    units_df: pd.DataFrame,
    scenario_df: pd.DataFrame,
    tss_df: pd.DataFrame,
    commodity_df: pd.DataFrame,
    convproc_df: pd.DataFrame,
    convsubproc_df: pd.DataFrame,
) -> None:
    with pd.ExcelWriter(xlsx, engine="openpyxl", mode="w") as xw:
        _write_standard_sheets(
            xw,
            units_df=units_df,
            scenario_df=scenario_df,
            tss_df=tss_df,
            commodity_df=commodity_df,
            convproc_df=convproc_df,
        )
        cs_sheet = "ConversionSubProcess"
        pd.DataFrame(columns=convsubproc_df.columns).to_excel(xw, sheet_name=cs_sheet, index=False)
        convsubproc_df.to_excel(xw, sheet_name=cs_sheet, index=False, header=False, startrow=3)
    logger.info("Wrote techmap: %s", xlsx)
    logger.info(
        "Techmap sheets: Units, Scenario, TSS, Commodity, ConversionProcess, ConversionSubProcess"
    )


def commodity_config_from_energy_system(es: "EnergySystem") -> tuple[dict[str, float], dict[str, float]]:
    """Extract and validate commodity grid/supply prices from an EnergySystem."""
    config_raw = getattr(es, "commodity_config", None)
    if config_raw is None:
        raise ValueError("EnergySystem.commodity_config is required to provide commodity prices")
    if not isinstance(config_raw, dict):
        raise ValueError("EnergySystem.commodity_config must be a mapping")

    grid_raw = config_raw.get("grid_prices")
    supply_raw = config_raw.get("supply_prices_eur_per_mwh")
    if not isinstance(grid_raw, dict) or not isinstance(supply_raw, dict):
        raise ValueError("EnergySystem.commodity_config must include grid_prices and supply_prices_eur_per_mwh mappings")

    return sanitize_price_map(grid_raw), sanitize_price_map(supply_raw)


def resolve_retain_schedule(
    *,
    explicit_schedule: Optional[list[float]],
    scenario: "Scenario" | None,
) -> Optional[list[float]]:
    """Resolve retention schedule from explicit values or scenario fields."""
    if explicit_schedule:
        return explicit_schedule
    if scenario is None:
        return None

    scen_schedule = getattr(scenario, "retain_existing_output_schedule", None)
    if scen_schedule:
        return scen_schedule

    drop_val_raw = getattr(scenario, "retain_existing_output_drop_per_year", None)
    if drop_val_raw is None:
        return None
    try:
        drop_val = float(drop_val_raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(drop_val):
        return None

    drop_val = max(0.0, drop_val)
    years = scenario.years() if hasattr(scenario, "years") else []
    if not years:
        return None
    start_year = years[0]
    return [(1.0 - drop_val) ** (year - start_year) for year in years]