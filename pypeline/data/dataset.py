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
    ):
        """
        Args:
            keys: List of data types this dataset can provide
            priority: Priority (higher = preferred), default: 10
            crs: Coordinate reference system (e.g., "EPSG:25832")
            regional_validity: GeoDataFrame with validity region
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

        self._registration_order: int = 0  # Set by registry

    @abstractmethod
    def query(self, query: dict) -> pd.DataFrame | gpd.GeoDataFrame:
        raise NotImplementedError

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
        super().__init__(keys=keys, unit=unit ,priority=priority, crs=crs, regional_validity=regional_validity)
        self.data = data

    def query(self, query: dict) -> pd.DataFrame | gpd.GeoDataFrame:
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
        )

        if (schema is None or table is None) and query_function is None:
            raise ValueError("Either schema and table must be provided, or a query_function must be provided.")

        self.db_connection = db_connection
        self.schema = schema
        self.table = table
        self.geometry_column = geometry_column
        self._custom_query_function = query_function

    def query(self, query: dict) -> pd.DataFrame | gpd.GeoDataFrame:
        """
        Executes a query on the table. If a custom query_function was provided, it will be used instead.
        """
        # Use custom query function if provided
        if self._custom_query_function is not None:
            return self._custom_query_function(self, query)

        # Default query logic
        return self._default_query(query)

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



class CSVDataset(Dataset):
    """
    Dataset for CSV files.
    """

    def __init__(
        self,
        keys: list[str],
        file_path: str,
        unit: Optional[UnitEnum] = None,
        pandas_kwargs: Optional[dict[str, Any]] = None,
        priority: int = 10,
        regional_validity: Optional[gpd.GeoDataFrame] = None,
    ):
        """
        Args:
            keys: List of data types this dataset provides
            file_path: Path to CSV file
            priority: Dataset priority
        """
        super().__init__(keys=keys, unit=unit, priority=priority, regional_validity=regional_validity)
        self.file_path = file_path
        self.pandas_kwargs = pandas_kwargs or {}


    def query(self, query: dict) -> pd.Series:
        """
        Reads the CSV file and optionally filters.

        Args:
            query: Dictionary with parameters:
                - key: str (required)
                - columns: list[str] (optional)
                - filters: dict (optional)

        Returns:
            DataFrame
        """
        df = pd.read_csv(self.file_path, **self.pandas_kwargs)
        s = pd.Series(df.values.ravel())
        return s

    def is_available(self) -> bool:
        """Checks if the CSV file exists."""
        import os
        return os.path.exists(self.file_path)
