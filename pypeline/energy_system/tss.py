# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import List, Tuple

def four_times_indices(hours_per_year: int = 8760) -> Tuple[List[int], List[int]]:
    """
    Returns (indices, weights) for a simple '4Times' selection:
    - 4 representative *weeks*: ISO weeks 3, 18, 28, 49.
    - Weights sum to 52 (weeks) to conserve annual energy after compression.
    """
    week_offsets = [2, 17, 27, 48]  
    indices: List[int] = []
    for w in week_offsets:
        start = w * 168
        indices.extend(range(start, start + 168))
    weights = [13, 13, 13, 13]
    return indices, weights
