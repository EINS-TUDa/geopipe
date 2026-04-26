from dataclasses import dataclass


@dataclass
class Scenario:
    name: str
    start_year: int
    end_year: int
    year_gap: int
    dt_hours: int
    tss: str
    co2_price: float | dict[int, float] | None = None
    co2_limit: float | dict[int, float] | None = None
    discount_rate: float = 0.05

    @property
    def years(self) -> list[int]:
        return list(range(self.start_year, self.end_year + 1, self.year_gap))
