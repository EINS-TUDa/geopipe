# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Iterable
import numpy as np

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
