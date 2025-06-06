from collections import defaultdict

from core.data.dataset import Dataset
import geopandas as gpd


class DataRegistry:
    def __init__(self):
        self._type_to_datasets: dict[str, list[Dataset]] = defaultdict(list)

    def register(self, dataset: Dataset):
        if not isinstance(dataset, Dataset):
            raise TypeError(f"{dataset} is not an instance of Dataset")
        if not isinstance(dataset.types, list):
            raise TypeError("Dataset types must be a list of strings.")
        for type_ in dataset.types:
            self._type_to_datasets[type_].append(dataset)

    def get_datasets(self, type_: str, region: gpd.GeoDataFrame = None) -> list[Dataset]:
        candidates = self._type_to_datasets.get(type_, [])
        if region is None:
            return candidates
        # Filter datasets applicable in the query region
        return [ds for ds in candidates if ds.is_in_region(region)]

    def query(self, query: dict):
        """
        query dict keys:
          - 'type': str
          - 'region': GeoDataFrame
          - ...other params
        """
        type_ = query.get("type")
        region = query.get("region")
        if not type_ :
            raise ValueError("'type' must be specified in the query.")

        datasets = self.get_datasets(query["type"], region)
        if not datasets:
            raise LookupError(f"No datasets found for type '{type_}' in region '{region}'.")

        # return the first dataset's query result:
        return datasets[0].query(query)

    def load_from_default(self, allowed_types: list[str] = None):
        for cls in DEFAULT_DATA_REGISTRY.get_all():
            instance = cls()
            if allowed_types is None or any(t in allowed_types for t in instance.types):
                self.register(instance)


class DefaultDataRegistry:
    # stores classes while DataRegistry stores instances
    def __init__(self):
        self._registry: list[type[Dataset]] = []

    def register(self, dataset_cls: type[Dataset]):
        self._registry.append(dataset_cls)

    def get_all(self) -> list[type[Dataset]]:
        return self._registry

DEFAULT_DATA_REGISTRY = DefaultDataRegistry()

def default_data_registry(cls):
    DEFAULT_DATA_REGISTRY.register(cls)
    return cls