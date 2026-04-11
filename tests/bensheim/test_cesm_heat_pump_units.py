import math
import sqlite3
from pathlib import Path

import pytest


def hp_units_whole_t():
    """Use the shipped Bensheim run to ensure discrete units imply repeated base CAPEX."""
    repo_root = Path(__file__).resolve().parents[2]
    db_path = repo_root / "CESM" / "Runs" / "Bensheim-Base4twk" / "db.sqlite"
    if not db_path.exists():
        pytest.skip("Reference CESM run missing (CESM/Runs/Bensheim-Base4twk/db.sqlite)")

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    query = """
        SELECT cp.name AS tech,
               y.value AS year,
               p.cap_max,
               p.capex_cost_base,
               pcs.max_units,
               o.cap_new
        FROM conversion_process cp
        JOIN conversion_subprocess cs ON cs.cp_id = cp.id
        JOIN param_cs_y p ON p.cs_id = cs.id
        JOIN year y ON y.id = p.y_id
        JOIN output_cs_y o ON o.cs_id = cs.id AND o.y_id = y.id
        LEFT JOIN param_cs pcs ON pcs.cs_id = cs.id
        WHERE cp.name LIKE 'cen_heat_pump%'
          AND o.cap_new > 0
        ORDER BY cp.name, y.value
    """
    rows = con.execute(query).fetchall()
    con.close()

    assert rows, "Expected at least one heat pump row with investment activity"

    per_unit_caps = set()
    base_costs = set()
    for row in rows:
        per_unit_cap = float(row["cap_max"])
        cap_new = float(row["cap_new"])
        base_cost = float(row["capex_cost_base"])
        per_unit_caps.add(round(per_unit_cap, 6))
        base_costs.add(round(base_cost, 2))

        assert per_unit_cap > 0.0
        assert base_cost > 0.0

        units = cap_new / per_unit_cap
        rounded_units = round(units)
        assert math.isclose(units, rounded_units, rel_tol=1e-9, abs_tol=1e-9)

        max_units = row["max_units"]
        if max_units is not None:
            assert max_units >= rounded_units

    assert len(per_unit_caps) == 1, "Expected a consistent per-unit cap for central heat pumps"
    assert len(base_costs) == 1, "Expected a consistent per-unit base CAPEX"
