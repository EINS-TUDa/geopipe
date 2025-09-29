JSON technology schema

This folder contains per-technology JSON files and the schema they must follow.

Minimal fields (all required unless noted):

- name: string
  - Unique technology name. Used as the conversion process name in CESM.
- commodity_in: string
  - Input commodity name (e.g. "Electricity", "Heat_D0").
- commodity_out: string
  - Output commodity name (e.g. "Heat", "Dummy").
- efficiency: number
  - Thermal/electrical conversion efficiency (unitless). For pure converters use 1.0.
- technical_lifetime: integer
  - Lifetime in years (used as technical_lifetime in CESM params).
- capex_cost_power: number
  - Capital expenditure per MW (EUR/MW).
- opex_cost_power: number (optional)
  - Fixed OPEX component per MW (EUR/MW).
- opex_cost_energy: number (optional)
  - Variable OPEX per MWh (EUR/MWh).

Optional fields
- availability_profile: string
  - Name of a TSS file (without extension) describing availability over the time series.

Examples
- See the JSON files in this folder for working examples.

Notes
- The writer canonicalizes commodity names for the CESM techmap (e.g. "electricity" -> "Electricity").
- Additional CESM parameters (cap_max, max_eout, output_profile, etc.) are applied by the writer as overrides
  when constructing conversion-subprocess rows; they are not required in the per-technology JSON files.
