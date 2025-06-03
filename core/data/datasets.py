from abc import ABC
from pathlib import Path
from typing import Any

from core.data.data_registry import DataRegistry, Dataset, global_data_registry
import geopandas as gpd
import pandas as pd
from core.data.technology import CensusTechnology
import requests

from shapely.geometry import shape

@global_data_registry
class Census2022HeatingType100mGrid(Dataset):
    def __init__(self, path: str = "data/Census2022HeatingType100mGrid/Census2022HeatingType100mGrid.csv"):
        super().__init__(
            types="residential_heating_technology_shares",
            path=path,
            crs="EPSG:3035")

    def load_data(self) -> gpd.GeoDataFrame:
        df = pd.read_csv(self.path, sep=";")
        df.rename(columns={
            "Gas": CensusTechnology.Gas,
            "Heizoel": CensusTechnology.Oil,
            "Holz_Holzpellets": CensusTechnology.Wood,
            "Biomasse_Biogas": CensusTechnology.Biomass,
            "Solar_Geothermie_Waermepumpen": CensusTechnology.Renewable,
            "Strom": CensusTechnology.Electric,
            "Kohle": CensusTechnology.Coal,
            "Fernwaerme": CensusTechnology.District_Heating,
            "kein_Energietraeger": CensusTechnology.NoEnergyCarrier,
        }, inplace=True)

        for tech in CensusTechnology:
            df[tech] = pd.to_numeric(df.get(tech, 0), errors='coerce').fillna(0)

        return gpd.GeoDataFrame(
            df,
            geometry=gpd.points_from_xy(df.x_mp_100m, df.y_mp_100m),
            crs=self.crs
        )

    def query(self, query: dict, type_: str) -> dict[CensusTechnology, float]:
        self.check(type_, query["region"])
        if type_ =="residential_heating_technology_shares":
            gdp_data = self.data
            gdp_data = gdp_data.to_crs(query["region"].crs)
            gpd_in_region = gpd.sjoin(gdp_data, query["region"], how="inner", predicate="intersects")

            technology_amounts = {}
            for tech in CensusTechnology:
                technology_amounts[tech] = gpd_in_region[tech].sum()
            # Calculate shares
            total_amount = sum(technology_amounts.values())
            if total_amount == 0:
                return {tech: 0 for tech in CensusTechnology}  # Avoid division by zero
            technology_shares = {tech: amount / total_amount for tech, amount in technology_amounts.items()}
            return technology_shares


@global_data_registry
class WaermeatlasHessen(Dataset):
    def __init__(self, path: str = "data/WaermeatlasHessen.gpkg", crs: str = "EPSG:25832"):
        super().__init__(
            types="residential_heat_demand",
            path=path,
            crs=crs)

    def load_data(self) -> gpd.GeoDataFrame:
        return gpd.read_file(Path("data") / "WaermeatlasHessen.gpkg", layer="WAH_Punkte")

    def query(self, query: dict, type_: str) -> float:
        self.check(type_, query["region"])
        if type_ == "residential_heat_demand":
            gdf = self.data.to_crs(query["region"].crs)
            gdf_in_region = gpd.sjoin(gdf, query["region"], how="inner", predicate="intersects")
            total_heat_demand = gdf_in_region["qnutzwaerme_2020_kwh"].sum()
            return total_heat_demand


@global_data_registry
class GeoportalHessenCityBoundaries(Dataset):
    def __init__(self, path: str = "data/GeoportalHessenCityBoundaries.gpkg", crs: str = "EPSG:4326"):
        super().__init__(
            types="city_boundary",
            path=path,
            crs=crs)

    def load_data(self) -> None:
        return None

    def query(self, query: dict[str, Any], type_: str) -> gpd.GeoDataFrame:
        self.check_type(type_)
        if type_ == "city_boundary":
            # dp: Find an understand API
            url = f"https://www.geoportal.hessen.de/spatial-objects/885/collections/borders:gemeindenHE_wfs/items?limit=50&GMDE_BZ={query['city_name']}&f=json"
            response = requests.get(url)
            data = response.json()
            # dp: to geometry
            geometry = shape(data['features'][0]['geometry'])
            # dp: transform geometry to base crs
            gdf_city_boundary = gpd.GeoDataFrame(geometry=[geometry], crs=self.crs)
            gdf_city_boundary = gdf_city_boundary.to_crs(query["base_crs"])
            return gdf_city_boundary