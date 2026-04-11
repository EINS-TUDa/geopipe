from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

import pandas as pd


def _escape(value: Any) -> str:
    return html.escape(str(value))


def _render_mapping_table(title: str, values: dict[str, Any]) -> str:
    if not values:
        return f"<h3>{_escape(title)}</h3><p><em>No data</em></p>"
    rows = ["<table><tr><th>Key</th><th>Value</th></tr>"]
    for key, value in values.items():
        rows.append(f"<tr><td>{_escape(key)}</td><td>{_escape(value)}</td></tr>")
    rows.append("</table>")
    return f"<h3>{_escape(title)}</h3>" + "\n".join(rows)


def _render_df(title: str, df: pd.DataFrame, *, max_rows: int = 500) -> str:
    if df is None or df.empty:
        return f"<h3>{_escape(title)}</h3><p><em>No rows</em></p>"

    clipped = df.head(max_rows)
    table_html = clipped.to_html(index=False, classes="tbl", border=0, escape=True)
    suffix = "" if len(df) <= max_rows else f"<p><em>Showing first {max_rows} of {len(df)} rows.</em></p>"
    return f"<h3>{_escape(title)}</h3>{table_html}{suffix}"


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

    costs = {
        "opex": getattr(results_obj, "opex", None),
        "capex": getattr(results_obj, "capex", None),
        "totex": getattr(results_obj, "totex", None),
    }

    parts = [
        "<!DOCTYPE html>",
        "<html lang='en'>",
        "<head><meta charset='utf-8'>",
        f"<title>{_escape(title)}</title>",
        "<style>",
        "body { font-family: 'Segoe UI', Arial, sans-serif; margin: 2em; background: #fafafa; color: #1a1a1a; }",
        "h1, h2, h3 { margin-top: 1.2em; }",
        "h2 { border-bottom: 2px solid #ddd; padding-bottom: 0.2em; }",
        "table { border-collapse: collapse; margin: 0.8em 0 1.2em; font-size: 0.92em; background: #fff; }",
        "th, td { border: 1px solid #ccc; padding: 4px 8px; text-align: left; vertical-align: top; }",
        "th { background: #efefef; }",
        "code { white-space: pre-wrap; display: block; background: #fff; border: 1px solid #ddd; padding: 0.8em; }",
        "</style>",
        "</head><body>",
        f"<h1>{_escape(title)}</h1>",
        "<p>Generated from CESM run results.</p>",
    ]

    if metadata:
        parts.append("<h2>Run Metadata</h2>")
        parts.append(_render_mapping_table("Metadata", {str(k): v for k, v in metadata.items()}))

    parts.append("<h2>Costs</h2>")
    parts.append(_render_mapping_table("System Costs", costs))

    parts.append("<h2>Standardized Results</h2>")
    parts.append(_render_df("Active Capacities", active_capacities))
    parts.append(_render_df("Yearly Energy Outputs", yearly_energy_outputs))

    parts.append("<h2>CESM KPI Snapshots</h2>")
    emissions = kpis.get("emissions_by_year", {}) if isinstance(kpis, dict) else {}
    net_balance = kpis.get("net_energy_balance_by_year", {}) if isinstance(kpis, dict) else {}
    parts.append(_render_mapping_table("Emissions By Year", emissions if isinstance(emissions, dict) else {}))
    parts.append(_render_mapping_table("Net Energy Balance By Year", net_balance if isinstance(net_balance, dict) else {}))

    parts.append("<h2>Raw KPI JSON</h2>")
    safe_json = json.dumps(kpis if isinstance(kpis, dict) else {}, indent=2, sort_keys=True, default=str)
    parts.append(f"<code>{_escape(safe_json)}</code>")

    parts.append("</body></html>")
    output_path.write_text("\n".join(parts), encoding="utf-8")
    return output_path
