# Welcome

**GeoPipe** — *GEOgraphical energy system modelling data PIPEline*

GeoPipe is an automated, open-source data processing pipeline that transforms geospatial data on energy demand and supply and 
techno-economic data into a structured energy system representation. The structured energy system representation
can be passed to modern multi investment energy system models via lightweight Python interfaces. 

## Idea
![idea.png](idea.png)

## Structured Representation of Energy System
Per Region:
![abstraction.png](abstraction.png)

## How the pieces fit together

```
        streets.geojson                technologies.yaml                  
              │                               │
              ▼                               ▼
      ┌─────────────────┐            register_technologies()
      │ TopologyBuilder │                     │
      │   (Simple       │                     │   (loads the
      │   Polygon)      │                     │    technology catalog)
      └────────┬────────┘                     │
               │ network (nx.Graph)           │
               │                              │
               ▼                              │
   ┌──────────────────────────────────────┐   │
   │       EnergySystemBuilder            │◀──┘
   │                                      │
   │   ◀── set_demand_types([DemandType]) │◀── residential_heat.txt
   │   ◀── set_data_registry(DataRegistry)│◀── heating_shares query
   │   ◀── set_config(ESBConfig)          │
   │   ◀── set_imports_exports(...)       │
   │   ◀── set_unit(UnitEnum.KW)          │
   └────────────────┬─────────────────────┘
                    │ EnergySystem
                    ▼
   ┌──────────────────────────────────────┐
   │        OptimizationBackend           │◀── Scenario(years, co2_limit, …)
   │      (CESMOptimizationBackend)       │
   └────────────────┬─────────────────────┘
                    │ Solution
                    ▼
        .save(...) · .write_html_report(...)
        .plot_grid(...) · .plot_decentral_shares(...)
```

Each arrow corresponds to a single Python call in your run script
(see `examples/test_case_bensheim/bensheim_test_main.py` for an
end-to-end reference).

## User-facing interfaces

These are the only names you need to know to build your own case
study. Each links to a dedicated page with usage, constructor
arguments, and the input-file format.

| Stage           | Interface                                     | What it does                                                                    |
| --------------- | --------------------------------------------- | ------------------------------------------------------------------------------- |
| Topology        | [`SimpleTopologyBuilder`][tb] / [`PolygonTopologyBuilder`][tb] | Build a region graph from a streets GeoJSON.                |
| Topology        | [`modify_streets_data`][tb]                   | Patch street attributes from `modifications.yaml` before building.              |
| Technologies    | [`register_technologies`][tech]               | Load `technologies.yaml` into the typed technology registries.                  |
| Demand          | [`DemandType`][dem]                           | Bind a demand profile + tech-share query to one commodity.                      |
| Demand          | [`CensusTechnology`][dem]                     | Enum used in `name_mapping` for census-driven shares.                           |
| Data            | [`DataRegistry`][dr]                          | Lookup point for regionalised data (currently used for `heating_shares` only).  |
| Data            | [`FileDataset`][dr] / [`PostgreSQLDataset`][dr] | Concrete data sources you register with the `DataRegistry`.                   |
| Build           | [`EnergySystemBuilderConfig`][esb]            | Tuning knobs for region connectivity, central-tech placement, capacity shares. |
| Build           | [`EnergySystemBuilder`][esb]                  | Fluent builder; `.build()` returns the `EnergySystem`.                          |
| Build           | [`UnitEnum`][esb]                             | Energy-unit selector for the built system (e.g. `UnitEnum.KW`).                 |
| Solve           | `Scenario`                                    | Years, time-step, time-series-set, CO₂ limit, year gap.                         |
| Solve           | `CESMOptimizationBackend`                     | Wraps CESM/Gurobi. `.solve(energy_system, scenario, mip_gap=None, lp_file=False)` → `Solution`. `mip_gap` sets the relative MIP optimality gap (e.g. `0.01` = 1 %; `None` uses the solver default); `lp_file=True` writes the model `.lp` for debugging. |
| Solve           | `Solution`                                    | `.save`, `.write_html_report`, `.plot_grid`, `.plot_decentral_shares`, …        |

[tb]:   Components/topology_builder.md
[tech]: Components/technologies.md
[dem]:  Components/demand_type.md
[dr]:   Components/data_registry.md
[esb]:  Components/energy_system_builder.md

## Where to start

- New to the project? Read [Getting started](getting_started.md), then
  walk the table above top-to-bottom — each row is one step in the
  example script.

