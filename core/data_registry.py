# === Global Dataset Registry ===
from core.technology import Technology
import pandas as pd
import numpy as np
from pathlib import Path

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

# === Dataset Handlers ===
@RegistryService.register_dataset("Census2022", "HeatingType100mGrid")
class HeatingType100mGrid:
    """Could contain class variables to store access credentials or similar, if needed."""
    def __call__(self, query):
        """
        Fetch data for the HeatingType100mGrid dataset.
        Visualization of the dataset: https://atlas.zensus2022.de/
        Grid size: 100m x 100m
        coordinate system: ETRS89-LAEA Europe (EPSG: 3035)
        column x_mp: geographical longitude of the center of the grid cell in ETRS89-LAEA Europe (EPSG: 3035)
        column y_mp: geographical latitude of the center of the grid cell in ETRS89-LAEA Europe (EPSG: 3035)
        Ignore column Insgesamt_Energietraeger. The sums don't add up to Insgesamt_Energietraeger
            because of a privacy protection algorithm.

        :param query: dict - Contains the query parameters for fetching data.
        :return: Data for the specified query.
        """
        df_data = pd.read_csv(Path("data") / "Gebaeude_mit_Wohnraum_nach_Energietraeger_der_Heizung" / "Zensus2022_Gebaeude_mit_Wohnraum_nach_Energietraeger_der_Heizung_100m-Gitter.csv")
        df_data_in_bb = create_random_data(10) # todo: Fetch data based on query, which is only a bounding box in this case
        return df_data_in_bb




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

