import pathlib

import pandas as pd
import geopandas as gpd

from geopipe import DataRegistry
from geopipe.data.data_registry import DataKeys, DataRegistryQuery
from geopipe.data.dataset import Dataset, FileDataset, CSVDataset, CensusTechnology, StreetValueDataset
from geopipe.energy_system.units import UnitEnum
from geopipe.topology_builder.topology import Topology


def census_query(dataset: Dataset, topology: Topology, query: DataRegistryQuery) -> dict[CensusTechnology, float]:
    if query.key != DataKeys.HEATING_SHARES:
        raise ValueError("census_bensheim_query only supports 'heating_shares' key")

    census_names = {"Gas": CensusTechnology.Gas,
                    "Heizoel": CensusTechnology.Oil,
                    "Holz_Holzpellets": CensusTechnology.Wood,
                    "Biomasse_Biogas": CensusTechnology.Biomass,
                    "Solar_Geothermie_Waermepumpen": CensusTechnology.Renewable,
                    "Strom": CensusTechnology.Electric,
                    "Kohle": CensusTechnology.Coal,
                    "Fernwaerme": CensusTechnology.District_Heating,
                    "kein_Energietraeger": CensusTechnology.NoEnergyCarrier}

    gdf_in_region = dataset.fetch(topology.convex_hull, query)
    gdf_in_region = gdf_in_region.rename(columns=census_names)

    technologies = list(census_names.values())

    for tech in technologies:
        gdf_in_region[tech] = pd.to_numeric(gdf_in_region[tech], errors='coerce').fillna(0)

    tech_amounts = {}
    for tech in technologies:
        tech_amounts[tech] = gdf_in_region[tech].sum()
    total = sum(tech_amounts.values())

    if total == 0:
        tech_shares = {tech: 0.0 for tech in technologies}
    else:
        tech_shares = {tech: amount / total for tech, amount in tech_amounts.items()}

    tech_shares = {k: float(v) for k, v in tech_shares.items()}

    name_mapping = query.params.get("name_mapping", {})
    if name_mapping:
        mapped_shares: dict[str, float] = {}
        for k, v in name_mapping.items():
            if v is None:
                continue
            mapped_shares[v] = mapped_shares.get(v, 0.0) + tech_shares[k]
        # ensure values sum to 1
        total_mapped = sum(mapped_shares.values())
        if total_mapped > 0:
            mapped_shares = {k: v / total_mapped for k, v in mapped_shares.items()}
        else:
            mapped_shares = {k: 0.0 for k in mapped_shares.keys()}

        tech_shares = mapped_shares

    return tech_shares


def example_data_registry(streets: gpd.GeoDataFrame) -> DataRegistry:
    census_heating_shares = FileDataset(
        keys=[DataKeys.HEATING_SHARES],
        file_path=str(pathlib.Path(__file__).parent / "Census2022HeatingType100mGrid.geojson"),
        query_function=census_query, )

    residential_heat_demand = StreetValueDataset.from_column(
        streets, id_column="id", value_column="raumwaerme",
        keys=[DataKeys.RESIDENTIAL_HEAT_DEMAND], unit=UnitEnum.KWH)

    heat_profile = CSVDataset(
        keys=[DataKeys.RESIDENTIAL_HEAT_DEMAND_PROFILE],
        file_path=str(pathlib.Path(__file__).parent / "heat_demand_profile.txt"),
        pandas_kwargs={"sep": r"\s+", "header": None})

    data_registry = DataRegistry(crs="EPSG:25832")
    data_registry.register_streets(streets, id_column="id", divide_at_junctions=True, gap_distance=5,
                                   drop_isolated_null_columns=["raumwaerme"])
    data_registry.register(census_heating_shares)
    data_registry.register(residential_heat_demand)
    data_registry.register(heat_profile)
    return data_registry
