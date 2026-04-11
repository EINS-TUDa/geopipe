from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from pypeline.data.dataset import CensusTechnology, FileDataset


_CENSUS_COL_TO_TECH: dict[str, CensusTechnology] = {
    "Gas": CensusTechnology.Gas,
    "Heizoel": CensusTechnology.Oil,
    "Holz_Holzpellets": CensusTechnology.Wood,
    "Biomasse_Biogas": CensusTechnology.Biomass,
    "Solar_Geothermie_Waermepumpen": CensusTechnology.Renewable,
    "Strom": CensusTechnology.Electric,
    "Kohle": CensusTechnology.Coal,
    "Fernwaerme": CensusTechnology.District_Heating,
    "kein_Energietraeger": CensusTechnology.NoEnergyCarrier,
}


class Census2022HeatingType100mGrid(FileDataset):
    """Compatibility wrapper for legacy census heating-shares queries."""

    def __init__(
        self,
        file_path: str | Path = "data/Census2022HeatingType100mGrid/Census2022HeatingType100mGrid_Polygons_southhessen.geojson",
    ) -> None:
        super().__init__(
            keys=["heating_shares", "residential_heat_technology_shares"],
            file_path=str(file_path),
            priority=6,
            regional_validity=None,
        )

    def _default_query(self, query: dict[str, Any]) -> dict[CensusTechnology, float]:
        key = str(query.get("key", "")).strip()
        if key not in {"heating_shares", "residential_heat_technology_shares"}:
            raise ValueError(
                "Census2022HeatingType100mGrid only supports keys "
                "'heating_shares' or 'residential_heat_technology_shares'"
            )

        region = query.get("region")
        if not isinstance(region, gpd.GeoDataFrame) or region.empty:
            raise ValueError("Query requires a non-empty GeoDataFrame under 'region'")

        census = self.get_data()
        if not isinstance(census, gpd.GeoDataFrame):
            raise TypeError("Expected GeoDataFrame census source")

        region_in = region if census.crs == region.crs else region.to_crs(census.crs)
        joined = gpd.sjoin(census, region_in[["geometry"]], predicate="intersects", how="inner")

        tech_amounts: dict[CensusTechnology, float] = {}
        for src_col, tech in _CENSUS_COL_TO_TECH.items():
            values = pd.to_numeric(joined.get(src_col), errors="coerce").fillna(0.0)
            tech_amounts[tech] = float(values.sum())

        total = float(sum(tech_amounts.values()))
        if total <= 0.0:
            shares = {tech: 0.0 for tech in _CENSUS_COL_TO_TECH.values()}
        else:
            shares = {tech: float(amount / total) for tech, amount in tech_amounts.items()}

        name_mapping = query.get("name_mapping")
        if isinstance(name_mapping, dict) and name_mapping:
            mapped: dict[Any, float] = {}
            for old_key, new_key in name_mapping.items():
                if new_key is None:
                    continue
                mapped[new_key] = float(mapped.get(new_key, 0.0) + shares.get(old_key, 0.0))
            mapped_total = float(sum(mapped.values()))
            if mapped_total > 0.0:
                mapped = {k: float(v / mapped_total) for k, v in mapped.items()}
            return mapped

        return shares


class WaermeatlasHessen(FileDataset):
    """Compatibility wrapper for legacy Waermeatlas residential heat queries."""

    def __init__(
        self,
        file_path: str | Path = "data/WaermeatlasHessen.gpkg",
        layer: str = "WAH_Punkte",
    ) -> None:
        super().__init__(
            keys=["residential_heat", "residential_heat_demand"],
            file_path=str(file_path),
            priority=6,
            regional_validity=None,
            load_data_kwargs={"layer": layer},
        )

    def _default_query(self, query: dict[str, Any]) -> float:
        key = str(query.get("key", "")).strip()
        if key not in {"residential_heat", "residential_heat_demand"}:
            raise ValueError("WaermeatlasHessen only supports keys 'residential_heat' or 'residential_heat_demand'")

        region = query.get("region")
        if not isinstance(region, gpd.GeoDataFrame) or region.empty:
            raise ValueError("Query requires a non-empty GeoDataFrame under 'region'")

        wah = self.get_data()
        if not isinstance(wah, gpd.GeoDataFrame):
            raise TypeError("Expected GeoDataFrame Waermeatlas source")

        region_in = region if wah.crs == region.crs else region.to_crs(wah.crs)
        joined = gpd.sjoin(wah, region_in[["geometry"]], predicate="within", how="inner")

        if "qnutzwaerme_2020_kwh" not in joined.columns:
            raise ValueError("Waermeatlas layer is missing 'qnutzwaerme_2020_kwh'")

        return float(pd.to_numeric(joined["qnutzwaerme_2020_kwh"], errors="coerce").fillna(0.0).sum())


__all__ = ["Census2022HeatingType100mGrid", "CensusTechnology", "WaermeatlasHessen"]
