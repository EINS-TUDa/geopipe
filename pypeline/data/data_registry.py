"""
DataRegistry - Central management of all Datasets.

The Registry manages all available Datasets and routes queries
to the appropriate Dataset (based on type, region, and priority).
"""

from collections import defaultdict
from typing import Optional
import geopandas as gpd
import yaml
import importlib

from pypeline.data.dataset import Dataset




class DataRegistry:
    """
    Central registry for all Datasets.

    The Registry:
    - Manages all registered Datasets
    - Sorts by priority and registration order
    - Routes queries to the best available Dataset
    """

    def __init__(self):
        self._type_to_datasets: dict[str, list[Dataset]] = defaultdict(list)
        self._registration_counter: int = 0

    def register(self, dataset: Dataset) -> None:
        """
        Registers a Dataset.
        """
        if not isinstance(dataset, Dataset):
            raise TypeError(f"{dataset} is not an instance of Dataset")

        # Store registration order
        dataset._registration_order = self._registration_counter
        self._registration_counter += 1

        # Add to all types
        for type_ in dataset.keys:
            self._type_to_datasets[type_].append(dataset)

        # Sort by priority (higher first) and registration order
        self._sort_datasets()

    def _sort_datasets(self) -> None:
        """Sorts all datasets by priority (higher first) and registration order."""
        for datasets in self._type_to_datasets.values():
            datasets.sort(key=lambda ds: (-ds.priority, ds._registration_order))

    def get_datasets(
        self,
        key: Optional[str] = None,
        region: Optional[gpd.GeoDataFrame] = None
        ) -> list[Dataset]:
        """
        Returns all datasets for a type (optionally filtered by region and key).

        Args:
            key: The requested data type
            region: Optional query region

        Returns:
            List of datasets, sorted by priority
        """
        if key is None:
            # all datasets
            candidates = [ds for datasets in self._type_to_datasets.values() for ds in datasets]
        else:
            candidates = self._type_to_datasets[key]

        if region is None:
            return candidates

        # Only return datasets that are valid in the region
        return [ds for ds in candidates if ds.is_in_region(region)]

    def query(self, query: dict):
        """
        Executes a query and returns the result from the best dataset.

        Args:
            query: Dictionary with at least 'key', optionally 'region' and other parameters

        Returns:
            Query result from the best available dataset
        """
        key = query.get("key")
        if not key:
            raise ValueError("'key' must be specified in the query.")

        region = query.get("region")
        datasets = self.get_datasets(key, region)

        if not datasets:
            region_info = f" in region '{region}'" if region is not None else ""
            raise LookupError(f"No datasets found for key '{key}'{region_info}.")

        # The first dataset has the highest priority
        return datasets[0].query(query)



