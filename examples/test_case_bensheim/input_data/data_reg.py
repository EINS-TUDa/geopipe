import pathlib

import networkx as nx
import pandas as pd
import geopandas as gpd
from shapely.geometry.multipoint import MultiPoint

from geopipe import DataRegistry
from geopipe.data.data_registry import DataKeys
from geopipe.data.data_utils import get_gdf_from_ags
from geopipe.data.dataset import FileDataset, CensusTechnology


def census_bensheim_query(dataset: FileDataset, region: gpd.GeoDataFrame, query: dict) -> dict[CensusTechnology, float]:
    if query["key"] != "heating_shares":
        raise ValueError("census_neuburg_query only supports 'heating_shares' key")

    census_names = {   "Gas": CensusTechnology.Gas,
                       "Heizoel": CensusTechnology.Oil,
                       "Holz_Holzpellets": CensusTechnology.Wood,
                       "Biomasse_Biogas": CensusTechnology.Biomass,
                       "Solar_Geothermie_Waermepumpen": CensusTechnology.Renewable,
                       "Strom": CensusTechnology.Electric,
                       "Kohle": CensusTechnology.Coal,
                       "Fernwaerme": CensusTechnology.District_Heating,
                       "kein_Energietraeger": CensusTechnology.NoEnergyCarrier}

    if isinstance(region, gpd.GeoDataFrame):
        boundary_gdf = gpd.GeoDataFrame(
            geometry=[region.geometry.union_all()],
            crs=region.crs)
        region_crs = boundary_gdf.crs
    else:
        raise TypeError("query expects region as geodataframe ")

    gdf = dataset.get_data()
    if region_crs is not None and gdf.crs != region_crs:
        gdf = gdf.to_crs(region_crs)

    gdf_in_region = gpd.sjoin(gdf, boundary_gdf, predicate="intersects", how="inner")
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

    name_mapping = query.get("name_mapping", {})
    if name_mapping:
        mapped_shares = {v: tech_shares[k] for k,v in name_mapping.items() if v is not None}
        # ensure values sum to 1
        total_mapped = sum(mapped_shares.values())
        if total_mapped > 0:
            mapped_shares = {k: v / total_mapped for k, v in mapped_shares.items()}
        else:
            mapped_shares = {k: 0.0 for k in mapped_shares.keys()}

        tech_shares = mapped_shares

    return tech_shares


def case1_data_registry() -> DataRegistry:
    neuburg_heating_shares = FileDataset(
        keys=[DataKeys.HEATING_SHARES],
        file_path= str(pathlib.Path(__file__).parent / "Census2022HeatingType100mGrid.geojson"),
        query_function=census_bensheim_query,
        priority=10,
        regional_validity=None
        # get_gdf_from_ags(["09185149"]
        )


    data_reg = DataRegistry()
    data_reg.register(neuburg_heating_shares)
    return data_reg

if __name__ == "__main__":
    data_reg = case1_data_registry()