# Generic source-aware dispatch engine

`dispatch.py` provides `DispatchOptimizer` for a grid connection, battery, PV,
fixed demand and optional discrete-power EV charger. It reuses the validated
PuLP solving and result checks in `optim.py`. Neither module imports integrations,
openHAB, device drivers or network clients.

The equipment, tariffs and constraints are supplied by the caller. There is no
default battery size, charging current, location, currency, tariff, export value
or preferred operating mode. Heating can be included in the fixed electrical
load; no thermal storage or compressor behavior is inferred by this model.

## Choose an API

- Use the existing `EnergyOptimizer` when assembling devices through its
  `add_*` methods, including its idealized flexible heating model. Its established
  efficiency convention and charging-based wear accounting remain unchanged.
- Use `DispatchOptimizer` for an explicit source-flow topology with separate
  charge/discharge losses, discharge-based wear, configurable export/curtailment,
  independent charging permissions and a discrete charger. Supply the complete
  input set at construction and build a new instance for each replan.

## Inputs

`Battery` requires capacity and maximum AC charging/discharge power. Optional
fields specify minimum stored energy, terminal reserve, separate efficiencies,
wear per AC kWh discharged and terminal value per stored kWh. Minimum and terminal
reserve are hard constraints. Initial stored energy is passed to the optimizer.

`Grid` requires maximum import and export power. Export capacity bounds the
**combined** PV and battery export. `prices` contains one import price per interval;
`export_prices` may be a scalar or aligned sequence.

`Charger` specifies an AC power step, minimum/maximum active step counts, and
efficiency. Charging is OFF or an integer number of steps. The model does not
assume a voltage or number of phases; an integration converts current settings
to power steps if needed. `EVRequest` supplies initial, required and maximum
stored energy and a deadline interval index. Charging stops after that interval.
The target is hard by default. Supplying a positive `shortfall_penalty` allows a
reported deficit while keeping physical capacity, energy and power bounds hard.
An optional terminal value can make charging beyond the minimum worthwhile.

Power inputs/outputs are interval-average AC kW. Stored and cumulative energy is
kWh. Every monetary parameter uses one consistent caller-selected unit. The
default interval length is one hour; pass `interval_hours=0.25` for quarters.
The engine works with interval indices; timestamp ingestion and alignment belong
to callers. All supplied arrays must be finite and match the horizon.

## Flow capabilities and independent permissions

`FlowPolicy` selects capabilities without referring to an inverter brand:

| Option | Default | Meaning |
| --- | --- | --- |
| `pv_priority_to_load` | False | If True, PV must serve fixed load first; otherwise its allocation is optimized. |
| `allow_battery_export` | False | Permit battery discharge to the grid, within total export capacity. |
| `allow_pv_curtailment` | True | Permit unused PV; disable if the caller cannot execute curtailment. |
| `allow_simultaneous_grid_import_export` | False | Allow separate concurrent import/export accounting only when appropriate for the installation. |

Independent scalar or per-interval boolean masks control `grid_charge_allowed`,
`solar_charge_allowed`, `discharge_allowed` and `ev_availability`. The engine
does not know why a permission is disabled. For example, an application can
disable discharge during an owned charging session without coupling that choice
to PV charging. Permissions default to enabled, subject to equipment limits.

`solar_charge_preference` is an optional scalar/sequence reward per PV kWh
charged, default zero. An application may use it as a small tie-break; the engine
does not impose a particular preference or time weighting.

PV, grid and battery flows obey explicit conservation equations. Battery charging
and discharging cannot overlap. With default flow policy, grid import and export
also cannot overlap. The engine can represent capabilities that a particular
installation lacks: integrations must select the correct capabilities rather than
silently modifying returned power values afterwards.

## Example

```python
from dispatch import Battery, DispatchOptimizer, FlowPolicy, Grid

model = DispatchOptimizer(
    prices=[0.10, 0.40], export_prices=[0.04, 0.08],  # EUR/kWh
    solar_kw=[2, 0], load_kw=[1, 2], initial_kwh=2,
    battery=Battery(capacity_kwh=8, max_charge_kw=3, max_discharge_kw=3,
                    minimum_kwh=1, terminal_kwh=2,
                    charge_efficiency=0.92, discharge_efficiency=0.94,
                    wear_per_discharged_kwh=0.02),
    grid=Grid(max_import_kw=10, max_export_kw=4),
    policy=FlowPolicy(allow_battery_export=False),
    interval_hours=1,
)
model.solve()
result = model.result()
```

`example_dispatch.py` adds an illustrative EV charger with different equipment
parameters. It can run with only the two engine modules and their requirements;
the test suite checks this in a directory containing no integration files.

## Results and solving

`result()` returns `series`, `objective`, and `ev_shortfall_kwh`. Series include
`grid_load_kw`, `grid_charge_kw`, `solar_load_kw`, `solar_charge_kw`,
`solar_export_kw`, `solar_curtailed_kw`, `battery_export_kw`, `discharge_kw`,
`energy_kwh`, `ev_steps`, `ev_power_kw`, and `ev_energy_kwh`, plus the model's
binary direction/on variables. All values are unrounded. `discharge_kw` is total
AC battery output, including `battery_export_kw`; the remainder serves local load.

Battery energy follows:

```text
next_kWh = previous_kWh + dt * (
    (grid_charge_kW + solar_charge_kW) * charge_efficiency
    - discharge_kW / discharge_efficiency
)
```

The objective includes energy import minus export revenue, discharged-energy
wear, any supplied PV charging preference, terminal values and optional EV
shortfall penalty. It is not necessarily the electricity bill alone.

CBC defaults to a 30-second limit, configurable through `solver_seconds`.
Nonoptimal, infeasible and invalid results raise `OptimizationError`; a feasible
timed-out incumbent is not exposed as a successful plan. Custom PuLP solvers may
be passed explicitly and must be bounded by the caller.

## Application boundary

The engine does not fetch telemetry or tariffs, decide manual ownership, validate
data freshness, translate power to device modes, or dispatch commands. Those
belong in integration code. The openHAB example keeps all household settings and
behavior choices under `integrations/openhab/`; other users can supply their own
configuration and consume the generic result without those files.
