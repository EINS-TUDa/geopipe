from abc import ABC, abstractmethod
from typing import Any, Optional, Callable
import geopandas as gpd
import pandas as pd
from sqlalchemy import create_engine, text
from pypeline.data.database_connection import DatabaseConnection
from pypeline.energy_system.unit import UnitEnum


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
        crs: Optional[str] = None,
        regional_validity: Optional[gpd.GeoDataFrame] = None,
        query_function: Optional[Callable] = None,
    ):
        """
        Args:
            keys: List of data types this dataset can provide
            priority: Priority (higher = preferred), default: 10
            crs: Coordinate reference system (e.g., "EPSG:25832")
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
        self.crs: Optional[str] = crs
        self.regional_validity = regional_validity
        self._custom_query_function = query_function

        self._registration_order: int = 0  # Set by registry

    def query(self, query: dict) -> pd.DataFrame | gpd.GeoDataFrame:
        """
        Default query implementation that uses custom query function if provided,
        otherwise calls _default_query method.
        """
        if self._custom_query_function is not None:
            return self._custom_query_function(self, query)
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
        return self.regional_validity.contains(region_)[0]


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
        crs: Optional[str] = None,
        regional_validity: Optional[gpd.GeoDataFrame] = None,
    ):
        super().__init__(keys=keys, unit=unit, priority=priority, crs=crs, regional_validity=regional_validity)
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
        unit: Optional[UnitEnum] = None,
        schema: Optional[str] = None,
        table: Optional[str] = None,
        geometry_column: Optional[str] = None,
        priority: int = 2,
        crs: Optional[str] = None,
        regional_validity: Optional[gpd.GeoDataFrame] = None,
        query_function: Optional[Callable] = None,
    ):
        """
        Args:
            keys: List of data types this dataset provides
            db_connection: Database connection
            schema: Database schema name
            table: Table name
            geometry_column: Optional, name of geometry column
            priority: Dataset priority (higher = preferred)
            crs: Optional, CRS string (e.g., "EPSG:25832")
            regional_validity: Optional GeoDataFrame defining validity region
            query_function: Optional custom query function with signature:
                           func(dataset: PostgreSQLTableDataset, query: dict) -> pd.DataFrame | gpd.GeoDataFrame
        """
        super().__init__(
            keys=keys,
            unit=unit,
            priority=priority,
            crs=crs,
            regional_validity=regional_validity,
            query_function=query_function,
        )

        if (schema is None or table is None) and query_function is None:
            raise ValueError("Either schema and table must be provided, or a query_function must be provided.")

        self.db_connection = db_connection
        self.schema = schema
        self.table = table
        self.geometry_column = geometry_column

    def _default_query(self, query: dict) -> pd.DataFrame | gpd.GeoDataFrame:
        columns = query.get("columns")
        filters = query.get("filters")
        region = query.get("region")

        # Build SQL query
        cols = ", ".join(f'"{c}"' for c in columns) if columns else "*"
        sql = f'SELECT {cols} FROM "{self.schema}"."{self.table}"'

        # Build WHERE conditions
        where_clauses = []

        if filters:
            for col, value in filters.items():
                if isinstance(value, str):
                    where_clauses.append(f'"{col}" = \'{value}\'')
                else:
                    where_clauses.append(f'"{col}" = {value}')

        # Spatial filter if region is specified
        if region is not None and self.geometry_column:
            region_wkt = region.union_all().wkt
            region_srid = region.crs.to_epsg() if region.crs else 4326
            where_clauses.append(
                f'ST_Intersects("{self.geometry_column}", '
                f"ST_GeomFromText('{region_wkt}', {region_srid}))"
            )

        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)

        # Execute query
        engine = self.db_connection.get_engine()
        if self.geometry_column:
            return gpd.read_postgis(sql, engine, geom_col=self.geometry_column)
        else:
            return pd.read_sql(sql, engine)

    def is_available(self) -> bool:
        """Checks if the database is accessible."""
        return self.db_connection.is_available()


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
            pandas_kwargs: Optional[dict[str, Any]] = None,
            priority: int = 10,
            crs: Optional[str] = None,
            regional_validity: Optional[gpd.GeoDataFrame] = None,
    ):
        super().__init__(
            keys=keys,
            unit=unit,
            priority=priority,
            crs=crs,
            regional_validity=regional_validity,
            query_function=query_function
        )
        self.file_path = file_path
        self.pandas_kwargs = pandas_kwargs or {}
        self._cached_data: Optional[pd.DataFrame | gpd.GeoDataFrame] = None
        self._is_loaded = False

    def _load_data(self) -> pd.DataFrame | gpd.GeoDataFrame:
        """
        Load data from file. Override this method in subclasses
        for specific file format handling.
        """
        # Try to detect file type and load accordingly
        if self.file_path.endswith('.geojson') or self.file_path.endswith('.gpkg') or self.file_path.endswith('.shp'):
            return gpd.read_file(self.file_path, **self.pandas_kwargs)
        else:
            return pd.read_csv(self.file_path, **self.pandas_kwargs)

    def get_data(self) -> pd.DataFrame | gpd.GeoDataFrame:
        """
        Returns the loaded data, using lazy loading.
        Data is only loaded once and then cached.
        """
        if not self._is_loaded:
            self._cached_data = self._load_data()
            self._is_loaded = True
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
            pandas_kwargs=pandas_kwargs,
            priority=priority,
            regional_validity=regional_validity
        )

    def _load_data(self) -> pd.Series:
        """
        Load CSV data and return as a Series (raveled DataFrame).
        """
        df = pd.read_csv(self.file_path, **self.pandas_kwargs)
        s = pd.Series(df.values.ravel())
        return s

    def _default_query(self, query: dict) -> pd.Series:
        """
        Default query returns the loaded CSV data as a Series.
        """
        return self.get_data()
