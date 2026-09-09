# README

## GeoPipe

**Geo**graphical energy system modeling data **pipe**line

**_Note:_** This repo currently is under development and the API is not stable yet. Please contact us if you want to use it in your research.

## Installation
### Using CESM 
Using the [Compact Energy System Modeling Tool](https://github.com/EINS-TUDa/CESM) as backend for the energy system optimization. Requires Gurobi.
```bash
pip install geopipe[cesm]
pip install git+https://github.com/EINS-TUDa/CESM.git@578e0bb
```


### Using PyPSA
Using [PyPSA](https://github.com/PyPSA/PyPSA) as backend for the energy system optimization.
```bash
tbd
```

## What `geopipe` does
`geopipe` is an automated, open-source data processing pipeline that transforms geospatial data on energy demand and supply and techno-economic data into a structured energy system representation. The structured energy system representation can be passed to modern multi investment energy system models via lightweight Python interfaces. Detailed documentation can be found [here](https://eins-tuda.github.io/geopipe).

Setting up an energy system could look as follows:

```python
from pathlib import Path

from geopipe import DataRegistry, EnergySystemBuilder, EnergySystemBuilderConfig
from geopipe.energy_system import Scenario, register_technologies
from geopipe.energy_system.demand import ColumnDemandValue, DemandType
from geopipe.energy_system.units import UnitEnum
from geopipe.optimization import CESMOptimizationBackend
from geopipe.topology_builder.topology_builder import PolygonTopologyBuilder

CASE_DIR = Path("my_case")

# 1) Topology: turn a streets GeoJSON into a graph of regions.
#    Each street segment is assigned to the polygon of `polygons.geojson` that
#    contains it; the polygon's `id` column becomes the region id.
topology_builder = PolygonTopologyBuilder()
topology_builder.set_streets_data(CASE_DIR / "streets_heat_demand.geojson", id_column="fid")
topology_builder.set_polygons_data(CASE_DIR / "polygons.geojson")
topology_builder.set_region_id_column("id")
topology_builder.set_extensive_columns(["heat_demand_mwh"])  # distributed over the street segments
topology_result = topology_builder.build()

# 2) Technologies: load the techno-economic catalog (boilers, heat pumps, grids, pipes, ...).
register_technologies(CASE_DIR / "technologies.yaml", clear_registry=True)

# 3) Demand: one demand type per commodity, with an hourly profile and an annual value.
residential_heat = DemandType(
    name="residential_heat",
    commodity_in="residential_heat",
    profile_path=CASE_DIR / "residential_heat.txt",       # normalized hourly profile
    value_source=ColumnDemandValue(column_name="heat_demand_mwh"),
    cooperation_of_technologies=False,
    default_decentral_supply_technology="ind_gas_boiler",  # existing supply mix
    decrease_percent_per_year=0,
)

# 4) Energy system: combine topology, data, demands and imports/exports.
config = EnergySystemBuilderConfig(
    additional_grid_capacity_factor={"heat_grid": 1.1},
    central_tech_locations_per_commodity={"district_heat_in": [0]},
)

builder = EnergySystemBuilder(energy_system_name="MyCase")
builder.set_system_topology(topology_result.network)
builder.set_data_registry(DataRegistry())            # add datasets for census-based technology shares
builder.set_config(config)
builder.set_demand_types([residential_heat])
builder.set_imports_exports(CASE_DIR / "imports_exports.yaml")
builder.set_unit(UnitEnum.KW)

energy_system = builder.build()

# 5) Optimization: solve the system for a scenario and report the results.
scenario = Scenario(name="Base", start_year=2025, end_year=2045, year_gap=5,
                    dt_hours=1, tss="8760h", co2_limit={2045: 0})
backend = CESMOptimizationBackend(timeseries_dir=CASE_DIR, output_dir=CASE_DIR / "output_data")
solution = backend.solve(energy_system, scenario)

solution.save(path=CASE_DIR / "output_data")
solution.write_html_report(output_path=CASE_DIR / "output_data" / "report.html")
```

A complete, runnable case study is in [`examples/`](examples/).


## Contribution
Clone the repo and run
```bash
uv sync
```
to set up the development environment.

## Contact
Carolin Ayasse, [carolin.ayasse@eins.tu-darmstadt.de](mailto:carolin.ayasse@eins.tu-darmstadt.de)

[Energy Information Networks and Systems (EINS)](https://www.eins.tu-darmstadt.de) at Technical University of Darmstadt, Germany


