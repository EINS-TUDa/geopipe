# Optimization

An `OptimizationBackend` solves an `EnergySystem` for a `Scenario` and
returns a `Solution`.

```python
from geopipe.energy_system import Scenario
from geopipe.optimization import CESMOptimizationBackend

scenario = Scenario(name="Base", start_year=2025, end_year=2045, year_gap=5,
                    dt_hours=1, tss="8760h", co2_limit={2045: 0})
backend = CESMOptimizationBackend(timeseries_dir="input", output_dir="output")
solution = backend.solve(energy_system, scenario, mip_gap=0.01)

solution.save("output")
solution.write_html_report("output/report.html")
```

## Scenario

Dataclass in `geopipe.energy_system`.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | — | Scenario name, used in output file names. |
| `start_year`, `end_year` | `int` | — | First and last modelled year. |
| `year_gap` | `int` | — | Years between modelled years. `scenario.years` lists them. |
| `dt_hours` | `int` | — | Time-step length in hours. |
| `tss` | `str` | — | Time-series set, e.g. `"8760h"` for `8760h.txt` in the backend's `timeseries_dir`. |
| `co2_price` | `float \| dict[int, float] \| None` | `None` | CO₂ price, constant or per year. |
| `co2_limit` | `float \| dict[int, float] \| None` | `None` | CO₂ limit, constant or per year (`None` = no limit). |
| `discount_rate` | `float` | `0.05` | Discount rate. |

## OptimizationBackend

Abstract base class in `geopipe.optimization`. Every backend implements
`solve(energy_system, scenario, mip_gap=None) -> Solution`. Backends are
imported lazily; if a backend's extra is missing, importing it raises an
`ImportError` with the install command.

### CESMOptimizationBackend

Runs [CESM](https://github.com/EINS-TUDa/CESM) with Gurobi. Requires the
`cesm` extra (see [Installation](../installation.md#cesm-backend)).

| Method | Arguments | Description |
|---|---|---|
| constructor | `timeseries_dir`, `output_dir` | Directories for static time series (time-step sets, central technology profiles) and for results (created if missing). |
| `solve` | `energy_system`, `scenario`, `mip_gap=None`, `lp_file=False` | `mip_gap`: relative MIP optimality gap (e.g. `0.01` = 1 %; `None` = solver default). `lp_file=True` writes the model `.lp` for debugging. |

`solve` writes `<system>_<scenario>.xlsx` (CESM input) and
`<system>_<scenario>.sqlite` (raw results) to `output_dir`. If no optimal
or suboptimal solution is found, it raises `RuntimeError`; for an
infeasible model it also writes `model_iis.ilp`.

CESM reads all time series from one folder, so `solve` clears
`output_dir/profiles/` and writes each distinct demand profile there
once: as `<demand>.txt` if it is the same in all regions, otherwise as
`<demand>_<region id>.txt`. A profile shared by several demands keeps
the first name. The static time series the model references are copied
there too.

## Solution

Returned by `solve()`. Holds `energy_system`, `scenario` and `results`
(`Results`: costs, emissions per year and one DataFrame per technology
group, all in the energy system's unit).

| Method | Arguments | Description |
|---|---|---|
| `save` | `path`, `file_name=None` | Pickle the solution (default name `<system>_<scenario>_Solution.pkl`). |
| `Solution.load` | `path`, `file_name` | Load a pickled solution. |
| `write_html_report` | `output_path` | Write an HTML report. |
| `plot_grid` | `grid_name`, `year`, `metric="capacity"`, `output_path=None` | Map of a grid; `metric`: `capacity`, `energy_output`. |
| `plot_decentral_shares` | `demand_name`, `year`, `metric="energy_output"`, `technology_style=None`, `output_path=None` | Decentral technology shares per region; `year` may be a list; `metric`: `energy_output`, `new_capacity`, `active_capacity`. |
