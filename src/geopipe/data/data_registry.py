"""
DataRegistry - Central management of all Datasets.

The Registry manages all available Datasets and routes queries to the appropriate
Dataset (based on key, scope and priority). Queries work on region topologies:
each region is routed to the highest-priority Dataset whose scope covers it.
It also owns the project CRS: every registered Dataset is reprojected to it.
"""

import logging
from collections import defaultdict
from enum import Enum
from typing import Any, Hashable, Mapping, Optional, TypeVar, TYPE_CHECKING
from pydantic import BaseModel, ConfigDict
from pyproj import CRS

from geopipe.data.dataset import Dataset

if TYPE_CHECKING:
    from geopipe.topology_builder.topology import Topology

logger = logging.getLogger(__name__)

K = TypeVar("K", bound=Hashable)


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


class DataRegistryQuery(BaseModel):
    """A query to the DataRegistry.

    ``key`` selects the datasets; ``params`` are dataset-specific options that are passed
    through unchanged to the query function of the dataset that answers the query.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    params: dict[str, Any] = {}


class DataRegistry:
    """
    Central registry for all Datasets.

    The Registry:
    - Owns the project CRS; all Datasets are reprojected to it on registration
    - Manages all registered Datasets
    - Sorts by priority and registration order
    - Routes each queried region to the best Dataset covering it
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
        topology: Optional["Topology"] = None
        ) -> list[Dataset]:
        """
        Returns all datasets for a type (optionally filtered by key and by coverage of a region topology).

        Args:
            key: The requested data type
            topology: Optional region topology the datasets must cover

        Returns:
            List of datasets, sorted by priority
        """
        if key is None:
            # all datasets
            candidates = [ds for datasets in self._type_to_datasets.values() for ds in datasets]
        else:
            candidates = self._type_to_datasets.get(key, [])

        if topology is None:
            return candidates

        # Only return datasets that are valid in the region
        return [ds for ds in candidates if ds.is_in_region(topology)]

    def query(self, topology: "Topology", query: DataRegistryQuery) -> Any:
        """
        Answers the query for a single region topology. See :meth:`query_all`.
        """
        return self.query_all({0: topology}, query)[0]

    def query_all(self, topologies: Mapping[K, "Topology"], query: DataRegistryQuery) -> dict[K, Any]:
        """
        Answers the query for many region topologies at once.

        Each topology is routed to the highest-priority dataset whose scope covers it
        completely. Each dataset is then queried once with all topologies routed to it.

        Args:
            topologies: Region topologies by an arbitrary key (e.g. the region id)
            query: The query

        Returns:
            The result per key, in the order of ``topologies``
        """
        candidates = self._type_to_datasets.get(query.key, [])
        if not candidates:
            raise LookupError(f"No datasets registered for key '{query.key}'.")

        routed: dict[Dataset, dict[K, "Topology"]] = {}
        uncovered = []
        for topology_key, topology in topologies.items():
            dataset = next((ds for ds in candidates if ds.is_in_region(topology)), None)
            if dataset is None:
                uncovered.append(topology_key)
            else:
                routed.setdefault(dataset, {})[topology_key] = topology
        if uncovered:
            raise LookupError(f"No dataset for key '{query.key}' covers the region(s) {uncovered}.")

        results: dict[K, Any] = {}
        for dataset, routed_topologies in routed.items():
            results.update(dataset.query_all(routed_topologies, query))
        return {topology_key: results[topology_key] for topology_key in topologies}
