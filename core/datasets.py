from pathlib import Path
from typing import Any

from core.data_registry import RegistryService, BaseDataset
import geopandas as gpd
import pandas as pd
from core.technology import Technology
import requests

from shapely.geometry import shape


@RegistryService.register_dataset("Census2022", "HeatingType100mGrid")
class Census2022HeatingType100mGrid(BaseDataset):
    crs = "EPSG:3035"

    def __call__(self, query: dict[str, Any]) -> gpd.GeoDataFrame:
        """
        query: is a polygone in the form of a GeoDataFrame with a single polygon.

        Fetch data for the HeatingType100mGrid dataset.
        Visualization of the dataset: https://atlas.zensus2022.de/
        Grid size: 100m x 100m
        coordinate system: ETRS89-LAEA Europe (EPSG: 3035)
        column x_mp: geographical longitude of the center of the grid cell in ETRS89-LAEA Europe (EPSG: 3035)
        column y_mp: geographical latitude of the center of the grid cell in ETRS89-LAEA Europe (EPSG: 3035)
        Ignore column Insgesamt_Energietraeger. The sums don't add up to Insgesamt_Energietraeger
            because of a privacy protection algorithm.

        :param polygone: dict - Contains the query parameters for fetching data.
        :return: gpd.GeoDataFrame - A GeoDataFrame containing the data for the specified polygone.
        """
        polygone = query.get("polygone")
        # dp: Ensure polygone is a GeoDataFrame with the correct CRS
        polygone = polygone.to_crs(query["base_crs"])

        df_data = pd.read_csv(Path("data") / "Census2022HeatingType100mGrid" / "Census2022HeatingType100mGrid.csv", sep=";")
        name_mapping = {
            "Gas": Technology.Gas,
            "Heizoel": Technology.Oil,
            "Holz_Holzpellets": Technology.Wood,
            "Biomasse_Biogas": Technology.Biomass,
            "Solar_Geothermie_Waermepumpen": Technology.Renewable,
            "Strom": Technology.Electric,
            "Kohle": Technology.Coal,
            "Fernwaerme": Technology.District_Heating,
            "kein_Energietraeger": Technology.NoEnergyCarrier,
        }
        # dp: Rename
        df_data.rename(columns=name_mapping, inplace=True)
        # dp: Convert all columns in name_mapping to numeric, errors='coerce' will convert non-numeric values to 0
        for tech in Technology:
            df_data[tech] = pd.to_numeric(df_data[tech], errors='coerce').fillna(0)
        # create a GeoDataFrame from the DataFrame
        gdf_data = gpd.GeoDataFrame(df_data, geometry=gpd.points_from_xy(df_data.x_mp_100m, df_data.y_mp_100m), crs=self.crs)
        # dp: Ensure the CRS of the GeoDataFrame matches the query CRS
        gdf_data = gdf_data.to_crs(query["base_crs"])
        # dp: Join operation
        gdf_data_in_polygone = gpd.sjoin(gdf_data, polygone, how="inner", predicate="intersects")
        return gdf_data_in_polygone

@RegistryService.register_dataset("WaermeatlasHessen", "WaermeatlasHessen")
class WaermeatlasHessen(BaseDataset):
    def __init__(self):
        self._data = None  # Cache for loaded data

    def _load_data(self) -> gpd.GeoDataFrame:
        if self._data is None:
            self._data = gpd.read_file(Path("data") / "WaermeatlasHessen.gpkg", layer="WAH_Punkte")
        return self._data

    def __call__(self, query: dict[str,Any]) -> gpd.GeoDataFrame:
        # caching needed as dataset contains different data
        gdf = self._load_data()
        polygone = query.get("polygone")
        # dp: To base crs
        gdf = gdf.to_crs(query["base_crs"])
        polygone = polygone.to_crs(query["base_crs"])
        # dp: Join operation
        gdf = gpd.sjoin(gdf, polygone, how="inner", predicate="intersects")
        return gdf

@RegistryService.register_dataset("GeoportalHessen", "CityBoundaries")
class GeoportalHessenCityBoundaries(BaseDataset):
    crs = "EPSG:4326"  # Geoportal Hessen uses WGS84
    def __call__(self, query: dict[str, Any]) -> gpd.GeoDataFrame:
        # dp: Find an understand API
        url = f"https://www.geoportal.hessen.de/spatial-objects/885/collections/borders:gemeindenHE_wfs/items?limit=50&GMDE_BZ={query['city_name']}&f=json"
        response = requests.get(url)
        data = response.json()
        # dp: to geometry
        geometry=shape(data['features'][0]['geometry'])
        # dp: transform geometry to base crs
        gdf_city_boundary = gpd.GeoDataFrame(geometry=[geometry], crs=self.crs)
        gdf_city_boundary = gdf_city_boundary.to_crs(query["base_crs"])
        return gdf_city_boundary