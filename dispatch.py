"""Generic source-aware electricity dispatch. No installation or control policy.

AC power is kW, stored energy is kWh, and prices use one caller-selected currency
unit per kWh. All equipment sizes and tariffs come from the caller. Results are
unrounded flows and energy states, never device commands or operating modes.
"""

from dataclasses import dataclass, fields
from numbers import Integral

import numpy as np
import pulp

from optim import EnergyOptimizer, OptimizationError


def _validate_numbers(instance, *, positive=(), signed=(), optional=()):
    for field in fields(instance):
        value = getattr(instance, field.name)
        if field.name in optional and value is None:
            continue
        value = EnergyOptimizer._number(value, field.name, positive=field.name in positive,
                                        minimum=None if field.name in signed else 0)
        object.__setattr__(instance, field.name, value)


@dataclass(frozen=True)
class Battery:
    capacity_kwh: float
    max_charge_kw: float
    max_discharge_kw: float
    minimum_kwh: float = 0.0
    terminal_kwh: float = 0.0
    charge_efficiency: float = 1.0
    discharge_efficiency: float = 1.0
    wear_per_discharged_kwh: float = 0.0
    terminal_value: float = 0.0

    def __post_init__(self):
        _validate_numbers(self, positive=("capacity_kwh", "charge_efficiency", "discharge_efficiency"),
                          signed=("terminal_value",))
        if max(self.minimum_kwh, self.terminal_kwh) > self.capacity_kwh:
            raise ValueError("Battery minimum/terminal energy exceeds capacity")
        if max(self.charge_efficiency, self.discharge_efficiency) > 1:
            raise ValueError("Battery efficiencies must be in (0, 1]")


@dataclass(frozen=True)
class Grid:
    max_import_kw: float
    max_export_kw: float

    def __post_init__(self):
        _validate_numbers(self)


@dataclass(frozen=True)
class EVRequest:
    initial_kwh: float
    minimum_kwh: float
    capacity_kwh: float
    deadline_index: int  # Target by the END of this interval.


@dataclass(frozen=True)
class Charger:
    """Discrete AC power: OFF or min_steps..max_steps times power_step_kw.

    E.g. power_step_kw=0.230 expresses 1 A steps at 230 V single phase;
    power_step_kw=0.690 expresses 1 A steps across three 230 V phases.
    The engine itself has no voltage, phase-count or current assumptions.
    None shortfall_penalty makes the EV target hard; a positive value permits
    explicit penalized shortfall. Terminal value is per stored EV kWh.
    """

    power_step_kw: float
    min_steps: int
    max_steps: int
    efficiency: float = 1.0
    terminal_value: float = 0.0
    shortfall_penalty: float | None = None

    def __post_init__(self):
        for name in ("min_steps", "max_steps"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        _validate_numbers(self, positive=("power_step_kw", "efficiency", "shortfall_penalty"),
                          signed=("terminal_value",), optional=("shortfall_penalty",))
        object.__setattr__(self, "min_steps", int(self.min_steps))
        object.__setattr__(self, "max_steps", int(self.max_steps))
        if self.min_steps > self.max_steps or self.efficiency > 1:
            raise ValueError("Require min_steps <= max_steps and efficiency <= 1")


@dataclass(frozen=True)
class FlowPolicy:
    """Caller-selected topology/capabilities, independent of device brands."""

    pv_priority_to_load: bool = False
    allow_battery_export: bool = False
    allow_pv_curtailment: bool = True
    allow_simultaneous_grid_import_export: bool = False

    def __post_init__(self):
        if not all(isinstance(getattr(self, f.name), bool) for f in fields(self)):
            raise ValueError("Flow policies must be booleans")


class DispatchOptimizer(EnergyOptimizer):
    """Plan a grid, battery, PV, fixed demand and optional discrete EV charger.

    Independent per-interval permission masks control grid charging, PV charging,
    discharge, and EV availability. Any installation-specific reason for those
    permissions belongs to the caller. Heating can be included in fixed demand;
    no thermal equipment behavior is inferred.
    """

    def __init__(self, prices, solar_kw, load_kw, initial_kwh, *, battery, grid,
                 export_prices=0.0, interval_hours=1.0, policy=None, charger=None,
                 ev=None, ev_availability=None, grid_charge_allowed=None,
                 solar_charge_allowed=None, discharge_allowed=None,
                 solar_charge_preference=0.0, solver_seconds=30.0):
        super().__init__(n_intervals=len(prices), interval_hours=interval_hours)
        self.battery = b = battery
        self.grid = g = grid
        self.policy = p = policy or FlowPolicy()
        self.charger = charger
        self.solver_seconds = self._number(solver_seconds, "solver_seconds", positive=True)
        self.prices = self._series(prices, "prices").copy()
        self.export_prices = self._series(export_prices, "export_prices", scalar=True).copy()
        self.solar_kw = self._series(solar_kw, "solar_kw", minimum=0).copy()
        self.load_kw = self._series(load_kw, "load_kw", minimum=0).copy()
        preference = self._series(solar_charge_preference, "solar_charge_preference", scalar=True)
        self.initial_kwh = self._number(initial_kwh, "initial_kwh", minimum=0)
        if self.initial_kwh > b.capacity_kwh:
            raise ValueError("Initial battery energy exceeds capacity")
        grid_allowed = self.permission_mask(grid_charge_allowed, "grid_charge_allowed")
        solar_allowed = self.permission_mask(solar_charge_allowed, "solar_charge_allowed")
        discharge_allowed = self.permission_mask(discharge_allowed, "discharge_allowed")
        available = self.permission_mask(ev_availability, "ev_availability")
        if (ev is None) != (charger is None):
            raise ValueError("Provide EV request and charger together")
        if ev is None and ev_availability is not None:
            raise ValueError("EV availability requires an EV request")
        self.ev = ev = self._validate_ev(ev)

        def series(name, maximum=None, binary=False):
            return self._new_time_series("dispatch", name, lowBound=0,
                                         upBound=maximum, binary=binary)

        grid_load = series("grid_load_kw", g.max_import_kw)
        grid_charge = series("grid_charge_kw", b.max_charge_kw)
        solar_load = series("solar_load_kw")
        solar_charge = series("solar_charge_kw", b.max_charge_kw)
        solar_export = series("solar_export_kw", g.max_export_kw)
        solar_curtailed = series("solar_curtailed_kw")
        discharge = series("discharge_kw", b.max_discharge_kw)
        battery_export = series("battery_export_kw", g.max_export_kw if p.allow_battery_export else 0)
        energy = series("energy_kwh", b.capacity_kwh)
        direction = series("battery_direction", binary=True)
        solar_ev = series("solar_ev_kw")
        steps = series("ev_steps", charger.max_steps if charger else 0)
        for variable in steps.values():
            variable.cat = pulp.LpInteger
        ev_power = series("ev_power_kw")
        ev_on = series("ev_on", binary=True)
        ev_energy = series("ev_energy_kwh", ev.capacity_kwh if ev else 0)
        slack_max = ev.minimum_kwh if ev and charger.shortfall_penalty is not None else 0
        self.ev_shortfall = pulp.LpVariable("ev_minimum_shortfall_kwh", 0, slack_max)
        grid_direction = None
        if not p.allow_simultaneous_grid_import_export:
            grid_direction = series("grid_direction", binary=True)

        for i in self.intervals:
            self.problem += solar_load[i] <= self.load_kw[i]
            if p.pv_priority_to_load:
                self.problem += solar_load[i] == min(self.solar_kw[i], self.load_kw[i])
            self.problem += grid_load[i] + discharge[i] - battery_export[i] + solar_ev[i] + solar_load[i] == self.load_kw[i] + ev_power[i]
            self.problem += solar_load[i] + solar_charge[i] + solar_export[i] + solar_ev[i] + solar_curtailed[i] == self.solar_kw[i]
            self.problem += solar_ev[i] <= ev_power[i]
            self.problem += battery_export[i] <= discharge[i]
            self.problem += grid_load[i] + grid_charge[i] <= g.max_import_kw
            self.problem += solar_export[i] + battery_export[i] <= g.max_export_kw
            self.problem += grid_charge[i] + solar_charge[i] <= b.max_charge_kw * direction[i]
            self.problem += discharge[i] <= b.max_discharge_kw * (1 - direction[i])
            if b.max_charge_kw == 0 or b.max_discharge_kw == 0:
                self.problem += direction[i] == int(b.max_charge_kw > 0)
            if grid_direction is not None:
                self.problem += grid_load[i] + grid_charge[i] <= g.max_import_kw * grid_direction[i]
                self.problem += solar_export[i] + battery_export[i] <= g.max_export_kw * (1 - grid_direction[i])
                if g.max_import_kw == 0 or g.max_export_kw == 0:
                    self.problem += grid_direction[i] == int(g.max_import_kw > 0)
            if not p.allow_pv_curtailment:
                self.problem += solar_curtailed[i] == 0
            for allowed, flow in ((grid_allowed, grid_charge), (solar_allowed, solar_charge),
                                  (discharge_allowed, discharge)):
                if not allowed[i]:
                    self.problem += flow[i] == 0
            previous = self.initial_kwh if i == 0 else energy[i - 1]
            self.problem += energy[i] == previous + self.interval_hours * (
                (grid_charge[i] + solar_charge[i]) * b.charge_efficiency
                - discharge[i] / b.discharge_efficiency)
            self.problem += energy[i] >= b.minimum_kwh
            if ev:
                self.problem += ev_power[i] == steps[i] * charger.power_step_kw
                self.problem += steps[i] >= charger.min_steps * ev_on[i]
                self.problem += steps[i] <= charger.max_steps * ev_on[i]
                if i > ev.deadline_index or not available[i]:
                    self.problem += ev_on[i] == 0
                previous_ev = ev.initial_kwh if i == 0 else ev_energy[i - 1]
                self.problem += ev_energy[i] == previous_ev + ev_power[i] * charger.efficiency * self.interval_hours
            else:
                self.problem += ev_on[i] == 0
                self.problem += steps[i] == 0
                self.problem += ev_power[i] == 0
                self.problem += ev_energy[i] == 0
        self.problem += energy[self.n_intervals - 1] >= b.terminal_kwh
        if ev:
            self.problem += ev_energy[ev.deadline_index] + self.ev_shortfall >= ev.minimum_kwh
        self._add_cost(pulp.lpSum(
            ((grid_load[i] + grid_charge[i]) * self.prices[i]
             - (solar_export[i] + battery_export[i]) * self.export_prices[i]
             + discharge[i] * b.wear_per_discharged_kwh
             - solar_charge[i] * preference[i]) * self.interval_hours for i in self.intervals)
            - energy[self.n_intervals - 1] * b.terminal_value)
        # Include the fixed-zero slack too, so solver readback always defines it.
        self._add_cost(self.ev_shortfall * (charger.shortfall_penalty if ev and charger.shortfall_penalty is not None else 1))
        if ev:
            self._add_cost(-ev_energy[ev.deadline_index] * charger.terminal_value)

    def permission_mask(self, values, name):
        result = self._series(1 if values is None else values, name, scalar=True)
        if not np.all((result == 0) | (result == 1)):
            raise ValueError(f"{name} must contain only booleans or 0/1")
        return result.astype(bool)

    def _validate_ev(self, ev):
        if ev is None:
            return None
        initial = self._number(ev.initial_kwh, "EV initial_kwh", minimum=0)
        minimum = self._number(ev.minimum_kwh, "EV minimum_kwh", minimum=0)
        capacity = self._number(ev.capacity_kwh, "EV capacity_kwh", positive=True)
        if max(initial, minimum) > capacity:
            raise ValueError("EV initial/required energy exceeds capacity")
        index = ev.deadline_index
        if isinstance(index, bool) or not isinstance(index, Integral) or not 0 <= index < self.n_intervals:
            raise ValueError("EV deadline_index must identify an interval in the horizon")
        return EVRequest(initial, minimum, capacity, int(index))

    def solve(self, solver=None):
        return super().solve(solver if solver is not None else
                             pulp.PULP_CBC_CMD(msg=False, timeLimit=self.solver_seconds))

    def _validate_solution(self):
        super()._validate_solution()
        value = self.ev_shortfall.varValue
        if value is None or not np.isfinite(value) or not self.ev_shortfall.valid(1e-5):
            raise OptimizationError("Solver returned invalid EV shortfall")

    def result(self):
        """Return validated, unrounded flows and states; no device commands."""
        return {"series": self.get_time_series()["dispatch"],
                "objective": float(pulp.value(self.total_cost)),
                "ev_shortfall_kwh": float(self.ev_shortfall.value())}
