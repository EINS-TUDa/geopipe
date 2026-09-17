# DemandType

A `DemandType` describes **one demand commodity per region** — its
hourly load shape, where its annual total comes from (a *data source*),
and how the demand is split across decentralised supply technologies.
The `EnergySystemBuilder` consumes a list of `DemandType`s; one
`DemandType` typically covers a whole demand class (e.g. all residential
heat). A demand is built in a region only where its value is non-zero
there, so the same mechanism covers both system-wide demands and
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

## Data sources

`value`, `profile` and `technology_shares` are `DataRegistryQuery`s, answered per
region by the `DataRegistry` (priorities, scopes, routing — see
[DataRegistry](data_registry.md)). A special demand registers its own
dataset under its own key. The value is converted from the dataset's
`unit` to the energy unit of the energy system (MWh for `UnitEnum.KW`);
a dataset without a unit is rejected.

Street-keyed values (`StreetValueDataset`) refer to the street ids of
the raw input streets. A region's value is the length-weighted sum over
its streets, so the same input works for any region definition.

## Constructor

```python
from geopipe.data import DataKeys, DataRegistryQuery, StreetValueDataset
from geopipe.data.dataset import CensusTechnology
from geopipe.energy_system.demand import DemandType
from geopipe.energy_system.units import UnitEnum

residential_heat = DemandType(
    name="residential_heat",
    commodity_in="residential_heat",
    cooperation_of_technologies=False,
    profile=DataRegistryQuery(key=DataKeys.RESIDENTIAL_HEAT_DEMAND_PROFILE),
    value=DataRegistryQuery(key=DataKeys.RESIDENTIAL_HEAT_DEMAND),
    technology_shares=DataRegistryQuery(
        key=DataKeys.HEATING_SHARES,
        params={"name_mapping": {
            CensusTechnology.Gas: "ind_gas_boiler",
            CensusTechnology.Oil: "ind_oil_boiler",
            CensusTechnology.Wood: "ind_biomass",
            CensusTechnology.Renewable: "ind_heat_pump",
            CensusTechnology.District_Heating: "heat_exchanger",
            CensusTechnology.Biomass: None,
            CensusTechnology.Electric: None,
            CensusTechnology.Coal: None,
            CensusTechnology.NoEnergyCarrier: None,
        }},
    ),
    default_decentral_supply_technology="ind_oil_boiler",
    decrease_percent_per_year=0,
)

# A special demand: known value on one street, no census data, served by a fixed share mix.
data_reg.register(StreetValueDataset(values={"1173": 800.0}, keys=["pool_heat_demand"], unit=UnitEnum.MWH))

pool_heat = DemandType(
    name="pool_heat",
    commodity_in="pool_heat",
    cooperation_of_technologies=False,
    profile=DataRegistryQuery(key="pool_heat_demand_profile"),
    value=DataRegistryQuery(key="pool_heat_demand"),  # only in the region of street 1173
    technology_shares=None,                            # no census data
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
| `profile`                             | `DataRegistryQuery` for the hourly profile per region (see below).                                                                                       |
| `value`                               | `DataRegistryQuery` for the annual demand per region (see [Data sources](#data-sources)). Zero or `None` = no demand in that region.                     |
| `technology_shares`                   | `DataRegistryQuery` for per-tech shares per region. `None` for demands without such data — see the default below.                                        |
| `default_decentral_supply_technology` | Existing-mix fallback when no census shares are available: a single tech name, or a `[(tech_name, share), ...]` mix whose shares sum to 1. **Required when `technology_shares` is `None` and the commodity is supplied by decentral technologies** (not needed for import-only demands). Validated at `.build()`. |
| `decrease_percent_per_year`           | Linear annual decline applied to demand (e.g. `1` = −1 %/yr).                                                                                            |

### `profile`

Must return a `pd.Series` of **8760 hourly values** per region. The
pipeline normalises it, so absolute units don't matter — only the
relative shape. A profile file (e.g. one space-separated row of 8760
floats) is registered as a `CSVDataset`; one file can serve several
demands through several keys:

```python
data_reg.register(CSVDataset(
    keys=[DataKeys.RESIDENTIAL_HEAT_DEMAND_PROFILE, "pool_heat_demand_profile"],
    file_path="input/heat_demand_profile.txt",
    pandas_kwargs={"sep": r"\s+", "header": None}))
```

### `technology_shares`

Resolved like `value`, without unit conversion. For the census example,
the query's `params` carry:

- **`name_mapping`** *(optional)* — a `CensusTechnology → tech_name`
  dict that converts census categories into the tech names you have
  registered. Mapping a category to `None` drops it; remaining shares
  are renormalised to sum to 1.

`CensusTechnology` is an enum (`Gas`, `Oil`, `Wood`, `Biomass`,
`Renewable`, `Electric`, `Coal`, `District_Heating`, `NoEnergyCarrier`)
matching the German census-2022 heating-type categories.
