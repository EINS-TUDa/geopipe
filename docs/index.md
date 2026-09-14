![img_1.png](img_1.png)
# Welcome

**GeoPipe — *Geo*graphical energy system modeling data *pipe*line**

GeoPipe is an automated, open-source data processing pipeline that transforms geospatial data on energy demand and supply and 
techno-economic data into a structured energy system representation. The structured energy system representation
can be passed to modern multi investment energy system models via lightweight Python interfaces. 

![idea.png](idea.png)

## Structured Representation of Energy System

![abstraction.png](abstraction.png)

## Concept

```mermaid
flowchart TD
    streets["Street network"] -- "register_streets" --> dr["DataRegistry"]
    datasets["Datasets"] -- "register" --> dr
    dr -- "streets" --> tb["TopologyBuilder"]
    tb -- "topology" --> esb["EnergySystemBuilder"]
    subgraph inputs["Energy system inputs"]
        direction TB
        techs["Technologies"] --- demands["DemandTypes"] --- ie["Commodity<br/>data"]
    end
    inputs --> esb
    dr -- "demand annual values, demand time profiles, technology shares" --> esb
    esb -- "EnergySystem" --> ob["OptimizationBackend"]
    scenario["Scenario"] --> ob
    ob -- "Solution" --> out["Results, Report, Plots"]

    linkStyle 4,5 stroke:none,stroke-width:0px

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

