from __future__ import annotations

from pathlib import Path

from pypeline.polygon_builder import (
    AbstractPolygonBuilder,
    PolygonBuildResult,
    PolygonBuilderConfig,
)


class PolygonBuilderFactory:
    """Factory that creates the appropriate AbstractPolygonBuilder for a given mode."""

    @staticmethod
    def create(mode: str, cfg: PolygonBuilderConfig) -> AbstractPolygonBuilder:
        """
        Return a concrete builder for the given mode.

        Each mode requires its own config subtype:

        +------------+-----------------------------+
        | mode       | expected cfg type           |
        +============+=============================+
        | "dijkstra" | DijkstraPolygonBuilderConfig|
        +------------+-----------------------------+

        Raises:
            TypeError: if cfg is not the expected subtype for the given mode.
            ValueError: if mode is unknown.
        """
        from pypeline.polygon_builder.dijkstra_builder import (
            DijkstraPolygonBuilder,
            DijkstraPolygonBuilderConfig,
        )

        mode = mode.lower()
        if mode == "dijkstra":
            if not isinstance(cfg, DijkstraPolygonBuilderConfig):
                raise TypeError(
                    f"Mode 'dijkstra' requires a DijkstraPolygonBuilderConfig, "
                    f"got {type(cfg).__name__}."
                )
            return DijkstraPolygonBuilder(cfg)

        raise ValueError(f"Unknown PolygonBuilder mode: '{mode}'")

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
        return AbstractPolygonBuilder.load(polygons_path, district_street_path)
