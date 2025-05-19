# === Global Dataset Registry ===
from core.technologies import Technologies
import pandas as pd
import numpy as np
from pathlib import Path

DATASET_REGISTRY = {}
"""
Global registry for dataset handlers.

Key: (source_name, dataset_name)
Value: Dataset handler class which can be called with a query to fetch data.
"""

# === Decorator to Auto-Register Dataset Handlers ===
def register_dataset(source_name, dataset_name):
    def decorator(handler):
        DATASET_REGISTRY[(source_name, dataset_name)] = handler()
        return handler
    return decorator

# === Dataset Handlers ===
@register_dataset("Census2022", "HeatingType100mGrid")
class HeatingType100mGrid:
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
        df_data_in_bb = ... # Fetch data based on query, which is only a bounding box in this case

        df_data_in_bb = create_random_data(10) # replace

        return df_data_in_bb

# === Generic Fetch Function ===
def fetch_data(source_name, dataset_name, query):
    handler = DATASET_REGISTRY.get((source_name, dataset_name))
    if not handler:
        raise ValueError(f"No handler for {source_name}:{dataset_name}")
    return handler(query)



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
        Technologies.Gas.value: np.random.randint(0, 6, n_rows),
        Technologies.Oil.value: np.random.randint(0, 6, n_rows),
        Technologies.Wood.value: np.random.randint(0, 6, n_rows),
        Technologies.Biomass.value: np.random.randint(0, 6, n_rows),
        Technologies.Renewable.value: np.random.randint(0, 6, n_rows),
        Technologies.Electric.value: np.random.randint(0, 6, n_rows),
        Technologies.Coal.value: np.random.randint(0, 6, n_rows),
        Technologies.District_Heating.value: np.random.randint(0, 6, n_rows),
        Technologies.NoEnergyCarrier.value: np.random.randint(0, 6, n_rows),
    }

    df = pd.DataFrame(data)

    return df

