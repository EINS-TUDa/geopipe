"""
Redesigned data management system for pypeline.

Main components:
- DataRegistry: Central management of all Datasets
- Dataset: Abstract base class for datasets
- PostgreSQLTableDataset, PostgreSQLColumnDataset, CSVDataset: Concrete implementations
- DatabaseConnection: Manages shared database connections
"""

from pypeline.data.data_registry import DataRegistry
from pypeline.data.dataset import (
    Dataset,
    PostgreSQLTableDataset,
    PostgreSQLColumnDataset,
    CSVDataset,
)
from pypeline.data.database_connection import DatabaseConnection


__all__ = [
    "DataRegistry",
    "Dataset",
    "PostgreSQLTableDataset",
    "PostgreSQLColumnDataset",
    "CSVDataset",
    "DatabaseConnection",
]
