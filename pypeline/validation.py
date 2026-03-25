# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path
from typing import Any, Iterable, Mapping, TypeVar
import math
import numpy as np

K = TypeVar("K")

NUMERIC_ERRORS = (TypeError, ValueError)


def require_float(label: str, value: Any) -> float:
    f = float(value)
    if math.isnan(f):
        raise ValueError(f"{label} is NaN")
    return f


def require_finite(label: str, value: Any, *, allow_zero: bool = True, gt_zero: bool = False) -> float:
    f = require_float(label, value)
    if not math.isfinite(f):
        raise ValueError(f"{label} must be finite")
    if gt_zero and f <= 0.0:
        raise ValueError(f"{label} must be > 0")
    if not allow_zero and f == 0.0:
        raise ValueError(f"{label} must be non-zero")
    return f


def require_positive_int(label: str, value: Any) -> int:
    if value is None:
        raise ValueError(f"{label} is required")
    try:
        iv = int(value)
    except NUMERIC_ERRORS as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if iv <= 0:
        raise ValueError(f"{label} must be > 0")
    return iv


def to_int_id(raw: Any) -> int:
    if hasattr(raw, "iloc"):
        try:
            raw = raw.iloc[0]
        except (IndexError, KeyError, TypeError, AttributeError):
            pass
    if isinstance(raw, np.generic):
        raw = raw.item()
    try:
        return int(raw)
    except NUMERIC_ERRORS:
        try:
            return int(float(raw))
        except NUMERIC_ERRORS as exc:
            raise ValueError(f"Unable to parse integer id from {raw!r}") from exc


def sanitize_price_map(source: dict[str, Any] | None) -> dict[str, float]:
    if source is None:
        return {}
    if not isinstance(source, dict):
        raise ValueError(f"Commodity price map must be a mapping, got {type(source).__name__}: {source!r}")
    out: dict[str, float] = {}
    for key, value in source.items():
        try:
            val = float(value)
        except NUMERIC_ERRORS as exc:
            raise ValueError("Commodity prices must be numeric") from exc
        if not math.isfinite(val):
            raise ValueError("Commodity prices must be finite")
        out[str(key).lower()] = val
    return out


def ensure_tss_indices(tss_file: Path) -> list[int]:
    if not tss_file.exists():
        raise FileNotFoundError(f"TSS file missing: {tss_file}. Provide it via the EnergySystem/Scenario.")
    content = tss_file.read_text(encoding="utf-8").strip()
    return [int(line) for line in content.splitlines() if line.strip()]


def normalize_profile_for_tss(profile_full: np.ndarray, tss_vals: list[int]) -> np.ndarray:
    prof_sum = float(profile_full.sum())
    if prof_sum <= 0:
        raise ValueError("Electricity profile sums to zero; cannot normalize.")
    profile_full = profile_full / prof_sum
    if not tss_vals:
        return profile_full

    max_idx = max(tss_vals)
    if max_idx >= len(profile_full):
        raise ValueError(f"TSS requires index {max_idx} but profile length is {len(profile_full)}.")

    idx_arr = np.asarray(tss_vals, dtype=int) - 1
    selected_sum = float(profile_full[idx_arr].sum())
    if selected_sum <= 0.0:
        raise ValueError("Electricity profile assigns zero weight to selected TSS hours; cannot normalize.")
    if not np.isclose(selected_sum, 1.0):
        profile_full = profile_full / selected_sum

    from decimal import Decimal, ROUND_HALF_UP, getcontext

    getcontext().prec = 28
    quantum = Decimal("0.00000001")
    rounded: list[Decimal] = []
    for pos in idx_arr:
        rounded.append(Decimal(profile_full[pos]).quantize(quantum, rounding=ROUND_HALF_UP))
    correction = Decimal("1.0") - sum(rounded)
    if correction != 0:
        rounded[-1] = (rounded[-1] + correction).quantize(quantum, rounding=ROUND_HALF_UP)
    for pos, dec_val in zip(idx_arr, rounded):
        profile_full[pos] = float(dec_val)
    return profile_full


def assert_fractional(series: Iterable[float], name: str, atol: float = 1e-4) -> None:
    arr = np.asarray(list(series), dtype=float)
    if np.isnan(arr).any():
        raise ValueError(f"{name}: contains NaNs")
    if (arr < -atol).any():
        raise ValueError(f"{name}: contains negative values")
    s = float(arr.sum())
    if not np.isclose(s, 1.0, atol=atol):
        raise ValueError(f"{name}: sum is {s:.6f}, expected 1.0 (±{atol})")


def renormalize_to_one(series: Iterable[float]) -> list[float]:
    arr = np.asarray(list(series), dtype=float)
    s = float(arr.sum())
    if s <= 0:
        raise ValueError("Cannot normalize a non-positive-sum series")
    return list((arr / s).tolist())


def normalize_shares_or_zero(shares: Mapping[K, float]) -> dict[K, float]:
    total_share = float(sum(shares.values()))
    if total_share > 0.0:
        return {key: float(value) / total_share for key, value in shares.items()}
    return {key: 0.0 for key in shares}
