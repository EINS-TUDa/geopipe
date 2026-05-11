# DemandType

A `DemandType` describes **one demand commodity per region** — its
hourly load shape, the column in `streets_data` that holds the annual
total, and how the demand is split across decentralised supply
technologies. The `EnergySystemBuilder` consumes a list of
`DemandType`s; one `DemandType` typically covers a whole demand class
(e.g. all residential heat) across all regions.

## Constructor

```python
from pathlib import Path
from pypeline.energy_system.demand import DemandType
from pypeline.data.dataset import CensusTechnology

residential_heat = DemandType(
    name="residential_heat",
    commodity_in="residential_heat",
    cooperation_of_technologies=False,
    profile_path=Path("input/residential_heat.txt"),
    demand_column_name="waerme_mwh",
    technology_shares_query_params={
        "key": "heating_shares",
        "name_mapping": {
            CensusTechnology.Gas:              "ind_gas_boiler",
            CensusTechnology.Oil:              "ind_oil_boiler",
            CensusTechnology.Wood:             "ind_biomass",
            CensusTechnology.Renewable:        "ind_heat_pump",
            CensusTechnology.District_Heating: "heat_exchanger",
            CensusTechnology.Biomass:          None,
            CensusTechnology.Electric:         None,
            CensusTechnology.Coal:             None,
            CensusTechnology.NoEnergyCarrier:  None,
        },
    },
    default_decentral_supply_technology="ind_oil_boiler",
    decrease_percent_per_year=0,
)
```

| Argument                              | Meaning                                                                                                                                                  |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`                                | Identifier; appears in plots and report output.                                                                                                          |
| `commodity_in`                        | Commodity that satisfies the demand (must match a registered technology's `commodity_out`).                                                              |
| `cooperation_of_technologies`         | `True` → all decentralised techs share a common load shape; `False` → each tech sees its own profile.                                                    |
| `profile_path`                        | Path to a normalised hourly profile (see below).                                                                                                         |
| `demand_column_name`                  | Column in the `streets_data` GeoDataFrame that holds the **annual demand per street** (e.g. `waerme_mwh` in kWh).                                        |
| `technology_shares_query_params`      | Dict forwarded to `DataRegistry.query()` to obtain the share of each tech in a region. Must include a `key` (e.g. `"heating_shares"`).                   |
| `default_decentral_supply_technology` | Tech to fall back on when the data-registry query returns no usable shares.                                                                              |
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
