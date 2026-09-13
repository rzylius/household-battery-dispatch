# Household battery dispatch

Optimize grid imports/exports, a household battery, solar production, EV charging
and heating with a mixed-integer linear model using PuLP and CBC.

## Install and run

```sh
python -m pip install -r requirements.txt
python example_15min.py
```

The example uses synthetic 15-minute prices and forecasts. It imports the actual
optimizer; it does not contain another copy of the optimization logic.

## 15-minute intervals

```python
from optim import EnergyOptimizer

# One chronological, aligned price/forecast value per interval.
prices = [0.10, 0.15, 0.30, 0.20]  # EUR/kWh, four 15-minute intervals
optimizer = EnergyOptimizer(n_intervals=len(prices), interval_hours=0.25)
optimizer.add_mains_electricity_supply(
    "grid", max_import_power=10, import_hourly_prices=prices,
)
optimizer.add_fixed_consumption("house", hourly_consumption=[4, 4, 4, 4])
status = optimizer.solve()
series = optimizer.get_time_series()
imported_kwh = series["grid"]["import"].sum() * optimizer.interval_hours
# 4 kW * 0.25 h * 4 intervals = 4 kWh, costing EUR 0.75.
```

| Input/output | Unit and meaning |
| --- | --- |
| `n_intervals` | Number of consecutive equal-duration intervals |
| `interval_hours` | Duration in hours: `0.25` for 15 minutes, `1.0` for hourly |
| Grid, consumption, solar, heating power | Average kW during each interval |
| Battery `charge_rate` | Nonnegative charging kW |
| Battery `discharge_rate` | Nonpositive stored-energy removal rate in kW; see efficiency convention below |
| Capacity, initial SOC, SOC bounds, returned SOC | kWh; returned SOC is at the **end** of each interval |
| EV cumulative requirements/caps, heating tolerances | kWh, not kW |
| Prices, wear cost, terminal energy value | One consistent currency unit per kWh, e.g. EUR/kWh |
| Objective | Same currency unit as prices, including wear and terminal-value adjustment |

The optimizer multiplies power by interval duration in **all** energy and cost
calculations. Power balance and kW limits do not get multiplied by duration.
Terminal energy is already in kWh, so its value is not scaled again.

If forecasts contain **kWh per interval**, divide them by `interval_hours` to
obtain average kW before passing them in. Do not divide prices by four.
Convert EUR/MWh prices to EUR/kWh by dividing by 1000. Use import/export prices
that reflect your actual contract and marginal charges.

A normal 24-hour day contains 96 quarter-hour intervals. Use `len(prices)` rather
than assuming 96: a local daylight-saving transition can produce 92 or 100.
The optimizer does not parse timestamps or download prices. The caller must
align all arrays to consecutive intervals, using timezone-aware timestamps
(preferably UTC), and reject missing/duplicate instants or stale forecasts.
If starting mid-interval, align execution to the next full interval or supply a
separate controller for the partial interval; this API uses one fixed duration.

### Compatibility

`EnergyOptimizer(24)` and `EnergyOptimizer(n_hours=24)` retain one-hour intervals.
The historical `n_hours`/`hours` attributes alias the interval count/index range,
not elapsed hours. Prefer `n_intervals`/`intervals` in new code.

Existing method argument names such as `import_hourly_prices`,
`hourly_consumption`, `estimated_hourly_production`, and `hourly_demand` remain
accepted. They now explicitly mean **one value per configured interval**.
For hourly data, average kW and interval kWh have identical numeric values.

## Battery model

Charging and discharging are mutually exclusive within each interval. The
historical efficiency convention is preserved to avoid silently changing
existing integrations: charge losses are zero in the model and the entire
round-trip loss is assigned to discharge.

```text
SOC_next = SOC_previous + (charge_rate + discharge_rate) * interval_hours
AC discharge power = -discharge_rate * efficiency
```

`max_discharge_power` limits stored-energy removal, not delivered AC output.
Do not send the stored-energy discharge rate directly to an AC-power control
interface. This remains an approximation of measured battery SOC and losses;
separate charge/discharge efficiencies and calibration are future work.

`cost_of_cycle_kwh` is charged per **kWh charged**. Terminal energy is valued at
`SOC_final * efficiency * final_energy_value_per_kwh`. The objective therefore
is not the electricity bill alone. A minimum final reserve can be required via
the last entry in `min_soc`. All SOC inputs are kWh, not fractions or percentages.

## EV and heating constraints

`add_flexible_consumption` accepts cumulative kWh requirements by the **end** of
each interval. The historical keyword `min_cumulative_consuption` is retained.
Zero requirements at later intervals do not reset previously consumed energy.

```python
optimizer.add_flexible_consumption(
    "ev", max_power=4,
    min_cumulative_consuption=[0, 0, 1, 1],
    max_cumulative_consumption=1,
    availability=[False, True, True, False],
)
```

If the upper cap is omitted, it defaults to the largest minimum requirement.
This intentionally prevents unnecessary consumption at negative prices. Set a
larger scalar or per-interval cap explicitly when additional charging is useful
and physically possible. Availability defaults to all intervals.

Heating demand is electrical kW per interval; cumulative tolerances remain
electrical kWh at every resolution. The heating model approximates thermal
storage and does not explicitly model temperature, varying COP or increased
heat loss when the house is warmer. Solar production may be curtailed by the
model, which requires corresponding hardware control.

## Solver and dispatch handling

`solve()` returns `"Optimal"` only after checking solver status, finite values,
variable bounds/integrality and constraint feasibility (tolerance `1e-5`).
Infeasible, failed and nonoptimal solves raise `OptimizationError`.
`get_time_series()` refuses to expose a failed or unsolved schedule.

```python
from optim import OptimizationError

try:
    optimizer.solve()
    dispatch = optimizer.get_time_series()
except OptimizationError:
    # The surrounding controller must apply its configured fallback.
    dispatch = None
```

An optional PuLP solver can be passed to `solve(solver=...)`. A time-limited
feasible incumbent is not accepted as optimal. Re-solving an unchanged model
does not duplicate constraints. Create a new optimizer when forecasts, measured
SOC or devices change; adding devices after solving is rejected.

This repository plans schedules; it does not operate an inverter, refresh
forecasts or implement the surrounding fallback/control loop.

## Tests

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Regression cases cover hourly/quarter-hour equivalence, battery losses and
terminal value, grid/export and wear costs, EV deadlines/caps/availability,
heating energy tolerances, negative prices, invalid inputs and failed solves.
