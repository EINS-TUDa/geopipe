from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Callable, Hashable, Mapping, TypeVar, TYPE_CHECKING
import geopandas as gpd
import pandas as pd
import shapely
from pyproj import CRS
from sqlalchemy import text

from ..data.database_connection import PostgresConnection
from ..energy_system.units import UnitEnum

if TYPE_CHECKING:
    from .data_registry import DataRegistryQuery
    from ..topology_builder.topology import Topology

K = TypeVar("K", bound=Hashable)

class CensusTechnology(Enum):
    Gas = "Gas"
    Oil = "Oil"
    Wood = "Wood"
    Biomass = "Biomass"
    Renewable = "Renewable" # Solar, Geothermal, Heatpump
    Electric = "Electric"
    Coal = "Coal"
    District_Heating = "District Heating"
    NoEnergyCarrier = "No Energy Carrier"

CENSUS_HEATING_CATEGORY_TO_TECH: dict[str, str | None] = {
    "Gas": "ind_gas_boiler",
    "Heizoel": "ind_oil_boiler",
    "Holz_Holzpellets": "ind_biomass_boiler",
    "Biomasse_Biogas": "ind_biogas_boiler",
    "Solar_Geothermie_Waermepumpen": "ind_heat_pump",
    "Strom": "ind_direct_electric",
    "Kohle": "ind_coal_boiler",
    "Fernwaerme": "HeatExchanger",
    "kein_Energietraeger": None,
}

class Dataset(ABC):
    """
    A Dataset represents a queryable data source with metadata.
    - Physical connection (database, file, etc.)
    - Logical structure (table, column, etc.)
    - Metadata (keys, priority, region)
    """
    def __init__(
        self,
        keys: list[str],
        unit: Optional[UnitEnum] = None,
        priority: int = 10,
        scope: Optional[gpd.GeoDataFrame] = None,
        query_function: Optional[Callable] = None,
    ):
        """
        Args:
            keys: List of data types this dataset can provide
            priority: Priority (higher = preferred), default: 10
            scope: GeoDataFrame with the area the dataset is valid for (None = everywhere)
            query_function: Optional custom query function ``(dataset, topology, query) -> Any``
        """
        if not isinstance(keys, list):
            raise TypeError("Dataset keys must be a list of strings.")
        if not keys:
            raise ValueError("Dataset must have at least one key.")

        self.keys: list[str] = keys
        self.unit: Optional[UnitEnum] = unit
        self.priority: int = priority
        if scope is not None:
            self.scope = scope.dissolve()  # Ensure single geometry
        else:
            self.scope = None
        self._custom_query_function = query_function
        self._crs: Optional[CRS] = None  # Set by registry
        self._scope_geometry = None  # Prepared union of scope, built on first use

        self._registration_order: int = 0  # Set by registry

    @property
    def crs(self) -> Optional[CRS]:
        return self._crs

    def set_crs(self, crs: CRS) -> None:
        """Set the project CRS (done by the DataRegistry on registration). Spatial data is reprojected to it."""
        self._crs = crs
        if self.scope is not None:
            self.scope = self._to_crs(self.scope)
        self._scope_geometry = None

    def _to_crs(self, data: Any) -> Any:
        """Reproject a GeoDataFrame to the project CRS; other data is returned unchanged."""
        if isinstance(data, gpd.GeoDataFrame) and self._crs is not None and data.crs != self._crs:
            return data.to_crs(self._crs)
        return data

    def query(self, topology: "Topology", query: "DataRegistryQuery") -> Any:
        """
        Answers the query for one region topology. Uses the custom query function if provided,
        otherwise calls _default_query method.
        """
        if self._custom_query_function is not None:
            return self._custom_query_function(self, topology, query)
        return self._default_query(query)

    def query_all(self, topologies: Mapping[K, "Topology"], query: "DataRegistryQuery") -> dict[K, Any]:
        """
        Answers the query for many region topologies. The default calls :meth:`query` per topology;
        subclasses can override this for vectorised access (e.g. one spatial join for all regions).
        """
        return {key: self.query(topology, query) for key, topology in topologies.items()}

    def get_data(self) -> pd.DataFrame | gpd.GeoDataFrame:
        """
        Return this dataset's full contents. Subclasses that hold data
        (file, in-memory value) override this; source-backed subclasses that
        can only answer region-scoped questions override `fetch` instead.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement either get_data or fetch"
        )

    def fetch(self, region: gpd.GeoDataFrame, query: "DataRegistryQuery") -> pd.DataFrame | gpd.GeoDataFrame:
        """
        Returns data filtered for region.

        Default is region agnostic. If region awareness is necessary, overwrite in subclasses.
        """
        return self.get_data()

    def _default_query(self, query: "DataRegistryQuery") -> pd.DataFrame | gpd.GeoDataFrame:
        """
        Default query logic. Subclasses should override this method
        if they don't provide a custom query function.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must either provide a query_function "
            "or implement _default_query method"
        )

    @abstractmethod
    def is_available(self) -> bool:
        raise NotImplementedError

    def is_in_region(self, topology: "Topology") -> bool:
        """True if the scope covers every node of the region topology."""
        if self.scope is None:
            return True  # Globally valid

        if self._scope_geometry is None:
            self._scope_geometry = self.scope.geometry.union_all()
            shapely.prepare(self._scope_geometry)
        nodes = list(topology.graph.nodes)
        if not nodes:
            return True
        return bool(shapely.covers(self._scope_geometry, shapely.points(nodes)).all())


class SimpleDataset(Dataset):
    """
    A simple dataset that returns a fixed value for any query
    """
    def __init__(
        self,
        keys: list[str],
        data: any,
        unit: Optional[UnitEnum] = None,
        priority: int = 10,
        scope: Optional[gpd.GeoDataFrame] = None,
    ):
        super().__init__(keys=keys, unit=unit, priority=priority, scope=scope)
        self.data = data

    def set_crs(self, crs: CRS) -> None:
        super().set_crs(crs)
        self.data = self._to_crs(self.data)

    def get_data(self) -> pd.DataFrame | gpd.GeoDataFrame:
        return self.data

    def _default_query(self, query: "DataRegistryQuery") -> pd.DataFrame | gpd.GeoDataFrame:
        return self.data

    def is_available(self) -> bool:
        return True


class PostgresDataset(Dataset):
    def __init__(
        self,
        keys: list[str],
        db_connection: PostgresConnection,
        query_function: Optional[Callable],
        sql: Optional[str] = None,
        unit: Optional[UnitEnum] = None,
        priority: int = 2,
        scope: Optional[gpd.GeoDataFrame] = None,

    ):
        """
        Args:
            keys: List of data types this dataset provides
            db_connection: Database connection
            priority: Dataset priority (higher = preferred)
            scope: Optional GeoDataFrame with the area the dataset is valid for
            query_function: Optional custom query function with signature:
                           func(dataset, topology, query) -> Any
        """
        super().__init__(
            keys=keys,
            unit=unit,
            priority=priority,
            scope=scope,
            query_function=query_function,
        )
        self.sql: str  = sql
        self.db_connection: PostgresConnection = db_connection

    def is_available(self) -> bool:
        """Checks if the database is accessible."""
        return self.db_connection.is_available()

    def fetch(self, region, query):
        if self.sql is None:
            raise ValueError("No sql query provided.")
        return self.execute_spatial_query({"region": region}, self.sql)

    def execute_spatial_query(self, query: dict, sql_query: text) -> pd.DataFrame:
        """
        Helper method to execute a spatial SQL query with region filtering.
        Handles common operations: extracting region, transforming to WKT,
        getting engine, and executing SQL.

        Args:
            query: Query dict containing 'region' key with GeoDataFrame
            sql_query: SQLAlchemy text object with placeholders :wkt and :epsg

        Returns:
            DataFrame with query results
        """
        if isinstance(sql_query, str):
            sql_query = text(sql_query)

        region: gpd.GeoDataFrame = query["region"]
        region_epsg = region.crs.to_epsg()
        region_geom_wkt = region.union_all().wkt

        engine = self.db_connection.get_engine()
        df = pd.read_sql(sql_query, engine, params={"wkt": region_geom_wkt, "epsg": int(region_epsg)})

        return df


class FileDataset(Dataset):
    """
    A file-based dataset with lazy loading capability.
    Data is loaded once on first access and cached for subsequent queries.
    """
    def __init__(
            self,
            keys: list[str],
            file_path: str,
            query_function: Optional[Callable] = None,
            unit: Optional[UnitEnum] = None,
            load_data_kwargs: Optional[dict[str, Any]] = None,
            priority: int = 10,
            scope: Optional[gpd.GeoDataFrame] = None,
    ):
        super().__init__(
            keys=keys,
            unit=unit,
            priority=priority,
            scope=scope,
            query_function=query_function
        )
        self.file_path = Path(file_path)
        self.load_data_kwargs = load_data_kwargs if load_data_kwargs is not None else {}
        self._cached_data: Optional[pd.DataFrame | gpd.GeoDataFrame] = None

    @property
    def data(self) -> pd.DataFrame | gpd.GeoDataFrame:
        """Expose cached data via attribute-style access used by legacy datasets."""
        return self.get_data()

    @property
    def file_path_str(self) -> str:
        return str(self.file_path)

    def set_crs(self, crs: CRS) -> None:
        if crs != self._crs:
            self._cached_data = None  # reload and reproject on next access
        super().set_crs(crs)

    def fetch(self, region, query):
        data = self.get_data()
        if not isinstance(data, gpd.GeoDataFrame):
            # Non-spatial payload (CSV profiles, plain tables): nothing to
            # filter on, so the region does not narrow the result.
            return data
        boundary = gpd.GeoDataFrame(geometry=[region.geometry.union_all()], crs=region.crs)
        return gpd.sjoin(data, boundary, predicate="intersects", how="inner")

    def _load_data(self) -> pd.DataFrame | gpd.GeoDataFrame:
        """
        Load data from file. Override this method in subclasses
        for specific file format handling.
        """
        # Try to detect file type and load accordingly
        if self.file_path_str.endswith('.geojson') or self.file_path_str.endswith('.gpkg') or self.file_path_str.endswith('.shp'):
            return gpd.read_file(self.file_path, **self.load_data_kwargs)
        else:
            return pd.read_csv(self.file_path, **self.load_data_kwargs)

    def get_data(self) -> pd.DataFrame | gpd.GeoDataFrame:
        """
        Returns the loaded data, using lazy loading.
        Data is only loaded (and reprojected to the project CRS) once and then cached.
        """
        if self._cached_data is None:
            self._cached_data = self._to_crs(self._load_data())
        return self._cached_data

    def _default_query(self, query: "DataRegistryQuery") -> pd.DataFrame | gpd.GeoDataFrame:
        """
        Default query returns the loaded data.
        For more complex queries, provide a custom query_function.
        """
        return self.get_data()

    def is_available(self) -> bool:
        import os
        return os.path.exists(self.file_path)

class CSVDataset(FileDataset):
    """
    A CSV file dataset with lazy loading.
    Subclass of FileDataset specifically for CSV files.
    """
    def __init__(
        self,
        keys: list[str],
        file_path: str,
        unit: Optional[UnitEnum] = None,
        pandas_kwargs: Optional[dict[str, Any]] = None,
        priority: int = 10,
        scope: Optional[gpd.GeoDataFrame] = None,
        query_function: Optional[Callable] = None,
    ):
        super().__init__(
            keys=keys,
            file_path=file_path,
            query_function=query_function,
            unit=unit,
            load_data_kwargs=pandas_kwargs,
            priority=priority,
            scope=scope
        )

    def _load_data(self) -> pd.Series:
        """
        Load CSV data and return as a Series (raveled DataFrame).
        """
        df = pd.read_csv(self.file_path, **self.load_data_kwargs)
        s = pd.Series(df.values.ravel())
        return s

    def _default_query(self, query: "DataRegistryQuery") -> pd.Series:
        """
        Default query returns the loaded CSV data as a Series.
        """
        return self.get_data()
