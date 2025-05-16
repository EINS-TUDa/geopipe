# === Global Dataset Registry ===
from core.technologies import Technologies
import pandas as pd
import geopandas as gpd

DATASET_REGISTRY = {}

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
        df = pd.read_csv("../data/Gebaeude_mit_Wohnraum_nach_Energietraeger_der_Heizung/Zensus2022_Gebaeude_mit_Wohnraum_nach_Energietraeger_der_Heizung_100m-Gitter.csv")
        data = ... # Fetch data based on query, which is only a bounding box in this case
        data = {Technologies.Gas: 0.24324,
                Technologies.Oil: 0.37435}  # Replace with actual data.

        return data


# @register_dataset("Census2022", "HeatingFuel100mGrid")
# class HeatingFuel100mGrid:
#     def __call__(self, query):
#         data = ...  # Fetch data based on query
#         return data

# === Generic Fetch Function ===
def fetch_data(source_name, dataset_name, query):
    handler = DATASET_REGISTRY.get((source_name, dataset_name))
    if not handler:
        raise ValueError(f"No handler for {source_name}:{dataset_name}")
    return handler(query)
