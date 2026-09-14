"""Timeseries files for CESM.

CESM reads every timeseries from a single folder as ``<name>.txt``. The demand profiles of the energy system are
written there; the static timeseries the techmap references (time-step set, central technology profiles) are
copied there from the backend's ``timeseries_dir``.
"""
import hashlib
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import pandas as pd

from ...energy_system.demand import Demand


def profile_hash(profile: pd.Series) -> str:
    """Content hash identifying a profile."""
    return hashlib.sha1(profile.to_numpy().tobytes()).hexdigest()


def profile_names(demands: dict[int, tuple[Demand, ...]]) -> dict[str, str]:
    """Name each distinct demand profile: ``<demand>`` if the demand's profile is identical in all regions,
    otherwise ``<demand>_<region id>``. A profile shared by several demands keeps the first name.
    Returns profile hash -> name."""
    hashes: dict[str, dict[int, str]] = defaultdict(dict)
    for region_id, region_demands in demands.items():
        for demand in region_demands:
            hashes[demand.name][region_id] = profile_hash(demand.profile)
    names = {}
    for demand_name, hash_per_region in hashes.items():
        identical = len(set(hash_per_region.values())) == 1
        for region_id, hash_ in hash_per_region.items():
            names.setdefault(hash_, demand_name if identical else f"{demand_name}_{region_id}")
    return names


def write_profiles(demands: dict[int, tuple[Demand, ...]], names: dict[str, str], directory: Path) -> None:
    """Clear ``directory`` and write each named profile once as a space-separated ``<name>.txt``."""
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.iterdir():
        if path.is_file():
            path.unlink()
    for region_demands in demands.values():
        for demand in region_demands:
            path = directory / f"{names[profile_hash(demand.profile)]}.txt"
            if not path.exists():
                path.write_text(" ".join(f"{value:.10g}" for value in demand.profile))


def copy_static_timeseries(names: Iterable[str], source_dir: Path, directory: Path) -> None:
    """Copy the timeseries ``<name>.txt`` from ``source_dir`` to ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        source = source_dir / f"{name}.txt"
        if not source.exists():
            raise FileNotFoundError(f"Timeseries '{name}' not found in {source_dir}.")
        shutil.copyfile(source, directory / source.name)
