# IMEON household planning and openHAB integration

This module extracts the installation-specific planning model into the library.
The existing openHAB/HABApp rule still owns forecasts, measured state, manual EV
ownership, battery preservation, recovery, dispatch, readback and publication.
Neither the planner nor the adapter performs network or device operations.

## Units and policy

All power is average AC kW per interval; battery/EV energy is stored kWh.
Default monetary settings and prices are **ct/kWh**, matching the household rule.
If using EUR/kWh, convert every monetary config field (including terminal value,
wear, EV value/shortfall penalty and the solar tie-break) consistently.

Defaults reproduce the saved household policy:

- 12 kWh battery, 2.4 kWh minimum and hard 7 kWh terminal reserve;
- 4 kW AC charging, 5 kW AC discharge, 22 kW grid import;
- 95% charging and 95% discharge efficiency; wear once, at 3.368 ct/discharged kWh;
- 9 ct/kWh PV export and terminal stored-energy value;
- EV charging OFF or integer 6–32 A at 230 V, with 90% efficiency;
- mandatory EV stored-energy target by a deadline; any infeasible shortfall is
  explicitly returned and penalized, while capacity and current limits remain hard;
- additional EV charging valued at 15 ct/AC kWh, accounting for charging losses;
- CBC time limit of 30 seconds; only proven optimal, validated schedules returned.

PV first serves fixed household load. The remaining PV is allocated to battery,
EV or export. Battery discharge can serve loads/EV, never grid export. The battery
cannot charge and discharge in the same interval. Heating is included in fixed
load; no idealized heat-storage scheduling is sent to the heat pump.

`battery_preservation` disables both discharge and PV battery charging for selected
intervals, matching the existing IDLE/PV_NO_CHARGE policy. Economical grid charging
remains available. The openHAB caller must compute this mask from actual EV state
and active preservation requests; a planner does not infer control ownership.

The default `allow_grid_charge_while_exporting=True` reproduces the existing
model's separate accounting for grid charging and PV export. This is an explicit
policy assumption, not evidence of how the meter bills simultaneous physical
flows. Set it to `False` to prohibit simultaneous grid import/PV export. Verify
the settlement convention and physical mode behavior before changing live control.
This choice does not enable battery export under either setting.

## Library example

```python
from household import HouseholdOptimizer, EVRequest

model = HouseholdOptimizer(
    prices=[5] * 24 + [30] * 24,  # cents/kWh
    solar_kw=[0] * 48,
    load_kw=[0.8] * 48,
    initial_kwh=8,
    ev=EVRequest(initial_kwh=13, minimum_kwh=14.48,
                 capacity_kwh=18.1, deadline_index=41),
    battery_preservation=[True] * 4 + [False] * 44,
)
model.solve()
result = model.result()  # unrounded arrays, objective, EV shortfall
```

`EVRequest` uses the end of `deadline_index` as its deadline. An optional
`ev_availability` 0/1 mask further restricts charging. Charging never continues
past the deadline. Integer-current constraints can leave unused capacity or a
small shortfall when a full minimum-current interval cannot fit. No post-solve
rounding to a supposedly executable power is used to hide that situation.

`HouseholdConfig` is immutable and validates bounds, efficiencies and limits.
Construct a new model for each replan. Custom PuLP solvers can be passed to
`solve(solver=...)`; callers are responsible for bounding custom solver runtime.

## openHAB adapter

`imeon_adapter.optimize` accepts the saved rule's argument order and returns
`(rows, terminal_value, ev_result)`. It translates the current EV input dictionary
into stored-energy constraints and returns the existing schedule fields and modes.
Mode selection uses unrounded powers; only presentation values are rounded.
It preserves expensive-period self-consumption support for cloud/load uncertainty.

The adapter requires consecutive UTC epoch timestamps on full 900-second
boundaries and a timezone-aware EV deadline within the forecast horizon. UTC
alignment avoids ambiguous/missing local clock hours at daylight-saving changes.
A partial interval is not modeled; the existing controller must handle execution
timing. Fetching an openHAB snapshot is not itself proof of device-data freshness.

The returned tuple is a proposal. Keep the existing pre/post-solve state checks,
controller ownership and recovery locks, EV/heat-pump dispatch policy, delayed
mode verification, fail-safe behavior and dispatch receipts in the rule. Keep
publication outside the calculation lock. Continue monitoring per-phase currents
in the smartmeter integration; an average total kW constraint is not a per-phase
or transient overload guard.

## Offline comparison

Run from this repository, with the PR's development requirements installed:

```sh
python -m pytest -q
python compare_openhab.py \
  --legacy-source /path/to/saved/imeon_quarter_hour_optimizer.py \
  --plan /path/to/saved/optimizer_15m.json \
  --output /tmp/household-comparison.json
```

`--plan` is optional. The script always runs six synthetic cases and can also
replay saved plan inputs. It extracts only `optimize()` and its constant dependencies
from a **trusted local** source file. It does not import HABApp, instantiate a
Rule, or execute startup/dispatch/publication. It is not a Python security sandbox.

Both solvers must produce proven optimal, constraint-valid results. The report
compares raw objectives within 0.001 ct and separately reports first-mode changes,
mode/current differences and battery-state differences. Matching objectives do
not mean matching schedules: flat prices can admit many equivalent solutions.
The script exits nonzero for an objective mismatch or failed solve. Replaying
saved rounded forecasts compares two new solves; it does not reconstruct the
original unrounded live solve. Household configuration is taken from the baseline's
referenced constants, using the same mapping as the generated rule candidate.

The September 13 local comparison against saved source SHA-256
`633a25cc1ae9476a2fa1f0b7507a0873ab64510f136b0be645bdbbe258dad4d1`
matched all seven objectives. The archived 135-interval case matched every mode
and battery state. Flat-price synthetic cases differed in some charging times,
including the first mode of the negative-price case. These differences need
observation in shadow operation before a live planner switch.

## Prepare a reviewable rule candidate

```sh
python make_openhab_candidate.py \
  --source /path/to/saved/imeon_quarter_hour_optimizer.py \
  --output /tmp/imeon_quarter_hour_optimizer.candidate.py
```

This writes a separate file and refuses to overwrite an existing output. Only
the `optimize` method is replaced by a call to the library; the existing numerical
constants are passed into `HouseholdConfig`. The modules `optim.py`, `household.py`
and `imeon_adapter.py` must be importable in HABApp's Python environment before
activating that candidate. No deployment is performed by this command.

The local candidate was structurally checked to preserve all other Python code,
and its return values matched the standalone adapter across the same seven cases.
The comparison covers saved/synthetic inputs; it does not validate new hardware
commands, actual price settlement, or current device availability.
