from __future__ import annotations

import argparse
from collections.abc import Mapping
import curses
import importlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any
from examples.example_api import render_topology_plot
from examples.example_registry import discover_examples, get_example_by_name, run_example
import matplotlib.pyplot as plt

def _plot_mix_results(
    plotter,
    results: dict,
    years: list[int],
    plots_dir: Path,
    *,
    demand_name: str | None = None,
    kind: str = "energy",
) -> None:
    mix_plot_paths = plotter.save_default_mix_plots(
        results,
        years=years,
        plots_dir=plots_dir,
        demand_name=demand_name,
        kind=kind,
    )
    technology_plot_path = mix_plot_paths["technology"]
    print(f"Saved technology plot: {technology_plot_path}")
    plt.show()
    plt.close("all")


def _show_cesm_sankey(project_root: Path, results: dict, years: list[int]) -> None:
    plotter_module = importlib.import_module("cesm.core.plotter")
    data_access_module = importlib.import_module("cesm.core.data_access")
    plotter_cls = getattr(plotter_module, "Plotter")
    dao_cls = getattr(data_access_module, "DAO")

    db_path = Path(results["db"])
    if not db_path.is_absolute():
        db_path = project_root / db_path
    print(f"Using DB: {db_path}")
    conn = sqlite3.connect(str(db_path))
    try:
        dao = dao_cls(conn)
        sankey_plotter = plotter_cls(dao)
        for year in years:
            sankey_fig = sankey_plotter.plot_sankey(year=year)
            sankey_fig.show()
    finally:
        conn.close()


def _render_menu(stdscr, entries, selected_idx: int) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    header = "Select an example with arrows and press Enter (q to quit)"
    stdscr.addnstr(0, 0, header, max(0, width - 1), curses.A_BOLD)

    row = 2
    for idx, entry in enumerate(entries):
        marker = ">" if idx == selected_idx else " "
        line = f"{marker} {entry.name:<12} {entry.description}"
        attr = curses.A_REVERSE if idx == selected_idx else curses.A_NORMAL
        if row < height:
            stdscr.addnstr(row, 0, line, max(0, width - 1), attr)
        row += 1
    stdscr.refresh()


def _select_entry_interactive(entries):
    if not entries:
        return None

    def _menu(stdscr):
        curses.curs_set(0)
        selected_idx = 0
        while True:
            _render_menu(stdscr, entries, selected_idx)
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                selected_idx = (selected_idx - 1) % len(entries)
            elif key in (curses.KEY_DOWN, ord("j")):
                selected_idx = (selected_idx + 1) % len(entries)
            elif key in (curses.KEY_ENTER, 10, 13):
                return entries[selected_idx]
            elif key in (ord("q"), 27):
                return None

    try:
        return curses.wrapper(_menu)
    except curses.error:
        return None


def _select_plot_menu(
    *,
    has_graph: bool,
    has_mix: bool,
    has_sankey: bool,
) -> str:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return "none"

    options = {
        "1": "all",
        "2": "graph",
        "3": "mix",
        "4": "sankey",
    }

    while True:
        print("Plot menu:")
        print("1. All plots")
        print("2. Graph plots")
        print("3. Mix plots")
        print("4. Sankey plots")
        raw = input("Choice [1-4, Enter to skip]: ").strip()
        if not raw:
            return "none"
        if raw not in options:
            print("Please enter 1, 2, 3, or 4.")
            continue

        choice = options[raw]
        if choice == "all":
            if not (has_graph or has_mix or has_sankey):
                print("No plot types are available for this run.")
                return "none"
            return choice
        if choice == "graph" and not has_graph:
            print("Graph plots are not available for this run.")
            continue
        if choice == "mix" and not has_mix:
            print("Mix plots are not available for this run.")
            continue
        if choice == "sankey" and not has_sankey:
            print("Sankey plots are not available for this run.")
            continue
        return choice


def _run_selected_plots(
    run_output: Mapping[str, Any],
    *,
    selection: str,
    has_graph: bool,
    has_mix: bool,
    has_sankey: bool,
) -> None:
    plotter = run_output.get("plotter")
    results_raw = run_output.get("results_raw")
    years = run_output.get("years")
    plots_dir = run_output.get("plots_dir")
    project_root = run_output.get("project_root")
    demand_name = run_output.get("demand_name")
    mix_plot_kind = str(run_output.get("mix_plot_kind", "energy"))

    if selection in {"all", "graph"} and has_graph:
        render_topology_plot(run_output)

    if selection in {"all", "mix"} and has_mix:
        out_dir = Path(plots_dir) if plots_dir is not None else Path.cwd()
        _plot_mix_results(
            plotter,
            results_raw,
            years,
            out_dir,
            demand_name=demand_name,
            kind=mix_plot_kind,
        )

    if selection in {"all", "sankey"} and has_sankey:
        _show_cesm_sankey(Path(project_root), results_raw, years)


def _resolve_report_path(run_output: Mapping[str, Any]) -> Path | None:
    report_value = run_output.get("results_report_html")
    if report_value is None:
        return None

    report_path = Path(report_value)
    if not report_path.is_absolute():
        project_root = run_output.get("project_root")
        if project_root is not None:
            report_path = Path(project_root) / report_path
    return report_path


def _open_path_in_default_app(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
        return
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    subprocess.run(["xdg-open", str(path)], check=False)


def _maybe_prompt_open_report(run_output: Any, *, mode: str = "ask") -> None:
    if not isinstance(run_output, Mapping):
        return

    report_path = _resolve_report_path(run_output)
    if report_path is None:
        return

    if not report_path.exists():
        print(f"CESM report not found: {report_path}")
        return

    if mode == "never":
        return

    if mode == "always":
        _open_path_in_default_app(report_path)
        return

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return

    answer = input(f"Open CESM report now? [y/N] ").strip().lower()
    if answer in {"y", "yes"}:
        _open_path_in_default_app(report_path)


def _select_report_open_mode_for_batch() -> str:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return "never"

    options = {
        "1": "never",
        "2": "ask",
        "3": "always",
    }

    while True:
        print("Report open mode for batch run:")
        print("1. Do not open reports")
        print("2. Ask for each report")
        print("3. Open all reports automatically")
        raw = input("Choice [1-3, Enter=1]: ").strip()
        if not raw:
            return "never"
        mode = options.get(raw)
        if mode is not None:
            return mode
        print("Please enter 1, 2, or 3.")


def _maybe_prompt_plots(run_output: Any) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return
    if not isinstance(run_output, Mapping):
        return

    plotter = run_output.get("plotter")
    streets_with_region = run_output.get("streets_with_region")
    results_raw = run_output.get("results_raw")
    years = run_output.get("years")
    project_root = run_output.get("project_root")

    required_graph_keys = (
        "plotter",
        "streets_with_region",
        "topology_plot_polygons",
        "region_id_column",
        "topology_plot_title",
        "topology_plot_filename",
        "plots_dir",
    )
    has_graph = all(key in run_output and run_output.get(key) is not None for key in required_graph_keys)
    has_mix = plotter is not None and results_raw is not None and years is not None
    has_sankey = has_mix and project_root is not None

    if not (has_graph or has_mix or has_sankey):
        return

    selection = _select_plot_menu(
        has_graph=has_graph,
        has_mix=has_mix,
        has_sankey=has_sankey,
    )
    if selection == "none":
        return

    _run_selected_plots(
        run_output,
        selection=selection,
        has_graph=has_graph,
        has_mix=has_mix,
        has_sankey=has_sankey,
    )


def _run_all_test_cases(entries) -> int:
    case_entries = [entry for entry in entries if getattr(entry, "is_test_case", False)]
    if len(case_entries) <= 1:
        print("run-all-test-cases needs more than one test case.")
        return 0

    report_mode = _select_report_open_mode_for_batch()
    failures = 0
    for entry in case_entries:
        print(f"Running {entry.name}...")
        try:
            run_output = run_example(entry)
            _maybe_prompt_open_report(run_output, mode=report_mode)
            _maybe_prompt_plots(run_output)
        except Exception as exc:
            failures += 1
            print(f"FAILED: {entry.name}: {exc}")
    if failures:
        print(f"Completed with {failures} failure(s).")
    else:
        print("All test cases completed.")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Run project examples and test-cases.")
    parser.add_argument("--list", action="store_true", help="List available examples and exit.")
    parser.add_argument("--run", type=str, help="Run a specific example by name.")
    parser.add_argument(
        "--run-all-test-cases",
        action="store_true",
        help="Run all discovered test cases in examples/test_cases.",
    )
    args = parser.parse_args()

    entries = discover_examples()
    if args.list:
        for entry in entries:
            print(f"{entry.name}: {entry.description}")
        return

    if args.run:
        entry = get_example_by_name(args.run)
        run_output = run_example(entry)
        _maybe_prompt_open_report(run_output)
        _maybe_prompt_plots(run_output)
        return

    if args.run_all_test_cases:
        failures = _run_all_test_cases(entries)
        if failures:
            raise SystemExit(1)
        return

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise SystemExit("Interactive mode requires a TTY. Use --run <example_name> or --run-all-test-cases.")

    selected = _select_entry_interactive(entries)
    if selected is None:
        print("No example selected.")
        return

    run_output = run_example(selected)
    _maybe_prompt_open_report(run_output)
    _maybe_prompt_plots(run_output)


if __name__ == "__main__":
    main()
