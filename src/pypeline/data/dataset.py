from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Callable
import geopandas as gpd
import pandas as pd
from sqlalchemy import text
from pypeline.data.database_connection import DatabaseConnection
from pypeline.units import UnitEnum

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


def census_category_to_tech(category: str | None) -> str | None:
    if category is None:
        return None
    key = str(category).strip()
    if not key:
        return None
    return CENSUS_HEATING_CATEGORY_TO_TECH.get(key)

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
        regional_validity: Optional[gpd.GeoDataFrame] = None,
        query_function: Optional[Callable] = None,
    ):
        """
        Args:
            keys: List of data types this dataset can provide
            priority: Priority (higher = preferred), default: 10
            regional_validity: GeoDataFrame with validity region
            query_function: Optional custom query function
        """
        if not isinstance(keys, list):
            raise TypeError("Dataset keys must be a list of strings.")
        if not keys:
            raise ValueError("Dataset must have at least one key.")

        self.keys: list[str] = keys
        self.unit: Optional[UnitEnum] = unit
        self.priority: int = priority
        if regional_validity is not None:
            self.regional_validity = regional_validity.dissolve()  # Ensure single geometry
        else:
            self.regional_validity = None
        self._custom_query_function = query_function

        self._registration_order: int = 0  # Set by registry

    def query(self, region: gpd.GeoDataFrame, query: dict) -> pd.DataFrame | gpd.GeoDataFrame:
        """
        Default query implementation that uses custom query function if provided,
        otherwise calls _default_query method.
        """
        if self._custom_query_function is not None:
            return self._custom_query_function(self, region, query)
        return self._default_query(query)

    def _default_query(self, query: dict) -> pd.DataFrame | gpd.GeoDataFrame:
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

    def is_in_region(self, region: gpd.GeoDataFrame) -> bool:
        if self.regional_validity is None:
            return True  # Globally valid

        region_ = region.to_crs(self.regional_validity.crs)
        rv_geom = self.regional_validity.geometry.union_all()
        reg_geom = region_.geometry.union_all()
        return bool(rv_geom.covers(reg_geom))


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
        regional_validity: Optional[gpd.GeoDataFrame] = None,
    ):
        super().__init__(keys=keys, unit=unit, priority=priority, regional_validity=regional_validity)
        self.data = data

    def _default_query(self, query: dict) -> pd.DataFrame | gpd.GeoDataFrame:
        return self.data

    def is_available(self) -> bool:
        return True


class PostgreSQLDataset(Dataset):
    def __init__(
        self,
        keys: list[str],
        db_connection: DatabaseConnection,
        query_function: Optional[Callable],
        unit: Optional[UnitEnum] = None,
        priority: int = 2,
        regional_validity: Optional[gpd.GeoDataFrame] = None,

    ):
        """
        Args:
            keys: List of data types this dataset provides
            db_connection: Database connection
            priority: Dataset priority (higher = preferred)
            regional_validity: Optional GeoDataFrame defining validity region
            query_function: Optional custom query function with signature:
                           func(dataset: PostgreSQLTableDataset, query: dict) -> pd.DataFrame | gpd.GeoDataFrame
        """
        super().__init__(
            keys=keys,
            unit=unit,
            priority=priority,
            regional_validity=regional_validity,
            query_function=query_function,
        )
        self.db_connection = db_connection

    def is_available(self) -> bool:
        """Checks if the database is accessible."""
        return self.db_connection.is_available()

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
            regional_validity: Optional[gpd.GeoDataFrame] = None,
    ):
        super().__init__(
            keys=keys,
            unit=unit,
            priority=priority,
            regional_validity=regional_validity,
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
        Data is only loaded once and then cached.
        """
        if self._cached_data is None:
            self._cached_data = self._load_data()
        return self._cached_data

    def _default_query(self, query: dict) -> pd.DataFrame | gpd.GeoDataFrame:
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
        regional_validity: Optional[gpd.GeoDataFrame] = None,
        query_function: Optional[Callable] = None,
    ):
        super().__init__(
            keys=keys,
            file_path=file_path,
            query_function=query_function,
            unit=unit,
            load_data_kwargs=pandas_kwargs,
            priority=priority,
            regional_validity=regional_validity
        )

    def _load_data(self) -> pd.Series:
        """
        Load CSV data and return as a Series (raveled DataFrame).
        """
        df = pd.read_csv(self.file_path, **self.load_data_kwargs)
        s = pd.Series(df.values.ravel())
        return s

    def _default_query(self, query: dict) -> pd.Series:
        """
        Default query returns the loaded CSV data as a Series.
        """
        return self.get_data()
