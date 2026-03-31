from __future__ import annotations

import geopandas as gpd
import pandas as pd


def detect_fernwaerme_column(columns: list[str]) -> str:
    candidates = [col for col in columns if "fern" in col.lower()]
    if not candidates:
        raise ValueError(f"No Fernwärme-like column found in heating shares: {columns}")
    return candidates[0]


def detect_id_column(columns: list[str]) -> str | None:
    normalized = {col.lower(): col for col in columns}
    for key in ("id", "district_id", "region_id"):
        if key in normalized:
            return normalized[key]
    return None


def resolve_fernwaerme_share_by_district(
    polygons: gpd.GeoDataFrame,
    heating_shares: gpd.GeoDataFrame,
) -> dict[int, float]:
    shares = heating_shares.copy()
    fern_col = detect_fernwaerme_column(list(shares.columns))
    shares["_fern_raw"] = pd.to_numeric(shares[fern_col], errors="coerce").fillna(0.0)

    max_val = float(shares["_fern_raw"].max()) if not shares.empty else 0.0
    scale = 100.0 if max_val > 1.0 else 1.0
    shares["_fern_share"] = shares["_fern_raw"].clip(lower=0.0) / scale

    id_col = detect_id_column(list(shares.columns))
    if id_col is not None:
        shares["_district_id"] = pd.to_numeric(shares[id_col], errors="coerce").fillna(-1).astype(int)
        agg = shares.groupby("_district_id", dropna=True)["_fern_share"].mean()
        return {int(idx): float(val) for idx, val in agg.items() if int(idx) >= 0 and float(val) > 0.0}

    if shares.crs != polygons.crs:
        shares = shares.to_crs(polygons.crs)

    joined = gpd.sjoin(
        shares[["_fern_share", "geometry"]],
        polygons[["id", "geometry"]],
        how="left",
        predicate="within",
    )

    if joined["id"].isna().any():
        unresolved = joined[joined["id"].isna()][["_fern_share", "geometry"]]
        nearest = gpd.sjoin_nearest(
            unresolved,
            polygons[["id", "geometry"]],
            how="left",
        )
        joined.loc[joined["id"].isna(), "id"] = nearest["id"].values

    joined["_district_id"] = pd.to_numeric(joined["id"], errors="coerce").fillna(-1).astype(int)
    agg = joined.groupby("_district_id", dropna=True)["_fern_share"].mean()
    return {int(idx): float(val) for idx, val in agg.items() if int(idx) >= 0 and float(val) > 0.0}


def resolve_fernwaerme_districts(
    polygons: gpd.GeoDataFrame,
    heating_shares: gpd.GeoDataFrame,
) -> list[int]:
    shares = resolve_fernwaerme_share_by_district(polygons, heating_shares)
    return sorted(int(did) for did, share in shares.items() if float(share) > 0.0)
