# Data sources and licences

This file documents third-party input data in `/input_data` used for this example. Each dataset keeps
its own license. The license of this repository's source code does not apply to
them.

## Zensus 2022 — heating energy carrier, 100 m grid

**File:** `input_data/Census2022HeatingType100mGrid.geojson`

- **Provider:** Statistische Ämter des Bundes und der Länder, Zensus 2022 —
  Gebäude mit Wohnraum nach Energieträger der Heizung, 100m-Gitterzellen
- **Dataset (URI):** <https://www.destatis.de/DE/Themen/Gesellschaft-Umwelt/Bevoelkerung/Zensus2022/_publikationen.html#1404032>
  (downloaded on `26.08.2026`)
- **Licence:** Datenlizenz Deutschland – Namensnennung – Version 2.0
  (`dl-de/by-2-0`), <https://www.govdata.de/dl-de/by-2-0>
- **Modified:** the data has been changed,e.g.,
  - the 100 m grid cells of the original CSV were converted into polygons
    (EPSG:3035),
  - the dataset was clipped to southern Hesse,
  - the energy-carrier columns were renamed.

## Hesse heat atlas - heat demand mapped to street segments

Download it from [Hesse heat atlas](https://www.waermeatlas-hessen.de).

## Technology data — KWW-Technikkatalog Wärmeplanung
**File:** `/input_data/technologies.yaml`

The techno-economic parameters of the heat supply technologies (efficiency,
technical lifetime, CAPEX and OPEX) are derived from the KWW-Technikkatalog
Wärmeplanung.

- **Author:** Deutsche Energie-Agentur (dena)
- **Rights holder:** Bundesministerium für Wirtschaft und Energie (BMWE), which
  holds the exclusive and unrestricted rights of use in the catalogue
- **Original publication:** Deutsche Energie-Agentur (dena, 2026):
  KWW-Technikkatalog Wärmeplanung. Version 1.1. Berlin.
- **Version:** 1.1, August 2026
- **Source (URI):** <https://www.kww-halle.de/angebote/infothek/detail/kww-technikkatalog-waermeplanung-begleitdokument>
- **Licence:** Creative Commons Attribution 4.0 International (CC BY 4.0),
  <https://creativecommons.org/licenses/by/4.0/>
- **Modified:** the data has been adapted
  - parameter values were extracted from the catalogue and restructured into the
    YAML schema used by this pipeline,
  - technologies were renamed to this model's technology identifiers
    (e.g. `ind_heat_pump`, `ind_gas_boiler`) 
  - values were converted to the units required by the model and, where the
    model needs a single figure, selected or aggregated from the catalogue's
    ranges and reference cases.


## Waste heat data - Plattform für Abwärme
The availability of waste heat sources in this example is derived from the Plattform für Abwärme (PfA). 

- **Publisher:** Bundesamt für Wirtschaft und Ausfuhrkontrolle (BAFA),
  Bundesstelle für Energieeffizienz (BfEE), Frankfurter Str. 29–35,
  65760 Eschborn, <pfa@bafa.bund.de>
- **Source (URI):** <http://www.bfee-online.de/pfa>
- **Version:** 1.2, as of 05.07.2026
- **Licence:** Creative Commons Attribution-NonCommercial 4.0 International
  (CC BY-NC 4.0), <https://creativecommons.org/licenses/by-nc/4.0/> —
  **non-commercial use only**
