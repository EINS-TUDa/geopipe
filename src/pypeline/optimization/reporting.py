"""Backend-agnostic HTML report writer for an optimization Solution."""
from __future__ import annotations

import html
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import pandas as pd

from ..energy_system.units import Unit

if TYPE_CHECKING:
    from .solver import Solution


def _column_unit_renames(unit: Unit) -> dict[str, str]:
    return {
        "capacity": f"capacity [{unit.power}]",
        "new_capacity": f"new_capacity [{unit.power}]",
        "energy_output": f"energy_output [{unit.energy}]",
        "amount": f"amount [{unit.co2_emissions}]",
    }


def _escape(value: Any) -> str:
    return html.escape(str(value))


def _with_units(df: pd.DataFrame, renames: dict[str, str]) -> pd.DataFrame:
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()
    rename = {col: renames[col] for col in df.columns if col in renames}
    return df.rename(columns=rename) if rename else df


def _render_df(title: str, df: pd.DataFrame, *, max_rows: int = 1000) -> str:
    if df is None or df.empty:
        return f"<h3>{_escape(title)}</h3><p><em>No rows</em></p>"
    clipped = df.head(max_rows)
    table_html = clipped.to_html(index=False, classes="tbl", border=0, escape=True)
    suffix = "" if len(df) <= max_rows else f"<p><em>Showing first {max_rows} of {len(df)} rows.</em></p>"
    return f"<h3>{_escape(title)}</h3><div class='tbl-wrap'>{table_html}</div>{suffix}"


def _render_mapping_table(title: str, values: dict[str, Any]) -> str:
    if not values:
        return f"<h3>{_escape(title)}</h3><p><em>No data</em></p>"
    rows = ["<table><tr><th>Key</th><th>Value</th></tr>"]
    for key, value in values.items():
        rows.append(f"<tr><td>{_escape(key)}</td><td>{_escape(value)}</td></tr>")
    rows.append("</table>")
    return f"<h3>{_escape(title)}</h3>" + "\n".join(rows)


def _render_card_grid(grid_class: str, *cards: str) -> str:
    rendered = "".join(f"<section class='card'>{card}</section>" for card in cards if card)
    if not rendered:
        return ""
    return f"<div class='{grid_class}'>{rendered}</div>"


def _render_group_section(
    summary: str,
    *,
    active: dict[str, pd.DataFrame],
    energy: dict[str, pd.DataFrame],
    new: dict[str, pd.DataFrame],
    key_label: str,
    renames: dict[str, str],
) -> str:
    keys = sorted(set(active) | set(energy) | set(new))
    if not keys:
        return (
            f"<details><summary>{_escape(summary)}</summary>"
            f"<p><em>No technologies in this group.</em></p></details>"
        )

    parts = [f"<details open><summary>{_escape(summary)}</summary>"]
    for key in keys:
        parts.append(f"<h3>{_escape(key_label)}: {_escape(key)}</h3>")
        parts.append(
            _render_card_grid(
                "grid-3",
                _render_df("Active capacity", _with_units(active.get(key, pd.DataFrame()), renames)),
                _render_df("Yearly energy output", _with_units(energy.get(key, pd.DataFrame()), renames)),
                _render_df("New capacity", _with_units(new.get(key, pd.DataFrame()), renames)),
            )
        )
    parts.append("</details>")
    return "\n".join(parts)


_STYLE = """
body { font-family: 'Segoe UI', Arial, sans-serif; margin: 2em; background: #fafafa; color: #1a1a1a; }
h1, h2, h3 { margin-top: 1.2em; }
h2 { border-bottom: 2px solid #ddd; padding-bottom: 0.2em; }
table { border-collapse: collapse; margin: 0.4em 0 0.8em; font-size: 0.92em; background: #fff; width: 100%; }
th, td { border: 1px solid #ccc; padding: 4px 8px; text-align: left; vertical-align: top; }
th { background: #efefef; }
.grid-2 { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); margin: 0.5em 0 1em; }
.grid-3 { display: grid; gap: 1rem; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 0.5em 0 1em; }
@media (max-width: 1100px) { .grid-3 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 760px) { .grid-3 { grid-template-columns: 1fr; } }
.card { background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 0.7em 0.9em; }
.card h3 { margin-top: 0.2em; }
.tbl-wrap { overflow-x: auto; }
details { background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 0.5em 0.9em; margin: 0.8em 0 1em; }
summary { cursor: pointer; font-weight: 600; }
"""


def _run_metadata(solution: "Solution") -> dict[str, Any]:
    md: dict[str, Any] = {}
    if solution.energy_system is not None:
        md["energy_system"] = getattr(solution.energy_system, "name", "")
    if solution.scenario is not None:
        scenario = solution.scenario
        md["scenario"] = getattr(scenario, "name", "")
        for attr in ("start_year", "end_year", "year_gap", "dt_hours", "tss"):
            if hasattr(scenario, attr):
                md[attr] = getattr(scenario, attr)
    return md


def write_html_report(
    solution: "Solution",
    output_path: Path,
) -> Path:
    """Render ``solution`` to a self-contained HTML report at ``output_path``."""
    if solution.results is None:
        raise ValueError("Cannot write report: solution.results is None.")

    results = solution.results
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_title = "Optimization Results"

    unit = results.unit
    renames = _column_unit_renames(unit)

    costs = {
        f"opex [{unit.money}]": results.opex,
        f"capex [{unit.money}]": results.capex,
        f"totex [{unit.money}]": results.totex,
    }

    run_metadata: dict[str, Any] = _run_metadata(solution)

    parts: list[str] = [
        "<!DOCTYPE html>",
        "<html lang='en'>",
        "<head><meta charset='utf-8'>",
        f"<title>{_escape(report_title)}</title>",
        f"<style>{_STYLE}</style>",
        "</head><body>",
        f"<h1>{_escape(report_title)}</h1>",
        "<h2>Run Overview</h2>",
        _render_card_grid(
            "grid-2",
            _render_mapping_table("Run Metadata", run_metadata),
            _render_mapping_table("System Costs", costs),
            _render_df("Emissions by Year", _with_units(results.emissions_by_year, renames)),
        ),
        "<h2>Decentral Technologies (per demand)</h2>",
        _render_group_section(
            "Decentral Technologies",
            active=results.active_capacities_decentral_technologies_per_demand,
            energy=results.yearly_energy_outputs_decentral_technologies_per_demand,
            new=results.new_capacities_decentral_technologies_per_demand,
            key_label="Demand",
            renames=renames,
        ),
        "<h2>Central Technologies (per commodity_out)</h2>",
        _render_group_section(
            "Central Technologies",
            active=results.active_capacities_central_technologies_per_commodity_out,
            energy=results.yearly_energy_outputs_central_technologies_per_commodity_out,
            new=results.new_capacities_central_technologies_per_commodity_out,
            key_label="Commodity Out",
            renames=renames,
        ),
        "<h2>Grids (per commodity_in)</h2>",
        _render_group_section(
            "Grids",
            active=results.active_capacities_grids_per_commodity_in,
            energy=results.yearly_energy_outputs_grids_per_commodity_in,
            new=results.new_capacities_grids_per_commodity_in,
            key_label="Commodity In",
            renames=renames,
        ),
        "<h2>Pipes (per commodity_out)</h2>",
        _render_group_section(
            "Pipes",
            active=results.active_capacities_pipes_per_commodity_out,
            energy=results.yearly_energy_outputs_pipes_per_commodity_out,
            new=results.new_capacities_pipes_per_commodity_out,
            key_label="Commodity Out",
            renames=renames,
        ),
        "</body></html>",
    ]

    output_path.write_text("\n".join(parts), encoding="utf-8")
    webbrowser.open(output_path.resolve().as_uri())
    return output_path