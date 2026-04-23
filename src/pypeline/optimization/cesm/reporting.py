from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


def _escape(value: Any) -> str:
    return html.escape(str(value))


def _render_mapping_table(title: str, values: dict[str, Any]) -> str:
    if not values:
        return f"<h3>{_escape(title)}</h3><p><em>No data</em></p>"
    items = list(values.items())
    if items and all(re.fullmatch(r"\d{4}", str(key).strip()) for key, _ in items):
        items.sort(key=lambda kv: int(str(kv[0]).strip()))
    rows = ["<table><tr><th>Key</th><th>Value</th></tr>"]
    for key, value in items:
        rows.append(f"<tr><td>{_escape(key)}</td><td>{_escape(value)}</td></tr>")
    rows.append("</table>")
    return f"<h3>{_escape(title)}</h3>" + "\n".join(rows)


def _render_df(title: str, df: pd.DataFrame, *, max_rows: int = 500) -> str:
    if df is None or df.empty:
        return f"<h3>{_escape(title)}</h3><p><em>No rows</em></p>"

    clipped = df.head(max_rows)
    table_html = clipped.to_html(index=False, classes="tbl", border=0, escape=True)
    suffix = "" if len(df) <= max_rows else f"<p><em>Showing first {max_rows} of {len(df)} rows.</em></p>"
    return f"<h3>{_escape(title)}</h3><div class='tbl-wrap'>{table_html}</div>{suffix}"


def _render_card_grid(grid_class: str, *cards: str) -> str:
    rendered = "".join(f"<section class='card'>{card}</section>" for card in cards if card)
    if not rendered:
        return ""
    return f"<div class='{grid_class}'>{rendered}</div>"


def _render_columns(*cards: str) -> str:
    return _render_card_grid("grid-2", *cards)


def _render_columns_3(*cards: str) -> str:
    return _render_card_grid("grid-3", *cards)


def _with_table_units(df: pd.DataFrame) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    rename_map = {
        "capacity": "capacity [MW]",
        "cap_new": "cap_new [MW]",
        "energy_output": "energy_output [MWh]",
        "opex": "opex [EUR]",
        "capex_gross": "capex_gross [EUR]",
        "capex_net": "capex_net [EUR]",
        "totex": "totex [EUR]",
    }
    return df.rename(columns={col: rename_map[col] for col in df.columns if col in rename_map})


def _name_column(df: pd.DataFrame) -> str | None:
    for candidate in ("technology", "process", "conversion_process_name"):
        if candidate in df.columns:
            return candidate
    return None


def _district_label_from_process(process_name: str) -> str | None:
    match = re.search(r"_d(\d+)$", str(process_name).strip(), flags=re.IGNORECASE)
    if not match:
        return None
    return f"D{int(match.group(1))}"


def _district_labels_in_df(df: pd.DataFrame) -> list[str]:
    if df is None or df.empty:
        return []
    name_col = _name_column(df)
    if name_col is None:
        return []
    district_labels = df[name_col].astype(str).map(_district_label_from_process)
    return sorted(
        {label for label in district_labels.dropna().unique()},
        key=lambda label: int(str(label)[1:]) if str(label).startswith("D") and str(label)[1:].isdigit() else 10**9,
    )


def _split_df_by_district(
    df: pd.DataFrame,
    *,
    expected_districts: list[str] | None = None,
) -> list[tuple[str, pd.DataFrame]]:
    expected = [str(label) for label in (expected_districts or [])]

    if df is None:
        return []
    if df.empty:
        if expected:
            empty = df.head(0).copy()
            return [(district, empty.copy()) for district in expected]
        return [("All", df.head(0).copy())]

    name_col = _name_column(df)
    if name_col is None:
        if expected:
            empty = df.head(0).copy()
            return [(district, empty.copy()) for district in expected]
        return [("All", df.reset_index(drop=True))]

    district_labels = df[name_col].astype(str).map(_district_label_from_process)
    districts = expected or sorted(
        {label for label in district_labels.dropna().unique()},
        key=lambda label: int(str(label)[1:]) if str(label).startswith("D") and str(label)[1:].isdigit() else 10**9,
    )

    by_district: list[tuple[str, pd.DataFrame]] = []
    for district in districts:
        district_rows = df.loc[district_labels == district].reset_index(drop=True)
        if expected or not district_rows.empty:
            by_district.append((str(district), district_rows))

    other_rows = df.loc[district_labels.isna()].reset_index(drop=True)
    if not other_rows.empty:
        by_district.append(("Other", other_rows))

    if not by_district:
        return [("All", df.reset_index(drop=True))]
    return by_district


def _base_process_token(process_name: str) -> str:
    token = str(process_name).strip().lower()
    return re.sub(r"_d\d+$", "", token)


def _is_heat_exchanger_process(process_name: str) -> bool:
    token = _base_process_token(process_name)
    return token.startswith("heat_exchanger") or token.startswith("heatexchanger")


def _is_heat_grid_process(process_name: str) -> bool:
    token = _base_process_token(process_name)
    return token.startswith("heat_grid")


def _is_pipe_process(process_name: str) -> bool:
    token = str(process_name).strip().lower()
    return token.startswith("pipe_d")


def _is_central_process(process_name: str) -> bool:
    return _base_process_token(process_name).startswith("cen_")


def _is_indirect_process(process_name: str) -> bool:
    return _base_process_token(process_name).startswith("ind_")


def _is_heatdemand_process(process_name: str) -> bool:
    return _base_process_token(process_name).startswith("heatdemand")


def _cost_mapping_to_df(cost_mapping: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for year, payload in (cost_mapping or {}).items():
        if not isinstance(payload, dict):
            continue
        rows.append(
            {
                "year": str(year),
                "opex": payload.get("opex", 0.0),
                "capex_gross": payload.get("capex_gross", 0.0),
                "capex_net": payload.get("capex_net", 0.0),
                "totex": payload.get("totex", 0.0),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["year", "opex", "capex_gross", "capex_net", "totex"])
    df = pd.DataFrame(rows)
    try:
        df["year_int"] = df["year"].astype(int)
        df = df.sort_values(["year_int"]).drop(columns=["year_int"])
    except Exception:
        pass
    return df


def _cost_by_tech_year_to_df(cost_mapping: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for technology, per_year in (cost_mapping or {}).items():
        if not isinstance(per_year, dict):
            continue
        for year, payload in per_year.items():
            if not isinstance(payload, dict):
                continue
            rows.append(
                {
                    "technology": str(technology),
                    "year": str(year),
                    "opex": payload.get("opex", 0.0),
                    "capex_gross": payload.get("capex_gross", 0.0),
                    "capex_net": payload.get("capex_net", 0.0),
                    "totex": payload.get("totex", 0.0),
                }
            )
    if not rows:
        return pd.DataFrame(columns=["technology", "year", "opex", "capex_gross", "capex_net", "totex"])
    df = pd.DataFrame(rows)
    try:
        df["year_int"] = df["year"].astype(int)
        df = df.sort_values(["technology", "year_int"]).drop(columns=["year_int"])
    except Exception:
        pass
    return df


def _scalar_by_tech_year_to_df(
    mapping: dict[str, Any],
    *,
    value_col: str,
    drop_zero_total_technologies: bool = False,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for technology, per_year in (mapping or {}).items():
        if not isinstance(per_year, dict):
            continue
        for year, value in per_year.items():
            try:
                numeric = float(value)
            except Exception:
                continue
            rows.append(
                {
                    "technology": str(technology),
                    "year": str(year),
                    value_col: numeric,
                }
            )
    if not rows:
        return pd.DataFrame(columns=["technology", "year", value_col])
    df = pd.DataFrame(rows)
    if drop_zero_total_technologies:
        non_zero_mask = (
            df.groupby("technology")[value_col]
            .transform(lambda s: (s.abs() > 1e-12).any())
        )
        df = df[non_zero_mask].copy()
        if df.empty:
            return pd.DataFrame(columns=["technology", "year", value_col])
    try:
        df["year_int"] = df["year"].astype(int)
        df = df.sort_values(["technology", "year_int"]).drop(columns=["year_int"])
    except Exception:
        pass
    return df


def _is_supply_or_system_process(process_name: str, *, include_heatdemand: bool = True) -> bool:
    token = _base_process_token(process_name)
    if token.endswith("supply"):
        return True
    if token.startswith("grid_electricity") or token.startswith("gridelectricity"):
        return True
    if token.startswith("grid_hydrogen") or token.startswith("gridhydrogen"):
        return True
    if include_heatdemand and token.startswith("heatdemand"):
        return True
    if token.startswith("gridexport"):
        return True
    if token.startswith("import"):
        return True
    return False


def _concat_df(*dfs: pd.DataFrame) -> pd.DataFrame:
    templates = [df for df in dfs if df is not None]
    if not templates:
        return pd.DataFrame()
    non_empty = [df for df in templates if not df.empty]
    if not non_empty:
        return templates[0].head(0).copy()
    return pd.concat(non_empty, ignore_index=True)


def _drop_always_zero_technologies(
    df: pd.DataFrame,
    *,
    value_columns: tuple[str, ...],
    tol: float = 1e-12,
) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    name_col = _name_column(df)
    if name_col is None:
        return df

    existing_value_columns = [col for col in value_columns if col in df.columns]
    if not existing_value_columns:
        return df

    numeric = (
        df[existing_value_columns]
        .apply(pd.to_numeric, errors="coerce")
        .fillna(0.0)
    )
    row_has_non_zero = numeric.abs().gt(tol).any(axis=1)
    keep_mask = row_has_non_zero.groupby(df[name_col].astype(str)).transform("any")
    filtered = df.loc[keep_mask].copy()
    return filtered.reset_index(drop=True)


def _split_df_into_process_groups(
    df: pd.DataFrame,
    *,
    include_heatdemand: bool = True,
) -> dict[str, pd.DataFrame]:
    if df is None:
        df = pd.DataFrame()
    if df.empty:
        empty = df.head(0).copy()
        return {
            "central": empty.copy(),
            "indirect": empty.copy(),
            "supply": empty.copy(),
            "heat_grid": empty.copy(),
            "heat_exchanger": empty.copy(),
            "other": empty.copy(),
        }

    name_col = _name_column(df)
    if name_col is None:
        empty = df.head(0).copy()
        return {
            "central": empty.copy(),
            "indirect": empty.copy(),
            "supply": empty.copy(),
            "heat_grid": empty.copy(),
            "heat_exchanger": empty.copy(),
            "other": df.reset_index(drop=True),
        }

    names = df[name_col].astype(str)
    heat_exchanger_mask = names.map(_is_heat_exchanger_process)
    heat_grid_mask = names.map(lambda value: _is_heat_grid_process(value) or _is_pipe_process(value))
    central_mask = names.map(_is_central_process)
    indirect_mask = names.map(_is_indirect_process)
    supply_mask = names.map(
        lambda value: _is_supply_or_system_process(value, include_heatdemand=include_heatdemand)
    )
    heatdemand_mask = names.map(_is_heatdemand_process)
    drop_mask = heatdemand_mask if not include_heatdemand else pd.Series(False, index=df.index)

    heat_exchanger_df = df.loc[(~drop_mask) & heat_exchanger_mask].reset_index(drop=True)
    heat_grid_df = df.loc[(~drop_mask) & (~heat_exchanger_mask) & heat_grid_mask].reset_index(drop=True)
    central_df = df.loc[(~drop_mask) & (~heat_exchanger_mask) & (~heat_grid_mask) & central_mask].reset_index(drop=True)
    indirect_df = df.loc[
        (~drop_mask) & (~heat_exchanger_mask) & (~heat_grid_mask) & (~central_mask) & indirect_mask
    ].reset_index(drop=True)
    supply_df = df.loc[
        (~drop_mask)
        & (~heat_exchanger_mask)
        & (~heat_grid_mask)
        & (~central_mask)
        & (~indirect_mask)
        & supply_mask
    ].reset_index(drop=True)
    other_df = df.loc[
        (~drop_mask)
        & (~heat_exchanger_mask)
        & (~heat_grid_mask)
        & (~central_mask)
        & (~indirect_mask)
        & (~supply_mask)
    ].reset_index(drop=True)

    return {
        "central": central_df,
        "indirect": indirect_df,
        "supply": supply_df,
        "heat_grid": heat_grid_df,
        "heat_exchanger": heat_exchanger_df,
        "other": other_df,
    }


def _aggregate_costs_by_year(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "year" not in df.columns:
        return pd.DataFrame(columns=["year", "opex", "capex_gross", "capex_net", "totex"])

    value_cols = [col for col in ("opex", "capex_gross", "capex_net", "totex") if col in df.columns]
    if not value_cols:
        return pd.DataFrame(columns=["year", "opex", "capex_gross", "capex_net", "totex"])

    grouped = (
        df[["year", *value_cols]]
        .copy()
        .groupby("year", as_index=False)[value_cols]
        .sum()
    )

    for col in ("opex", "capex_gross", "capex_net", "totex"):
        if col not in grouped.columns:
            grouped[col] = 0.0

    try:
        grouped["year_int"] = grouped["year"].astype(int)
        grouped = grouped.sort_values(["year_int"]).drop(columns=["year_int"])
    except Exception:
        pass

    return grouped[["year", "opex", "capex_gross", "capex_net", "totex"]]


def write_cesm_results_html_report(
    *,
    results_obj: Any,
    output_path: Path,
    title: str,
    metadata: dict[str, Any] | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    raw = getattr(results_obj, "raw", {})
    kpis = raw.get("kpis", {}) if isinstance(raw, dict) else {}

    active_capacities = getattr(results_obj, "active_capacities", pd.DataFrame())
    yearly_energy_outputs = getattr(results_obj, "yearly_energy_outputs", pd.DataFrame())

    active_capacities = _drop_always_zero_technologies(
        active_capacities,
        value_columns=("capacity",),
    )
    yearly_energy_outputs = _drop_always_zero_technologies(
        yearly_energy_outputs,
        value_columns=("energy_output",),
    )

    costs = {
        "opex [EUR]": getattr(results_obj, "opex", None),
        "capex [EUR]": getattr(results_obj, "capex", None),
        "totex [EUR]": getattr(results_obj, "totex", None),
    }

    emissions = kpis.get("emissions_by_year", {}) if isinstance(kpis, dict) else {}
    net_balance = kpis.get("net_energy_balance_by_year", {}) if isinstance(kpis, dict) else {}

    cost_dhn_by_year = kpis.get("objective_cost_dhn_by_year", {}) if isinstance(kpis, dict) else {}
    cost_by_tech_year = kpis.get("objective_cost_by_tech_year", {}) if isinstance(kpis, dict) else {}
    cap_new_by_tech_year = kpis.get("cap_new_by_tech_year", {}) if isinstance(kpis, dict) else {}

    parts = [
        "<!DOCTYPE html>",
        "<html lang='en'>",
        "<head><meta charset='utf-8'>",
        f"<title>{_escape(title)}</title>",
        "<style>",
        "body { font-family: 'Segoe UI', Arial, sans-serif; margin: 2em; background: #fafafa; color: #1a1a1a; }",
        "h1, h2, h3 { margin-top: 1.2em; }",
        "h2 { border-bottom: 2px solid #ddd; padding-bottom: 0.2em; }",
        "table { border-collapse: collapse; margin: 0.4em 0 0.8em; font-size: 0.92em; background: #fff; width: 100%; }",
        "th, td { border: 1px solid #ccc; padding: 4px 8px; text-align: left; vertical-align: top; }",
        "th { background: #efefef; }",
        ".grid-2 { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); margin: 0.5em 0 1em; }",
        ".grid-3 { display: grid; gap: 1rem; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 0.5em 0 1em; }",
        "@media (max-width: 1100px) { .grid-3 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }",
        "@media (max-width: 760px) { .grid-3 { grid-template-columns: 1fr; } }",
        ".card { background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 0.7em 0.9em; }",
        ".card h3 { margin-top: 0.2em; }",
        ".tbl-wrap { overflow-x: auto; }",
        "details { background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 0.5em 0.9em; margin: 0.8em 0 1em; }",
        "summary { cursor: pointer; font-weight: 600; }",
        ".hint { color: #4b4b4b; font-size: 0.92em; margin: 0.4em 0 0.8em; }",
        "code { white-space: pre-wrap; display: block; background: #fff; border: 1px solid #ddd; padding: 0.8em; }",
        "</style>",
        "</head><body>",
        f"<h1>{_escape(title)}</h1>",
        "<p>Generated from CESM run results.</p>",
    ]

    if metadata:
        parts.append("<h2>Run Overview</h2>")
        parts.append(
            _render_columns(
                _render_mapping_table("Metadata", {str(k): v for k, v in metadata.items()}),
                _render_mapping_table("System Costs", costs),
                _render_mapping_table("Emissions By Year", emissions if isinstance(emissions, dict) else {}),
                _render_mapping_table("Net Energy Balance By Year", net_balance if isinstance(net_balance, dict) else {}),
            )
        )
    else:
        parts.append("<h2>Run Overview</h2>")
        parts.append(
            _render_columns(
                _render_mapping_table("System Costs", costs),
                _render_mapping_table("Emissions By Year", emissions if isinstance(emissions, dict) else {}),
                _render_mapping_table("Net Energy Balance By Year", net_balance if isinstance(net_balance, dict) else {}),
            )
        )

    parts.append("<h2>Standardized Results</h2>")
    energy_name_col = _name_column(yearly_energy_outputs)
    if energy_name_col is None:
        heat_demand_rows = yearly_energy_outputs.head(0).copy()
    else:
        heat_demand_mask = yearly_energy_outputs[energy_name_col].astype(str).map(_is_heatdemand_process)
        heat_demand_rows = yearly_energy_outputs.loc[heat_demand_mask].reset_index(drop=True)

    # Split core process groups while keeping HeatDemand separate for its own table.
    energy_groups = _split_df_into_process_groups(yearly_energy_outputs, include_heatdemand=False)
    energy_indirect_rows = _concat_df(
        energy_groups["indirect"],
        energy_groups["other"],
    )
    energy_districts = _district_labels_in_df(yearly_energy_outputs)
    energy_central_by_district = _split_df_by_district(
        energy_groups["central"],
        expected_districts=energy_districts,
    )
    energy_indirect_by_district = _split_df_by_district(
        energy_indirect_rows,
        expected_districts=energy_districts,
    )

    parts.append("<details>")
    parts.append("<summary>Central Technologies</summary>")
    parts.append(
        _render_columns_3(
            *[
                _render_df(
                    f"Yearly Output - Cen_tech ({district})",
                    _with_table_units(rows),
                )
                for district, rows in energy_central_by_district
            ]
        )
    )
    parts.append("</details>")

    parts.append("<details>")
    parts.append("<summary>Indirect Technologies</summary>")
    parts.append(
        _render_columns_3(
            *[
                _render_df(
                    f"Yearly Outputs - Ind_Tech ({district})",
                    _with_table_units(rows),
                )
                for district, rows in energy_indirect_by_district
            ]
        )
    )
    parts.append("</details>")

    heat_grid_rows = energy_groups["heat_grid"]
    heat_grid_name_col = _name_column(heat_grid_rows)
    if heat_grid_name_col is None:
        pipe_energy_rows = heat_grid_rows.head(0).copy()
        heat_grid_non_pipe_rows = heat_grid_rows.copy()
    else:
        pipe_mask = heat_grid_rows[heat_grid_name_col].astype(str).map(_is_pipe_process)
        pipe_energy_rows = heat_grid_rows.loc[pipe_mask].reset_index(drop=True)
        heat_grid_non_pipe_rows = heat_grid_rows.loc[~pipe_mask].reset_index(drop=True)

    parts.append("<details>")
    parts.append("<summary>Pipes / Heat Exchanger / Heat Grid</summary>")
    parts.append(
        _render_columns_3(
            _render_df(
                "Yearly Outputs - Pipes",
                _with_table_units(pipe_energy_rows),
            ),
            _render_df(
                "Yearly  Outputs - Heat Exchanger",
                _with_table_units(energy_groups["heat_exchanger"]),
            ),
            _render_df(
                "Yearly Outputs - Heat Grid",
                _with_table_units(heat_grid_non_pipe_rows),
            ),
        )
    )
    parts.append("</details>")

    parts.append("<details>")
    parts.append("<summary>Supply / Demand</summary>")
    parts.append(
        _render_columns(
            _render_df(
                "Yearly Outputs - Supply",
                _with_table_units(energy_groups["supply"]),
            ),
            _render_df(
                "Yearly Outputs - Heat Demand",
                _with_table_units(heat_demand_rows),
            ),
        )
    )
    parts.append("</details>")

    active_capacity_groups = _split_df_into_process_groups(active_capacities)
    active_capacities_no_supply = _concat_df(
        active_capacity_groups["central"],
        active_capacity_groups["indirect"],
        active_capacity_groups["heat_grid"],
        active_capacity_groups["heat_exchanger"],
        active_capacity_groups["other"],
    )

    parts.append("<details>")
    parts.append("<summary>Active Capacities</summary>")
    parts.append(
        _render_df("Active Capacities", _with_table_units(active_capacities_no_supply))
    )
    parts.append("</details>")

    parts.append("<details>")
    parts.append("<summary>Built Capacity (cap_new)</summary>")
    cap_new_df = _scalar_by_tech_year_to_df(
        cap_new_by_tech_year,
        value_col="cap_new",
        drop_zero_total_technologies=True,
    )
    cap_new_groups = _split_df_into_process_groups(cap_new_df)
    cap_new_tech_only = _concat_df(
        cap_new_groups["central"],
        cap_new_groups["indirect"],
        cap_new_groups["heat_grid"],
        cap_new_groups["heat_exchanger"],
        cap_new_groups["other"],
    )
    parts.append(
        _render_df(
            "Built Capacity (cap_new)",
            _with_table_units(cap_new_tech_only),
            max_rows=2000,
        )
    )
    parts.append("</details>")

    objective_by_process = _drop_always_zero_technologies(
        _cost_by_tech_year_to_df(cost_by_tech_year),
        value_columns=("opex", "capex_gross", "capex_net", "totex"),
    )
    objective_groups = _split_df_into_process_groups(objective_by_process, include_heatdemand=False)
    objective_by_year_no_heatdemand = _aggregate_costs_by_year(
        _concat_df(
            objective_groups["central"],
            objective_groups["indirect"],
            objective_groups["supply"],
            objective_groups["heat_grid"],
            objective_groups["heat_exchanger"],
            objective_groups["other"],
        )
    )
    objective_non_supply = _concat_df(
        objective_groups["central"],
        objective_groups["indirect"],
        objective_groups["heat_grid"],
        objective_groups["heat_exchanger"],
        objective_groups["other"],
    )
    objective_districts = _district_labels_in_df(objective_by_process)
    objective_non_supply_by_district = _split_df_by_district(
        objective_non_supply,
        expected_districts=objective_districts,
    )

    parts.append("<h2>Objective Costs</h2>")
    parts.append(
        "<p class='hint'>Objective Cost By Year decomposes the modeled objective into operating cost and investment terms for each modeled year.</p>"
    )
    parts.append(
        _render_columns(
            _render_df(
                "Objective Cost By Year",
                _with_table_units(objective_by_year_no_heatdemand),
            ),
            _render_df(
                "Objective Cost By Year (DHN / Heat Ex. / Pipe)",
                _with_table_units(_cost_mapping_to_df(cost_dhn_by_year)),
            ),
        )
    )

    parts.append("<details>")
    parts.append("<summary>Objective Cost By Process</summary>")
    parts.append(
        _render_columns_3(
            *[
                _render_df(
                    f"Objective Cost By Process ({district})",
                    _with_table_units(rows),
                    max_rows=2000,
                )
                for district, rows in objective_non_supply_by_district
            ]
        )
    )
    parts.append(
        _render_columns(
            _render_df(
                "Supply",
                _with_table_units(objective_groups["supply"]),
                max_rows=2000,
            ),
        )
    )
    parts.append("</details>")

    parts.append("<details>")
    parts.append("<summary>Raw KPI JSON</summary>")
    safe_json = json.dumps(kpis if isinstance(kpis, dict) else {}, indent=2, sort_keys=True, default=str)
    parts.append(f"<code>{_escape(safe_json)}</code>")
    parts.append("</details>")

    parts.append("</body></html>")
    output_path.write_text("\n".join(parts), encoding="utf-8")
    return output_path
