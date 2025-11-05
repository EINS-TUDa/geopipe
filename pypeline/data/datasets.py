from abc import ABC
from pathlib import Path
from typing import Any
from enum import Enum
import shapely.geometry

import numpy as np

from pypeline.data.dataset import Dataset, SpatialDataset, TemporalDataset
from pypeline.data.data_registry import DataRegistry, default_data_registry
import geopandas as gpd
import pandas as pd
import requests

from shapely.geometry import shape

from pypeline.energy_system.technology_registry import DEFAULT_TECHNOLOGY_REGISTRY


class CensusTechnology(Enum):
    Gas = "Gas"
    Oil = "Oil"
    Wood = "Wood"
    Biomass = "Biomass"
    Renewable = "Renewable" # Solar, Geothermal, Heatpump
    Electric = "Electric"
    Coal = "Coal"
    District_Heating = "District Heating"
    NoEnergyCarrier = "No Energy Carrier"

@default_data_registry
class Census2022HeatingType100mGrid(SpatialDataset):
    def __init__(self, path: str = "data/Census2022HeatingType100mGrid/Census2022HeatingType100mGrid_Polygons_southhessen.geojson"):
        """Already transformed points to polygons to avoid long transformation times."""
        super().__init__(
            types=["residential_heat_technology_shares"],
            path=path,
            crs="EPSG:3035")

    def load_data(self) -> gpd.GeoDataFrame:
        gdf = gpd.read_file(self.path)
        gdf.rename(columns={
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
            gdf[tech] = pd.to_numeric(gdf.get(tech, 0), errors='coerce').fillna(0)

        return gdf

    def query(self, query: dict) -> dict[CensusTechnology, float]:
        type_ = query["type"]
        self.check(type_, query["region"])
        if type_ =="residential_heat_technology_shares":
            gdp_data = self.data
            gdp_data = gdp_data.to_crs(query["region"].crs)
            gpd_in_region = gpd.sjoin(gdp_data, query["region"], how="inner", predicate="intersects")

            technology_amounts = {}
            for tech in CensusTechnology:
                technology_amounts[tech] = gpd_in_region[tech].sum()
            # Calculate shares
            total_amount = sum(technology_amounts.values())
            if total_amount == 0:
                technology_shares = {tech: 0 for tech in CensusTechnology}  # Avoid division by zero
            else:
                technology_shares = {tech: amount / total_amount for tech, amount in technology_amounts.items()}

            # rename keys if given
            if query.get("name_mapping"):
                technology_shares = {
                    query["name_mapping"].get(tech, tech): share for tech, share in technology_shares.items()
                }
            return technology_shares
        else:
            raise ValueError(f"Unsupported type '{type_}' for Census2022HeatingType100mGrid dataset.")


@default_data_registry
class WaermeatlasHessen(SpatialDataset):
    def __init__(self, path: str = "data/WaermeatlasHessen.gpkg", crs: str = "EPSG:25832"):
        super().__init__(
            types=["residential_heat"],
            path=path,
            crs=crs)

    def load_data(self) -> gpd.GeoDataFrame:
        return gpd.read_file(Path("data") / "WaermeatlasHessen.gpkg", layer="WAH_Punkte")

    def query(self, query: dict) -> float:
        type_ = query["type"]
        self.check(type_, query["region"])
        if type_ == "residential_heat":
            gdf = self.data.to_crs(query["region"].crs)
            gdf_in_region = gpd.sjoin(gdf, query["region"], how="inner", predicate="intersects")
            total_heat_demand = gdf_in_region["qnutzwaerme_2020_kwh"].sum()
            return total_heat_demand


@default_data_registry
class GeoportalHessenCityBoundaries(SpatialDataset):
    def __init__(self, path: str = "data/GeoportalHessenCityBoundaries.gpkg", crs: str = "EPSG:4326"):
        super().__init__(
            types=["city_boundary"],
            path=path,
            crs=crs)

    def load_data(self) -> None:
        return None

    def query(self, query: dict[str, Any]) -> gpd.GeoDataFrame:
        type_ = query["type"]
        self.is_type(type_)
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

@default_data_registry
class ResidentialHeatDemandProfile(TemporalDataset):
    def __init__(self, path: str = Path("data") / "D_Heat_Household_J.txt"):
        super().__init__(
            types=["residential_heat_profile"],
            path=path)

    def load_data(self) -> pd.DataFrame:
        return pd.read_csv(self.path, sep=" ", header=None).T

    def query(self, query: dict) -> pd.Series:
        type_ = query["type"]
        self.check(type_)
        if type_ == "residential_heat_profile":
            return self.data.iloc[:, 0]

@default_data_registry
class ResidentialElectricityDemand(Dataset):
    def __init__(self, path: str = None):
        super().__init__(
            types=["residential_electricity"],
            path=path)

    def load_data(self) -> pd.Series:
        return None

    def query(self, query: dict) -> float:
        return 10000

@default_data_registry
class ResidentialElectricityDemandProfile(TemporalDataset):
    def __init__(self, path: str = Path("data") / "corrected_eletricity_demand_2016.txt"):
        super().__init__(
            types=["residential_electricity_profile"],
            path=path)

    def load_data(self) -> pd.DataFrame:
        return pd.read_csv(self.path, sep=" ", header=None).T

    def query(self, query: dict) -> pd.Series:
        type_ = query["type"]
        self.check(type_)
        if type_ == "residential_electricity_profile":
            return self.data.iloc[:, 0]