# NewlyInstalledUnits, `cap_max_unit`, and base costs

This page documents the integer-unit investment formulation that the
**pypeline branch of CESM** adds on top of the continuous capacity
expansion formulation in `CESM/main`. It is not part of upstream
`CESM/main` — anyone reading the upstream model will not find the
`NewlyInstalledUnits` variable, the `build_activation` constraint, or
the `capex_cost_base` / `cap_max_unit` parameters there.

The relevant code lives in `CESM/src/cesm/core/model.py` and the
schema in `CESM/src/cesm/core/init_queries.sql`.

## Motivation

The pypeline branch adds an explicit per-unit investment decision so
that:

1. installing a new unit incurs a **fixed cost** (`capex_cost_base`)
   on top of the size-proportional cost (`capex_cost_power`),
2. the size of each newly built unit is **capped from above**
   (`cap_max_unit`), so building 1 GW of a technology that comes in
   100 MW blocks requires 10 units, and
3. the per-unit decision is **integer** (or binary, see below)

## Inputs

Two `param_cs_y` / `param_cs` columns drive the formulation. Both
default to values that make the formulation a no-op, so existing
techmaps continue to behave like upstream `CESM/main` until the user
sets them.

| Parameter          | Index   | Default | Meaning                                                                                  |
| ------------------ | ------- | ------- | ---------------------------------------------------------------------------------------- |
| `capex_cost_base`  | (CS, Y) | `0`     | Fixed investment cost incurred per newly installed unit, in the model's currency unit.   |
| `cap_max_unit`     | (CS)    | `1e6`   | Maximum capacity (in power units) of a single newly installed unit. Sentinel = no limit. |

The `1e6` default for `cap_max_unit` is a **sentinel**, not a real
cap — see [Binary fallback](#binary-fallback) for what it triggers.

## The `base_cost_cs` set

`NewlyInstalledUnits` and `build_activation` are only defined for
conversion subprocesses that actually have a non-zero base cost. The
DAO exposes this as the set
`conversion_subprocess_base_costs` (referred to in the code as
`base_cost_cs`):

```sql
SELECT DISTINCT cp.name, cin.name, cout.name
FROM param_cs_y
JOIN conversion_subprocess AS cs ON cs_id = cs.id
...
WHERE capex_cost_base > 0;
```

A CS is included iff at least one `(cs, y)` row has
`capex_cost_base > 0`. Setting `capex_cost_base = 0` for every year
keeps a CS out of the set entirely, so it pays no fixed cost and is
not subject to the unit-size cap — the model behaves exactly like
upstream for that CS.

## Decision variable

$$
\mathrm{NewlyInstalledUnits}_{cs, y} \in \mathbb{Z}_{\ge 0}
\qquad \forall\, cs \in \mathrm{base\_cost\_cs},\ y \in \mathrm{year}
$$

Defined in `_add_var` (`model.py:53`).
Integer by default; downgraded to **binary** for any CS whose
`cap_max_unit` is left at the \(10^{6}\) sentinel (see below).

## Constraint: `build_activation`

$$
\mathrm{Cap\_new}_{cs, y}
\;\le\;
\mathrm{cap\_max\_unit}_{cs}
\cdot
\mathrm{NewlyInstalledUnits}_{cs, y}
\qquad \forall\, cs \in \mathrm{base\_cost\_cs},\ y \in \mathrm{year}
$$

Source: `_add_constr` (`model.py:109`).

This is the link between the continuous capacity variable and the
integer unit-count variable. Three regimes:

- **\(\mathrm{NewlyInstalledUnits} = 0\)** \(\Rightarrow\)
  \(\mathrm{Cap\_new} \le 0\), i.e. no capacity may be added in year
  \(y\) (\(\mathrm{Cap\_new} \ge 0\) is implicit from Gurobi
  defaults).
- **\(\mathrm{NewlyInstalledUnits} = k > 0\)** \(\Rightarrow\)
  \(\mathrm{Cap\_new} \le k \cdot \mathrm{cap\_max\_unit}\), i.e.
  \(k\) units' worth of capacity may be added.
- **No constraint in the other direction.** The model is free to pay
  for \(k\) units and use less than \(k \cdot \mathrm{cap\_max\_unit}\)
  of capacity. The fixed cost still gets paid in full — the optimiser
  will only do this if it is cheaper than the alternative (typically
  it is not).

The constraint is only added for `cs ∈ base_cost_cs`. Subprocesses
without a base cost have `Cap_new` unconstrained from above by this
mechanism, exactly as in upstream `CESM/main`. Other caps (`cap_max`)
still apply.

### Binary fallback

If a user enables base costs (\(\mathrm{capex\_cost\_base} > 0\)) but
does **not** set a meaningful `cap_max_unit`, the parameter takes its
default \(10^{6}\). In that case we know nothing about the size of
one unit, so `build_activation` would let `NewlyInstalledUnits` blow
up to large integers without changing the feasible region of
`Cap_new`. To keep the integer domain small, the variable is
downgraded to binary (`vtype = GRB.BINARY`) per \((cs, y)\):

```python
if cap_max_unit == 1e6:
    for y in get_set("year"):
        vars["NewlyInstalledUnits"][cs, y].vtype = GRB.BINARY
```

Source: `model.py:59-65`.

Effect: a binary "did we build *anything* in year \(y\)?" decision
that turns the fixed cost into a one-shot charge, while leaving
`Cap_new` otherwise unconstrained by `build_activation` (since
\(\mathrm{Cap\_new} \le 10^{6} \cdot 1\) is non-binding for any
realistic input). This makes the formulation behave like a classic
fixed-charge-on-investment model when the unit size is unknown.

> **Practical note.** If you care about unit count, always set
> `cap_max_unit` explicitly. Leaving the default makes
> `NewlyInstalledUnits` a 0/1 indicator, not a unit count, and the
> `output_cs_y.newly_installed_units` column will reflect that.

## Cost contributions

### CAPEX

`build_activation` only ties `Cap_new` to `NewlyInstalledUnits`; the
**fixed cost itself** is added to the CAPEX expression in
`_add_constr` (`model.py:97-102`):

$$
\begin{aligned}
\mathrm{CAPEX} \;=\;
& \sum_{cs,\, y} d(y) \cdot \mathrm{Cap\_new}_{cs, y} \cdot \mathrm{capex\_cost\_power}_{cs, y} \\
& {} + \sum_{\substack{cs \in \mathrm{base\_cost\_cs} \\ y}} d(y) \cdot \mathrm{NewlyInstalledUnits}_{cs, y} \cdot \mathrm{capex\_cost\_base}_{cs, y} \\
& {} - \mathrm{TotalSalvageValue}
\end{aligned}
$$

where \(d(y)\) is the discount factor returned by
`DAO.get_discount_factor(y)`. The first sum is the upstream term;
the second sum is the addition introduced by this branch.

### Salvage value

Salvage is computed in `salvage_value_rule` per \((cs, y)\) for any
year \(y\) that is within `technical_lifetime` of the planning
horizon. For CSs in `base_cost_cs`, the per-unit fixed cost is
salvaged on the same straight-line basis as the size-proportional
cost
(`model.py:146-153`):

$$
r(cs, y) \;=\; 1 - \frac{y_{\mathrm{last}} - y + 1}{\mathrm{technical\_lifetime}_{cs}}
$$

$$
\begin{aligned}
\mathrm{Salvage}(cs, y) \;=\;
& \mathrm{Cap\_new}_{cs, y} \cdot \mathrm{capex\_cost\_power}_{cs, y} \cdot r(cs, y) \\
& {} + \mathbb{1}_{\{cs \in \mathrm{base\_cost\_cs}\}} \cdot \mathrm{NewlyInstalledUnits}_{cs, y} \cdot \mathrm{capex\_cost\_base}_{cs, y} \cdot r(cs, y)
\end{aligned}
$$

The whole term is then discounted to the last planning year. Without
this addition (i.e. on `CESM/main`), the model would *charge* the
fixed cost in the year of installation but *never refund* its unused
remainder at the horizon, biasing late-horizon investments against
base-cost technologies.

## Output

`NewlyInstalledUnits` is persisted to the `output_cs_y` table via the
`newly_installed_units` column (added on this branch; default `0`).
For CSs outside `base_cost_cs`, `0` is written:

```python
newly_installed_units = (
    vars['NewlyInstalledUnits'][cs, y].X
    if cs in base_cost_cs_set
    else 0
)
```

Source: `model.py:488`.

## Quick sanity checklist

- A CS that should keep upstream behaviour: leave `capex_cost_base`
  unset (or `0`) for every year. It is not added to `base_cost_cs`,
  no `NewlyInstalledUnits` variable is created, and no
  `build_activation` constraint is added.
- A CS that should be modelled as discrete blocks of a known size:
  set both `capex_cost_base > 0` and `cap_max_unit` to the block
  size. `NewlyInstalledUnits` is integer.
- A CS that should be modelled with a fixed-charge on investment but
  no per-block size: set `capex_cost_base > 0` and leave
  `cap_max_unit` at the default. `NewlyInstalledUnits` is binary.
