# Heat pump, heat storage, EV and household integration review

Tested optimizer: PR #10 commit `167b194e8893d7964e5e2d108408b861c1ab92fe`.

## Conclusion

The optimizer can jointly schedule fixed household demand, flexible EV charging,
and a heat pump with **ideal, lossless heat storage and constant COP**. The shared
power balance correctly makes these loads compete for the grid connection and,
optionally, a household battery. It is not yet a calibrated heat-pump/buffer-tank
controller. The thermal model lacks varying COP, storage losses, temperature
limits, and equipment switching/modulation constraints.

## Reproduce

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
python test_household_system.py
```

47 tests passed, including 11 new integration/limitation cases. Tests marked
`test_limitation_*` pass by **demonstrating an unsupported physical behavior**;
their passing does not mean that limitation has been fixed. No production model
code was changed for this review, and no real household telemetry or equipment
was used. Printed solve durations are local observations, not performance gates.

## Synthetic 24-hour test setup

All values below are illustrative, not measurements of the user's equipment.

| Parameter | Assumption |
| --- | --- |
| Resolution | 96 intervals of 15 minutes |
| Grid import limit | 7 kW, no exports |
| Fixed household consumption | 1 kW continuously: 24 kWh/day |
| Heat demand | 6 kW thermal continuously: 144 kWh thermal/day |
| Heat pump | 0–4 kW electrical, constant COP 3, no minimum run time |
| Heat storage | 12 kWh thermal usable capacity; initially 6 kWh; lossless |
| EV charging | Up to 7.2 kW; 14 kWh AC input required by 08:00 |
| EV availability | 00:00–08:00 and 18:00–24:00 |
| Tariff | 00:00–06:00 EUR 0.10/kWh; 06:00–16:00 EUR 0.22; 16:00–22:00 EUR 0.40; 22:00–24:00 EUR 0.12 |
| Optional battery | 10 kWh capacity; initial/final 5 kWh; 4 kW charge/removal rates; 95% modeled efficiency; EUR 0.03/kWh charged wear |

For a fair cost comparison, heat storage finishes at its initial 6 kWh thermal
and the optional battery finishes at its initial 5 kWh. No terminal-value credit
is included in the comparison. The no-storage baseline schedules EV charging
under the same grid limit and maintains constant heating power.

| Configuration | Grid bill | Battery wear | Total modeled daily cost | Grid energy |
| --- | ---: | ---: | ---: | ---: |
| Constant heating + scheduled EV + household | EUR 17.72 | EUR 0.00 | EUR 17.72 | 86.0 kWh |
| Heat storage + scheduled EV + household | EUR 16.56 | EUR 0.00 | EUR 16.56 | 86.0 kWh |
| Heat storage + EV + household + battery | EUR 13.86 | EUR 0.30 | EUR 14.16 | 86.5 kWh |

Heat storage saves EUR 1.16 (6.5%) in this constructed case. With the battery,
combined savings are EUR 3.56 (20.1%) relative to the no-storage baseline.
These are test results, not forecasts of real-world savings or a battery ROI.
The extra 0.5 kWh imported in the battery case is consistent with modeled losses.

All feasible cases supply the full 24 kWh household demand and 144 kWh thermal
heat demand, deliver exactly 14 kWh to EV charging by 08:00, respect EV absence,
keep every interval at or below the 7 kW grid limit, and respect storage bounds.
The negative-price variant also respects these constraints and energy caps.
Reducing the grid limit to 3 kW correctly makes the combined case infeasible:
before 08:00 the grid can supply 24 kWh, while household demand, required heating
after using initial heat, and EV charging together require at least 36 kWh.

## What the current heating API actually models

For constant COP `c`, let:

- `E0` be initially usable stored heat in kWh thermal;
- `Emax` be usable store capacity in kWh thermal;
- `Q[t]` be heat demand in kW thermal;
- `P[t]` be the heat pump's electrical kW.

Use the existing arguments as follows:

```python
hourly_demand = Q / c
tol_cumul_min = -E0 / c
tol_cumul_max = (Emax - E0) / c
```

The independently reconstructed physical state is:

```text
E[t] = E0 + c * sum(P[i] * dt, i <= t) - sum(Q[i] * dt, i <= t)
```

The optimizer's cumulative bounds then enforce `0 <= E[t] <= Emax` for this
ideal store. For the test's 12 kWh thermal store, 6 kWh initial heat and COP 3,
the correct electrical-equivalent tolerances are **-2 and +2 kWh**, not -6/+6
or -12/+12. This represents one aggregate store, not separate room and tank
temperatures, stratification, or distinct space-heating and hot-water circuits.

There is no explicit terminal heat-reserve argument. To compare identical
starting/ending heat, the integration test adds a constraint through PuLP before
solving, using the returned heating variables:

```python
optimizer.problem += pulp.lpSum(hp.values()) * dt == sum(Q) * dt / c
```

Without that equality, the optimizer may rationally spend initial heat to reduce
the bill. `final_energy_value_per_kwh` changes incentives but does not guarantee
a specific reserve. Replanning must update the bounds from measured current
heat storage; reusing the original bounds would implicitly reset the assumed
starting heat on each run.

## Limitations reproduced or established from the API

### 1. Changing COP can produce an invalid thermal schedule

A two-interval counterexample has prices EUR 0.12/0.20 per kWh, COP 2/4, and
1 kWh thermal demanded in the second interval. Passing `Q[t] / COP[t]` as the
electrical demand forecast is **not sufficient** when heating is shifted in time.

The current optimizer buys 0.25 kWh electricity in the first interval for EUR
0.03, producing only **0.5 kWh thermal**, half the requirement. Correctly waiting
would produce the full 1 kWh thermal for EUR 0.05; supplying it early at COP 2
would cost EUR 0.06. A time-varying COP must multiply the scheduled heat-pump
power in the thermal state equation, not just divide the demand forecast.

### 2. Heat losses are absent

The lossless model charges 1 kWh thermal early for a later 1 kWh demand. Replaying
the resulting schedule with an intentionally exaggerated 10% loss per interval
leaves a 0.271 kWh thermal shortage after three storage intervals. This loss rate
is a stress-test parameter, not a claim about a real tank. Known fixed losses
can be folded into demand; state/temperature-dependent losses are not modeled.

### 3. No compressor minimum run/off time or minimum modulation

The 24-hour heat-storage case returned 25 off-to-on transitions when the heat
pump is assumed off before the first interval. This count is solver-dependent:
flat prices permit alternate optimal schedules, and there is no switching
penalty or equipment constraint to select a smoother one.

A smaller test with alternating prices forces a `2, 0, 2, 0` kW schedule over
four quarter-hours. That schedule is incompatible with an illustrative
30-minute minimum run time. The model also allows arbitrarily small nonzero
power. Actual minimum power, run/off times and control interface must be supplied
for the installed equipment before calling schedules executable.

### 4. EV is a controllable electrical load, not a full vehicle model

Availability, power maximum, cumulative deadlines and energy caps work together.
But the returned schedule may request 0.4 kW, as a one-interval test demonstrates.
There is no minimum charging current, discrete power step, phase selection,
charging efficiency, vehicle battery SOC/capacity or energy use while driving.

The 14 kWh test requirement is AC energy drawn by the charger. If 14 kWh must
reach the battery, convert for charging losses or add an explicit EV state model.
Session-specific availability/deadlines can be supplied, but minimum currents
and tapering cannot be guaranteed with this API alone.

### 5. Household/grid guarantees are interval averages

Fixed consumption is enforced in every modeled interval and participates in the
joint power balance. The grid limit is one total average kW limit; there are no
per-phase current constraints or sub-interval peak constraints. A correct model
schedule therefore does not establish protection against transient overloads.

## Recommended next implementation

Introduce an explicit heat-pump/thermal-store device with a thermal energy state:

```text
E[t+1] = E[t] + COP[t] * P_hp[t] * dt - Q_load[t] * dt - heat_loss[t]
```

`heat_loss[t]` is energy lost during that interval, in kWh thermal. Define the
initial state, usable capacity and required final reserve explicitly. Add COP
forecasts, appropriate store heat-loss parameters, and installation-specific
heat-pump minimum power, minimum on/off time and switching costs. Add EV minimum
charging power/steps and AC-to-battery efficiency as required by the charger.

For a buffer tank, usable capacity must be derived from its permitted temperature
range, not its total heat content. For building thermal mass, calibrate the
energy/temperature relationship and heat losses. These require the actual
installation's data; they cannot be established by the synthetic tests here.
