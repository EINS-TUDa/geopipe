# === Global Dataset Registry ===
from abc import ABC, abstractmethod
from typing import Any

from core.technology import Technology
import pandas as pd
import numpy as np
import geopandas as gpd


class RegistryService:
    _registry = {}

    @classmethod
    def register_dataset(cls, source_name, dataset_name):
        def decorator(dataset_class):
            key = (source_name, dataset_name)
            if key in cls._registry:
                raise ValueError(f"Handler already registered for {source_name}:{dataset_name}")
            cls._registry[key] = dataset_class()
            return dataset_class
        return decorator

    @classmethod
    def fetch_data(cls, source_name, dataset_name, query):
        dataset = cls._registry.get((source_name, dataset_name))
        if not dataset:
            raise ValueError(f"No handler for {source_name}:{dataset_name}")
        return dataset(query)

# === Datasets ===
class BaseDataset(ABC):
    @abstractmethod
    def __call__(self, query: dict[str,Any]) -> gpd.GeoDataFrame:
        pass

