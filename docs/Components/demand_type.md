# DemandType

A `DemandType` describes **one demand commodity per region** — its
hourly load shape, where its annual total comes from (a *value source*),
and how the demand is split across decentralised supply technologies.
The `EnergySystemBuilder` consumes a list of `DemandType`s; one
`DemandType` typically covers a whole demand class (e.g. all residential
heat). A demand is built in a region only where its value source yields a
value there, so the same mechanism covers both system-wide demands and
localised "special" demands (e.g. a single swimming pool).

## Which technologies can supply a demand

A technology can supply a demand **if its `commodity_out` equals the
demand's `commodity_in`**. To add a special demand, give it its own
commodity and register technologies that produce it:

```yaml
decentralized:
  pool_heat_pump:      { commodity_in: electricity,       commodity_out: pool_heat, ... }
  heat_exchanger_pool: { commodity_in: district_heat_out, commodity_out: pool_heat, ... }  # district route
```

The optimizer then chooses among all commodity-matched candidates
(decentral units and, via shared grid commodities, the district route).
A demand may also be satisfied directly by an `Import` whose
`commodity_out` matches (e.g. electricity demand served from the grid).

## Value sources

The annual total per region is resolved by a `DemandValueSource`:

- **`ColumnDemandValue(column_name=...)`** — sums an (extensive) column of
  `streets_data` over the region's edges (the classic, data-driven case).
- **`ExplicitDemandValue(value_per_region={region_id: value})`** — explicit
  per-region values; the mapping keys also **scope** the demand to those
  regions. Use this for special demands whose value is known up front; a
  data-derived source can replace it later without changing the builder.

## Constructor

```python
from pathlib import Path
from geopipe.energy_system.demand import DemandType, ColumnDemandValue, ExplicitDemandValue
from geopipe.data.dataset import CensusTechnology

residential_heat = DemandType(
    name="residential_heat",
    commodity_in="residential_heat",
    cooperation_of_technologies=False,
    profile_path=Path("input/residential_heat.txt"),
    value_source=ColumnDemandValue(column_name="waerme_mwh"),
    technology_shares_query_params={
        "key": "heating_shares",
        "name_mapping": {
            CensusTechnology.Gas: "ind_gas_boiler",
            CensusTechnology.Oil: "ind_oil_boiler",
            CensusTechnology.Wood: "ind_biomass",
            CensusTechnology.Renewable: "ind_heat_pump",
            CensusTechnology.District_Heating: "heat_exchanger",
            CensusTechnology.Biomass: None,
            CensusTechnology.Electric: None,
            CensusTechnology.Coal: None,
            CensusTechnology.NoEnergyCarrier: None,
        },
    },
    default_decentral_supply_technology="ind_oil_boiler",
    decrease_percent_per_year=0,
)

# A special demand: known value, no census data, served by a fixed share mix.
pool_heat = DemandType(
    name="pool_heat",
    commodity_in="pool_heat",
    cooperation_of_technologies=False,
    profile_path=Path("input/pool_heat.txt"),
    value_source=ExplicitDemandValue(value_per_region={0: 800.0}),  # MWh/yr; also the scope
    technology_shares_query_params=None,                            # no census data
    default_decentral_supply_technology=[("pool_heat_pump", 0.6),
                                         ("pool_gas_boiler", 0.4)],
    decrease_percent_per_year=0,
)
```

| Argument                              | Meaning                                                                                                                                                  |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`                                | Identifier; appears in plots and report output.                                                                                                          |
| `commodity_in`                        | Commodity that satisfies the demand (must match a registered technology's `commodity_out`, or an import's).                                              |
| `cooperation_of_technologies`         | `True` → all decentralised techs share a common load shape; `False` → each tech sees its own profile.                                                    |
| `profile_path`                        | Path to a normalised hourly profile (see below).                                                                                                         |
| `value_source`                        | `ColumnDemandValue` (sum of a `streets_data` column) or `ExplicitDemandValue` (per-region values, which also scope the demand).                          |
| `technology_shares_query_params`      | Dict forwarded to `DataRegistry.query()` for per-tech shares (must include a `key`). `None` for demands without census data — see the default below.     |
| `default_decentral_supply_technology` | Existing-mix fallback when no census shares are available: a single tech name, or a `[(tech_name, share), ...]` mix whose shares sum to 1. **Required when `technology_shares_query_params` is `None` and the commodity is supplied by decentral technologies** (not needed for import-only demands). Validated at `.build()`. |
| `decrease_percent_per_year`           | Linear annual decline applied to demand (e.g. `1` = −1 %/yr).                                                                                            |

### `profile_path` file

Plain text, one space-separated row of **8760 floats** that **sum to
≈ 1**. The pipeline normalises and broadcasts the profile across years
internally, so absolute units don't matter — only the relative shape.

```
0.00011250 0.00012030 0.00012237 ... (8760 values total)
```

### `technology_shares_query_params`

The dict is passed through to whichever `Dataset` the registry resolves
to. Two keys matter at the user level:

- **`key`** — the registry key to query (e.g. `"heating_shares"`,
  matching `DataKeys.HEATING_SHARES`).
- **`name_mapping`** *(optional)* — a `CensusTechnology → tech_name`
  dict that converts census categories into the tech names you have
  registered. Mapping a category to `None` drops it; remaining shares
  are renormalised to sum to 1.

`CensusTechnology` is an enum (`Gas`, `Oil`, `Wood`, `Biomass`,
`Renewable`, `Electric`, `Coal`, `District_Heating`, `NoEnergyCarrier`)
matching the German census-2022 heating-type categories.
