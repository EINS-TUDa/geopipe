from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Tuple

import geopandas as gpd


# Return type for PolygonBuilder.build(): (polygons, district_street_segments)
PolygonBuildResult = Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]


class PolygonBuildError(RuntimeError):
    """Raised when polygon building fails."""
    pass


class AbstractPolygonBuilder(ABC):
    """Interface for all polygon builders."""

    @abstractmethod
    def build(self) -> PolygonBuildResult:
        """Build polygons and return (polygons, district_street_segments)."""
        raise NotImplementedError

    @staticmethod
    def load(polygons_path: Path, district_street_path: Path) -> PolygonBuildResult:
        """
        Load previously built polygons and street segments from file.

        Args:
            polygons_path: path to the saved polygons GeoJSON file.
            district_street_path: path to the saved district street segments GeoJSON file.

        Returns:
            (polygons, district_street_segments) as GeoDataFrames.
        """
        if not polygons_path.exists():
            raise FileNotFoundError(f"Polygons file not found: {polygons_path}")
        if not district_street_path.exists():
            raise FileNotFoundError(f"District street segments file not found: {district_street_path}")
        return gpd.read_file(polygons_path), gpd.read_file(district_street_path)


