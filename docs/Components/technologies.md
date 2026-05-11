# Registering technologies

Technologies are loaded from a single YAML file via
`register_technologies(...)`. The function parses the YAML and
populates the typed registries that `EnergySystemBuilder` reads from
later.

## API

`register_technologies(path, clear_registry=True) -> None`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `path` | `str` or `Path` | — (required) | Path to the technologies YAML file. |
| `clear_registry` | `bool` | `True` | If `True`, wipes all previously registered technology types before loading. Set to `False` to merge into an existing registry. |

```python
from pypeline.energy_system import register_technologies

register_technologies("input/technologies.yaml", clear_registry=True)
```

The YAML has **five top-level sections**, each mapping a technology
name to its parameter block. The section determines which `*Type`
class is built; fields not allowed for that section are rejected, and
`commodity_in` / `commodity_out` are always required.

| Section         | Class built          | Typical use                                                     |
| --------------- | -------------------- |-----------------------------------------------------------------|
| `decentralized` | `DecentralizedType`  | Per-building heat/electricity supply                            |
| `central`       | `CentralType`        | Plant feeding into a grid                                       |
| `chp`           | `CHPType`            | Subclass of central plant. Has co-generation (two outputs)      |
| `grids`         | `GridType`           | Distribution networks within one region. Fed by central plants. |
| `pipes`         | `PipeType`           | Inter-region transfer pipes                                     |

## Example YAML

```yaml
decentralized:
  ind_heat_pump:
    commodity_in: electricity
    commodity_out: residential_heat
    efficiency: 3.9
    technical_lifetime: 18
    opex_cost_energy: 0          # EUR/MWh
    opex_cost_power: 13          # EUR/kW
    capex_cost_power: 5000       # EUR/kW
    existing_capacity_phase_out_years: 20

central:
  cen_waste_heat_langnese:
    commodity_in: Dummy
    commodity_out: district_heat_in
    efficiency: 1
    technical_lifetime: 25
    opex_cost_energy: 10
    opex_cost_power: 1
    capex_cost_power: 440
    capex_cost_base: 260000      # EUR (fixed cost per unit)
    max_capacity_per_unit: 400   # kW per unit; or {0: 400, 5: 600}
    existing_capacity_retirement_years: 15
    constrain_location_to_streets:
      - DEHE04620001hKzc

chp:
  chp_gas:
    commodity_in: gas
    commodity_out: district_heat_in
    commodity_out_2: electricity
    efficiency: 0.4
    loss: 0.1
    technical_lifetime: 25
    opex_cost_power: 2
    capex_cost_power: 500
    capex_cost_base: 260000
    existing_capacity_retirement_years: 20

grids:
  heat_grid:
    commodity_in: district_heat_in
    commodity_out: district_heat_out
    efficiency: 0.86
    technical_lifetime: 40
    capex_per_km: 1000000        # EUR/km
    existing_capacity_retirement_years: 30

pipes:
  heat_pipe:
    commodity_in: district_heat_out
    commodity_out: district_heat_in
    efficiency: 0.96
    technical_lifetime: 40
    capex_per_km: 1000000
    distance_threshold_m: 50
    existing_capacity_retirement_years: 30
```

## Field reference

Common to all sections:

| Field                | Meaning                                                                  |
| -------------------- | ------------------------------------------------------------------------ |
| `commodity_in`       | Input commodity name (must match a balance bus). Use `Dummy` for "free". |
| `commodity_out`      | Output commodity name.                                                   |
| `efficiency`         | Output / input ratio (e.g. `3.9` for a heat pump's COP).                 |
| `technical_lifetime` | Years of useful life; drives salvage value at the horizon.               |
| `opex_cost_energy`   | Variable cost in EUR/MWh.                                                |
| `opex_cost_power`    | Fixed cost in EUR/kW/year.                                               |
| `capex_cost_power`   | Investment cost in EUR/kW.                                               |

Section-specific:

- **`central`** — adds `capex_cost_base` (EUR fixed per unit, see
  *NewlyInstalledUnits*), `max_capacity_per_unit` (kW per unit; scalar
  or `{relative_year: value}` dict), and
  `constrain_location_to_streets` (list of street IDs the tech may
  occupy).
- **`chp`** — adds `commodity_out_2` and `loss` (electric-output loss
  fraction).
- **`grids`** / **`pipes`** — replace `capex_cost_power` with
  `capex_per_km` (EUR/km). `pipes` additionally take
  `distance_threshold_m` (max pipe length).
- Existing-capacity decay is configured per section:
  `existing_capacity_phase_out_years` (linear, decentralised) or
  `existing_capacity_retirement_years` (abrupt, central/chp/grids/pipes).

Year-dependent values are written as
`{relative_year: value}` dicts where `0` is the scenario start year.
