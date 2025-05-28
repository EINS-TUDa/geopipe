# === Global Dataset Registry ===
from abc import ABC, abstractmethod
from core.technology import Technology
import pandas as pd
import numpy as np
from pathlib import Path
import geopandas as gpd

class RegistryService:
    _registry = {}

    @classmethod
    def register_dataset(cls, source_name, dataset_name):
        def decorator(handler_class):
            key = (source_name, dataset_name)
            if key in cls._registry:
                raise ValueError(f"Handler already registered for {source_name}:{dataset_name}")
            cls._registry[key] = handler_class()
            return handler_class
        return decorator

    @classmethod
    def fetch_data(cls, source_name, dataset_name, query):
        handler = cls._registry.get((source_name, dataset_name))
        if not handler:
            raise ValueError(f"No handler for {source_name}:{dataset_name}")
        return handler(query)

# === Datasets ===
class BaseDataset(ABC):
    crs: str  # Optional: enforce that all datasets define a CRS

    @abstractmethod
    def __call__(self, query: dict[str,any]) -> gpd.GeoDataFrame:
        pass

@RegistryService.register_dataset("Census2022", "HeatingType100mGrid")
class Census2022HeatingType100mGrid:
    crs = "EPSG:3035"

    def __call__(self, query) -> gpd.GeoDataFrame:
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
        # filter the data based on the polygone
        if not isinstance(polygone, gpd.GeoDataFrame) or polygone.crs != self.crs:
            raise ValueError("Query must be a GeoDataFrame with the correct CRS (EPSG:3035).")
        # dp: Join operation
        gdf_data_in_polygone = gpd.sjoin(gdf_data, polygone, how="inner", predicate="within")
        return gdf_data_in_polygone

@RegistryService.register_dataset("WaermeatlasHessen", "ResidentialYearlyHeatDemand")
class WaermeatlasHessenResidentialYearlyHeatDemand:
    crs = ...


def create_random_data(n_rows) -> pd.DataFrame:
    """
    Create a random dataset for testing purposes.
    :param n_rows: how many rows to create
    :return: pd.DataFrame with random data
    """

    # Define random data for testing
    data = {
        "x_mp": np.random.uniform(4000000, 5000000, n_rows),  # Random x-coordinates
        "y_mp": np.random.uniform(3000000, 4000000, n_rows),  # Random y-coordinates
        Technology.Gas.value: np.random.randint(0, 6, n_rows),
        Technology.Oil.value: np.random.randint(0, 6, n_rows),
        Technology.Wood.value: np.random.randint(0, 6, n_rows),
        Technology.Biomass.value: np.random.randint(0, 6, n_rows),
        Technology.Renewable.value: np.random.randint(0, 6, n_rows),
        Technology.Electric.value: np.random.randint(0, 6, n_rows),
        Technology.Coal.value: np.random.randint(0, 6, n_rows),
        Technology.District_Heating.value: np.random.randint(0, 6, n_rows),
        Technology.NoEnergyCarrier.value: np.random.randint(0, 6, n_rows),
    }

    df = pd.DataFrame(data)

    return df

