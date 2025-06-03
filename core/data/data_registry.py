# === Global Dataset Registry ===
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any

import geopandas as gpd


class Dataset(ABC):
    def __init__(self, types: str|list[str], path: str, crs: str = None, regional_validity: gpd.GeoDataFrame = None):
        # List of demand/source types this dataset can provide
        self.types: list[str] = types
        self.crs = crs
        self.path = path
        self.data = self.load_data()
        self.regional_validity = regional_validity

    @abstractmethod
    def load_data(self) -> gpd.GeoDataFrame:
        ...

    @abstractmethod
    def query(self, region: gpd.GeoDataFrame, type_: str) -> Any:
        """
        Extracts data of the given type for the given region.
        Must be implemented by subclasses.
        """
        ...

    def check_region(self, region: gpd.GeoDataFrame) -> bool:
        """
        Determines if this dataset is valid/applicable in the given region for the given type.
        Override as needed.
        """
        if self.regional_validity is not None:
            # check if region is completely within the dataset's regional validity. MAke sure the crs matches
            if region.crs != self.regional_validity.crs:
                region = region.to_crs(self.regional_validity.crs)
            return self.regional_validity.contains(region).all()
        else:
            # If no regional validity is defined, assume it is applicable everywhere
            return True

    def check_type(self, type_: str) -> bool:
        """
        Checks if the dataset can provide data for the given type.
        """
        return type_ in self.types

    def check(self, type_: str, region: gpd.GeoDataFrame):
        """
        # check type and region
        """
        if not self.check_type(type_) and self.check_region(region):
            ValueError(f"Dataset {self.__class__.__name__} is not applicable for type '{type_}' in the given region.")



class DataRegistry:
    def __init__(self):
        self._type_to_instances = defaultdict(list)
        self._datasets = []

    def register(self, dataset: Dataset):
        self._datasets.append(dataset)
        for type_ in dataset.types:
            self._type_to_instances[type_].append(dataset)

    def get_datasets(self, type_: str) -> list[Dataset]:
        return self._type_to_instances.get(type_, [])

    def load_from_global(self, allowed_types: list[str] = None):
        for cls in GLOBAL_DATA_REGISTRY.get_all():
            instance = cls()
            if allowed_types is None or any(t in allowed_types for t in instance.types):
                self.register(instance)


class GlobalDataRegistry:
    # stores classes while DataRegistry stores instances
    def __init__(self):
        self._registry: list[type[Dataset]] = []

    def register(self, dataset_cls: type[Dataset]):
        self._registry.append(dataset_cls)

    def get_all(self) -> list[type[Dataset]]:
        return self._registry

GLOBAL_DATA_REGISTRY = GlobalDataRegistry()

def global_data_registry(cls):
    GLOBAL_DATA_REGISTRY.register(cls)
    return cls
