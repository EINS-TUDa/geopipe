"""
DataRegistry - Central management of all Datasets.

The Registry manages all available Datasets and routes queries
to the appropriate Dataset (based on type, region, and priority).
It also owns the project CRS: every registered Dataset is reprojected to it.
"""

import logging
from collections import defaultdict
from enum import Enum
from typing import Optional
import geopandas as gpd
import networkx as nx
from pyproj import CRS

from shapely.geometry.multipoint import MultiPoint

from geopipe.data.dataset import Dataset

logger = logging.getLogger(__name__)


class DataKeys(str, Enum):
    RESIDENTIAL_HEAT_DEMAND_PROFILE = "residential_heat_demand_profile"
    RESIDENTIAL_ELECTRICITY_DEMAND_PROFILE = "residential_electricity_demand_profile"
    RESIDENTIAL_ELECTRICITY_DEMAND = "residential_electricity_demand"
    RESIDENTIAL_HEAT_DEMAND = "residential_heat_demand"
    HEATING_SHARES = "heating_shares"
    STREET_NETWORK = "street_network"
    LINEAR_HEAT_DENSITY = "linear_heat_density"
    EXISTING_HEAT_GRID = "existing_heat_grid"

"""
DataKeys.HEAT_DEMAND_PROFILE should return a pd.Series
DataKeys.ELECTRICITY_DEMAND_PROFILE should return a pd.Series
DataKeys.ELECTRICITY_DEMAND should return float with the sum of the residential electricity demand in kWh for the specified region.
DataKeys.RESIDENTIAL_HEAT_DEMAND should return float with the sum of the residential heat demand in kWh for the specified region.
DataKeys.HEATING_SHARES should return a dict[CensusTechnology, float], or dict[str, float] if a name_mapping is provided
DataKeys.STREET_NETWORK should return a GeoDataFrame with the street network for the specified region, with columns 'geom' (LineString)
DataKeys.LINEAR_HEAT_DENSITY should return a GeoDataFrame with the linear heat density for the specified region, with columns 'geom' (LineString) and 'heat_density_mwh_per_km'
DataKeys.EXISTING_HEAT_GRID should return a GeoDataFrame with the existing heat grid for the specified region, with columns 'geom' (LineString)
"""


def is_metric_crs(crs: CRS) -> bool:
    """True if ``crs`` is projected with metre axes."""
    return crs.is_projected and crs.axis_info[0].unit_name in ("metre", "meter")


class DataRegistry:
    """
    Central registry for all Datasets.

    The Registry:
    - Owns the project CRS; all Datasets are reprojected to it on registration
    - Manages all registered Datasets
    - Sorts by priority and registration order
    - Routes queries to the best available Dataset
    """

    def __init__(self, crs: str | CRS):
        """
        Args:
            crs: The project CRS (e.g. "EPSG:25832"). Should be projected in metres, as lengths and
                distances throughout the pipeline are measured in CRS units.
        """
        self._crs: CRS = CRS.from_user_input(crs)
        if not is_metric_crs(self._crs):
            logger.warning("CRS '%s' is not a projected CRS in metres. Lengths and distances are measured in "
                           "CRS units.", self._crs.name)
        self._type_to_datasets: dict[str, list[Dataset]] = defaultdict(list)
        self._registration_counter: int = 0

    @property
    def crs(self) -> CRS:
        return self._crs

    def register(self, dataset: Dataset) -> None:
        """
        Registers a Dataset and reprojects it to the project CRS.
        """
        if not isinstance(dataset, Dataset):
            raise TypeError(f"{dataset} is not an instance of Dataset")

        dataset.set_crs(self._crs)

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

        region = query.pop("region")
        if isinstance(region, gpd.GeoDataFrame):
            boundary_gdf = gpd.GeoDataFrame(
                geometry=[region.geometry.union_all().convex_hull],
                crs=self._crs)
        elif isinstance(region, nx.Graph):
            boundary_gdf = gpd.GeoDataFrame(
                geometry=[MultiPoint(list(region.nodes)).convex_hull],
                crs=self._crs)
        else:
            raise TypeError("query expects region as nx.Graph")

        datasets = self.get_datasets(key, boundary_gdf)

        if not datasets:
            region_info = f" in region '{region}'" if region is not None else ""
            raise LookupError(f"No datasets found for key '{key}'{region_info}.")

        # The first dataset has the highest priority
        return datasets[0].query(query=query, region = boundary_gdf)
