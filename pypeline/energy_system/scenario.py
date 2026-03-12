# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

Number = Union[int, float]

@dataclass
class Scenario:
    """
    Scenario configuration
    - Years control interpolation of year-dependent inputs.
    - TSS identifies the representative time set (e.g. "4Times", "12Times", "FullYear").
    - CO2 signals can be scalar or a year:value dict and are interpolated per year.
    """
    name: str
    start_year: int
    end_year: int
    year_gap: int
    tss: str = "4Times"
    co2_price: Optional[Union[Number, Dict[int, Number]]] = None
    co2_limit: Optional[Union[Number, Dict[int, Number]]] = None
    rules: List[str] = field(default_factory=list)
    discount_rate: float = 0.05
    retain_existing_output_schedule: Optional[List[float]] = None
    retain_existing_output_drop_per_year: Optional[Number] = None

    def years(self) -> List[int]:
        return list(range(self.start_year, self.end_year + 1, self.year_gap))
