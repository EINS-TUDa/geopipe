"""
Redesigned data management system for geopipe.

Main components:
- DataRegistry: Central management of all Datasets
- Dataset: Abstract base class for datasets
- PostgreSQLTableDataset, PostgreSQLColumnDataset, CSVDataset: Concrete implementations
- DatabaseConnection: Manages shared database connections
"""

from geopipe.data.data_registry import DataRegistry
from geopipe.data.dataset import (
    Dataset,
    PostgreSQLDataset,
    CSVDataset,
)
from geopipe.data.database_connection import DatabaseConnection


__all__ = [
    "DataRegistry",
    "Dataset",
    "PostgreSQLDataset",
    "CSVDataset",
    "DatabaseConnection",
]
