"""
Simplified Dataset - combines DataSource and Dataset into one concept.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional
import geopandas as gpd
import pandas as pd
from sqlalchemy import create_engine, text
from pypeline.data.database_connection import DatabaseConnection


class Dataset(ABC):
    """
    A Dataset represents a queryable data source with metadata.

    Combines:
    - Physical connection (database, file, etc.)
    - Logical structure (table, column, etc.)
    - Metadata (keys, priority, region)
    """

    def __init__(
        self,
        keys: list[str],
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
        self.priority: int = priority
        self.crs: Optional[str] = crs
        self.regional_validity: Optional[gpd.GeoDataFrame] = regional_validity
        self._registration_order: int = 0  # Set by registry

    @abstractmethod
    def query(self, query: dict) -> pd.DataFrame | gpd.GeoDataFrame:
        """
        Executes a query on this dataset.

        Args:
            query: Dictionary with query parameters (at least 'key')

        Returns:
            Query result as DataFrame or GeoDataFrame
        """
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        """Checks if the dataset is accessible."""
        raise NotImplementedError

    def is_in_region(self, region: gpd.GeoDataFrame) -> bool:
        """
        Checks if this dataset is valid for a specific region.

        Returns:
            True if dataset is valid in the region
        """
        if self.regional_validity is None:
            return True  # Globally valid

        # Check if regions intersect
        return self.regional_validity.intersects(region.unary_union).any()


class PostgreSQLTableDataset(Dataset):
    """
    Dataset for a PostgreSQL table.
    """

    def __init__(
        self,
        keys: list[str],
        db_connection: DatabaseConnection,
        schema: str,
        table: str,
        geometry_column: Optional[str] = None,
        priority: int = 10,
        crs: Optional[str] = None,
        regional_validity: Optional[gpd.GeoDataFrame] = None,
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
        """
        super().__init__(
            keys=keys,
            priority=priority,
            crs=crs,
            regional_validity=regional_validity,
        )

        self.db_connection = db_connection
        self.schema = schema
        self.table = table
        self.geometry_column = geometry_column

    def query(self, query: dict) -> pd.DataFrame | gpd.GeoDataFrame:
        """
        Executes a query on the table.

        Args:
            query: Dictionary with parameters:
                - key: str (required)
                - region: gpd.GeoDataFrame (optional)
                - columns: list[str] (optional, default: all)
                - filters: dict (optional, WHERE conditions)

        Returns:
            DataFrame or GeoDataFrame
        """
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
            region_wkt = region.unary_union.wkt
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


class PostgreSQLColumnDataset(Dataset):
    """
    Dataset for a specific column in a PostgreSQL table.

    Useful when different columns represent different information types
    (e.g., Wärmeatlas Hessen with multiple heat demand columns).
    """

    def __init__(
        self,
        keys: list[str],
        db_connection: DatabaseConnection,
        schema: str,
        table: str,
        column: str,
        geometry_column: Optional[str] = None,
        priority: int = 10,
        crs: Optional[str] = None,
        regional_validity: Optional[gpd.GeoDataFrame] = None,
    ):
        """
        Args:
            keys: List of data types this dataset provides
            db_connection: Database connection
            schema: Database schema name
            table: Table name
            column: Column name with relevant data
            geometry_column: Optional, name of geometry column
            priority: Dataset priority
            crs: Optional, CRS string
            regional_validity: Optional GeoDataFrame defining validity region
        """
        super().__init__(
            keys=keys,
            priority=priority,
            crs=crs,
            regional_validity=regional_validity,
        )

        self.db_connection = db_connection
        self.schema = schema
        self.table = table
        self.column = column
        self.geometry_column = geometry_column

    def query(self, query: dict) -> pd.DataFrame | gpd.GeoDataFrame:
        """
        Executes a query on the specific column.

        Args:
            query: Dictionary with parameters:
                - key: str (required)
                - region: gpd.GeoDataFrame (optional)
                - aggregation: str (optional, e.g., "SUM", "AVG")
                - additional_columns: list[str] (optional)

        Returns:
            DataFrame or GeoDataFrame
        """
        aggregation = query.get("aggregation")
        additional_columns = query.get("additional_columns", [])
        region = query.get("region")

        # Build columns
        if aggregation:
            cols = [f'{aggregation}("{self.column}") as "{self.column}"']
        else:
            cols = [f'"{self.column}"']

        if self.geometry_column and self.geometry_column not in cols:
            cols.append(f'"{self.geometry_column}"')

        cols.extend(f'"{c}"' for c in additional_columns)

        sql = f'SELECT {", ".join(cols)} FROM "{self.schema}"."{self.table}"'

        # Spatial filter
        if region is not None and self.geometry_column:
            region_wkt = region.unary_union.wkt
            region_srid = region.crs.to_epsg() if region.crs else 4326
            sql += (
                f' WHERE ST_Intersects("{self.geometry_column}", '
                f"ST_GeomFromText('{region_wkt}', {region_srid}))"
            )

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
        priority: int = 10,
    ):
        """
        Args:
            keys: List of data types this dataset provides
            file_path: Path to CSV file
            priority: Dataset priority
        """
        super().__init__(keys=keys, priority=priority)
        self.file_path = file_path

    def query(self, query: dict) -> pd.DataFrame:
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
        df = pd.read_csv(self.file_path)

        # Filter columns
        columns = query.get("columns")
        if columns:
            df = df[columns]

        # Filter rows
        filters = query.get("filters")
        if filters:
            for col, value in filters.items():
                df = df[df[col] == value]

        return df

    def is_available(self) -> bool:
        """Checks if the CSV file exists."""
        import os
        return os.path.exists(self.file_path)

