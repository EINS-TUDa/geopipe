from dataclasses import dataclass, fields
from pathlib import Path

import pandas as pd

from pypeline.optimization.cesm.io_utils import _units_df
from pypeline.optimization.resolved_system import ResolvedSystem

@dataclass(frozen=True)
class Techmap:
    Units: pd.DataFrame
    Scenario: pd.DataFrame
    Commodity: pd.DataFrame
    ConversionProcess: pd.DataFrame
    ConversionSubProcess: pd.DataFrame
    TSS: pd.DataFrame

    def to_excel(self, path: str):
        # the attribute names are used as sheet names
        with pd.ExcelWriter(path) as writer:
            for field in fields(self):
                df = getattr(self, field.name)
                df.to_excel(writer, sheet_name=field.name, index=False)



def create_techmap(
        resolved: ResolvedSystem,
        techmap_dir: Path
) -> Techmap:
    units_df = _units_df() # TODO: Check what this really is
    scenario =


    techmap = Techmap(
        Units=units_df,
        Scenario=scenario_df,
        Commodity=commodity_df,
        ConversionProcess=conversion_process_df,
        ConversionSubProcess=conversion_subprocess_df,
        TSS=tss_df,
    )

    return techmap
