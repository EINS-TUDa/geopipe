# EnergySystemBuilder

`EnergySystemBuilder` assembles a fully-specified `EnergySystem` from a
topology, a `DataRegistry`, a list of `DemandType`s, an imports/exports
file, and an `EnergySystemBuilderConfig`. The builder uses fluent setters and
a final `.build()` that returns the assembled system, ready to be
passed to an optimisation backend.

## Logic
![logic_energy_system_builder.svg](..%2Flogic_energy_system_builder.svg)

## API

### Constructor

`EnergySystemBuilder(energy_system_name="Default")`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `energy_system_name` | `str` | `"Default"` | Identifier of the built `EnergySystem`; surfaces in reports and plots. |

### Setters

| Setter | Type | Required | Description |
|---|---|---|---|
| `set_system_topology` | `networkx.Graph` or `Topology` | yes | The graph returned by `TopologyBuilder.build().network`. |
| `set_data_registry` | `DataRegistry` | yes | Registry that resolves the demand values and technology shares. The topology must be built with the same registry (same CRS). |
| `set_demand_types` | `list[DemandType]` | yes | Overwrites the demand list. |
| `add_demand_types` | `*DemandType` | — | Appends additional demand types. |
| `set_imports_exports` | `str` or `Path` | yes | Path to `imports_exports.yaml` (see below). |
| `set_unit` | `UnitEnum` | yes | Energy unit of the built system (e.g. `UnitEnum.KW`). |
| `set_config` | `EnergySystemBuilderConfig` | no | Tuning knobs (see below). Defaults are used if omitted. |

`.build() -> EnergySystem` assembles and returns the configured system.

## Minimal usage

```python
from geopipe.energy_system import EnergySystemBuilder, EnergySystemBuilderConfig
from geopipe.energy_system.units import UnitEnum

builder = EnergySystemBuilder(energy_system_name="Case1")
builder.set_system_topology(topology_result.network)
builder.set_data_registry(data_reg)
builder.set_config(esb_cfg)
builder.set_demand_types([residential_heat_demand])
builder.set_imports_exports("input/imports_exports.yaml")
builder.set_unit(UnitEnum.KW)

energy_system = builder.build()
```

## EnergySystemBuilderConfig

A pydantic model that controls how the builder maps technologies onto
the topology. All fields are optional; sensible defaults make a config
unnecessary for the smallest cases.

```python
esb_cfg = EnergySystemBuilderConfig(
    minimum_decentral_technology_share={"heat_exchanger": 0.1},
    considered_connected_region_distance_m=50,
    central_tech_locations_per_commodity={"district_heat_in": [0]},
    central_tech_existing_capacities={
        "district_heat_in": {
            0:        [("cen_waste_heat_langnese", 0.6), ("cen_gas_boiler", 0.4)],
            "default":[("chp_gas", 0.8),                 ("cen_gas_boiler", 0.2)],
        }
    },
    additional_grid_capacity_factor={"heat_grid": 1.1},
)
```

| Field                                    | Meaning                                                                                                                                            |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `minimum_decentral_technology_share`     | `tech_name → float` in [0, 1]. Existing-capacity shares below this floor are zeroed (and a warning is logged).                                     |
| `considered_connected_region_distance_m` | Two regions are treated as a single connected cluster only if their nearest streets are within this distance. Default: unbounded.                  |
| `central_tech_locations_per_commodity`   | `commodity_in → [region_id, …]` — which regions may host central techs. If a connected cluster has no entry, the largest-grid region is used.      |
| `central_tech_existing_capacities`       | `commodity_in → {region_id │ "default" → [(tech, share), …]}`. Shares per region must sum to 1.0; `"default"` is the fallback.                     |
| `additional_grid_capacity_factor`        | `grid_name → factor` (e.g. `1.1` = +10 %). Multiplies the existing-capacity bound when grid capacity is propagated through a connected cluster.    |

## `imports_exports.yaml`

Defines exogenous commodity supplies (`imports`: purchased electricity,
gas, biomass, …) and sinks (`exports`: electricity sold back, …). The
`imports` key must be present and non-empty; the `exports` key is
optional and may be omitted entirely. Each is a list of one entry per
commodity:

```yaml
imports:
  - commodity_out: electricity
    price_eur_per_mwh: 80.0
    co2_emissions_ton_per_mwh:
      0: 0.5
      10: 0
  - commodity_out: biomass
    price_eur_per_mwh: 20.0
    max_cap_per_year: 100              # kW
    max_energy_out_per_year:           # MWh
      0: 500
      10: 1000

exports:                               # optional
  - commodity_in: electricity
    price_eur_per_mwh: -80.0           # negative = revenue per MWh exported
    co2_emissions_ton_per_mwh:
      0: -0.5                          # negative = avoided emissions credit
      10: 0
```

**Imports** (required) — required fields: `commodity_out`,
`price_eur_per_mwh`. Optional: `co2_emissions_ton_per_mwh`,
`max_cap_per_year`, `max_energy_out_per_year`.

**Exports** (optional) — required fields: `commodity_in`,
`price_eur_per_mwh` (use a negative value to model revenue). Optional:
`co2_emissions_ton_per_mwh`, `max_cap_per_year`,
`max_energy_in_per_year`.

Any numeric field accepts either a scalar or a `{relative_year: value}`
dict — keys are years counted from the scenario start (`0` = first
year).
