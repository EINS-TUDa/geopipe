![img_1.png](img_1.png)
# Welcome

**GeoPipe — *Geo*graphical energy system modeling data *pipe*line**

GeoPipe is an automated, open-source data processing pipeline that transforms geospatial data on energy demand and supply and 
techno-economic data into a structured energy system representation. The structured energy system representation
can be passed to modern multi investment energy system models via lightweight Python interfaces. 

![idea.png](idea.png)

## Structured Representation of Energy System

![abstraction.png](abstraction.png)

## How the pieces fit together

```mermaid
flowchart TD
    streets["Street network<br/>+ region definition"] --> tb["TopologyBuilder"]
    tb -- "network" --> esb["EnergySystemBuilder"]
    subgraph inputs["Energy system inputs"]
        direction TB
        techs["Technologies"] ~~~ demands["DemandTypes"] ~~~ ie["Imports/Exports"]
    end
    inputs --> esb
    datasets["Datasets<br/>(file, PostgreSQL)"] --> dr["DataRegistry"]
    dr -- "heating_shares" --> esb
    esb -- "EnergySystem" --> ob["OptimizationBackend"]
    scenario["Scenario<br/>+ time series"] --> ob
    ob -- "Solution" --> out["Results · Report · Plots"]

    classDef registry fill:#fff3cd,stroke:#d4a017,stroke-width:2px,color:#000
    class dr registry

    click streets href "Components/modify_streets_data/"
    click tb href "Components/topology_builder/"
    click techs href "Components/technologies/"
    click demands href "Components/demand_type/"
    click ie href "Components/energy_system_builder/#imports_exportsyaml"
    click esb href "Components/energy_system_builder/"
    click datasets href "Components/data_registry/"
    click dr href "Components/data_registry/"
    click ob href "Components/optimization/#optimizationbackend"
    click scenario href "Components/optimization/#scenario"
    click out href "Components/optimization/#solution"
```

See
[`examples/test_case_bensheim/bensheim_test_main.py`](https://github.com/EINS-TUDa/geopipe/blob/main/examples/test_case_bensheim/bensheim_test_main.py)
for an end-to-end reference.

## Where to start

- New to the project? [Install GeoPipe](installation.md),
  then follow the diagram top-to-bottom — each box is one step in the
  example script.
- Found a bug or want to contribute? Open an issue or pull request on
  [GitHub](https://github.com/EINS-TUDa/geopipe).

