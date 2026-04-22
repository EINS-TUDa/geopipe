from dataclasses import dataclass


@dataclass
class Pipe:
    region_id_in: int
    region_id_out: int
    pipe_length_km: float
    pipe_capex_base_eur: float = 0.0
    below_distance_threshold: bool = False
    pipe_loss_fraction: float = 0.02
    pipe_capex_eur_per_mw: float = 0.0
    pipe_opex_eur_per_mwh: float = 0.0
    pipe_cap_max_mw: float = 500.0
    pipe_cap_max_mwh: float = None
    pipe_lifetime_years: int = 40

    def __post_init__(self) -> None:
        if self.below_distance_threshold:
            self.pipe_capex_base_eur = 0.0
            self.pipe_capex_eur_per_mw = 0.0
            self.pipe_opex_eur_per_mwh = 0.0
