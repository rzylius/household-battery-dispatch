"""Household planning with explicit source flows; no device or network access.

Power is AC kW; battery and EV state are stored kWh. All monetary parameters
must use the same currency unit. Defaults reproduce the saved IMEON household
policy in ct/kWh. Heating remains part of the fixed household forecast.
"""

from dataclasses import dataclass
from numbers import Integral

import numpy as np
import pulp

from optim import EnergyOptimizer, OptimizationError


@dataclass(frozen=True)
class HouseholdConfig:
    capacity_kwh: float = 12.0
    minimum_kwh: float = 2.4
    terminal_kwh: float = 7.0
    charge_kw: float = 4.0
    discharge_kw: float = 5.0
    grid_import_kw: float = 22.0
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.95
    wear_per_discharged_kwh: float = 2425 * 100 / (12 * 6000)
    export_value: float = 9.0
    terminal_value: float = 9.0
    early_solar_tie_break: float = 0.001
    ev_voltage: float = 230.0
    ev_min_current_a: int = 6
    ev_max_current_a: int = 32
    ev_efficiency: float = 0.90
    ev_optional_price: float = 15.0
    ev_shortfall_penalty: float = 10000.0
    solver_seconds: float = 30.0
    # Reproduces the current source-accounting policy. This does NOT establish
    # whether a particular installation settles gross or net meter energy.
    allow_grid_charge_while_exporting: bool = True

    def __post_init__(self):
        positive = {"capacity_kwh", "charge_efficiency", "discharge_efficiency",
                    "ev_voltage", "ev_efficiency", "ev_shortfall_penalty", "solver_seconds"}
        signed = {"export_value", "terminal_value", "ev_optional_price"}
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if name == "allow_grid_charge_while_exporting":
                if not isinstance(value, bool):
                    raise ValueError(f"{name} must be boolean")
                continue
            if name in {"ev_min_current_a", "ev_max_current_a"}:
                if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
                    raise ValueError(f"{name} must be a positive integer")
                continue
            value = EnergyOptimizer._number(value, name, positive=name in positive,
                                            minimum=None if name in signed else 0)
            object.__setattr__(self, name, value)
        if not 0 <= self.minimum_kwh <= self.terminal_kwh <= self.capacity_kwh:
            raise ValueError("Require 0 <= minimum <= terminal <= capacity")
        if max(self.charge_efficiency, self.discharge_efficiency, self.ev_efficiency) > 1:
            raise ValueError("Efficiencies must be in (0, 1]")
        if self.ev_min_current_a > self.ev_max_current_a:
            raise ValueError("EV minimum current exceeds maximum")


@dataclass(frozen=True)
class EVRequest:
    initial_kwh: float
    minimum_kwh: float
    capacity_kwh: float
    deadline_index: int  # Required by the END of this interval.


class HouseholdOptimizer(EnergyOptimizer):
    """One immutable-input replan; a new object is required for new telemetry.

    PV serves fixed household demand first. Only surplus PV may charge the
    battery, charge the EV, or export; battery energy cannot reach export.
    `battery_preservation` disables discharge AND solar charging in selected
    intervals, matching IDLE/PV_NO_CHARGE; economical grid charging remains.
    The caller derives these flags from ownership/recovery/EV state.
    """

    def __init__(self, prices, solar_kw, load_kw, initial_kwh, *,
                 config=None, interval_hours=0.25, ev=None,
                 ev_availability=None, battery_preservation=None):
        super().__init__(n_intervals=len(prices), interval_hours=interval_hours)
        self.config = c = config or HouseholdConfig()
        self.prices = self._series(prices, "prices").copy()
        self.solar_kw = self._series(solar_kw, "solar_kw", minimum=0).copy()
        self.load_kw = self._series(load_kw, "load_kw", minimum=0).copy()
        self.initial_kwh = self._number(initial_kwh, "initial_kwh", minimum=0)
        if self.initial_kwh > c.capacity_kwh:
            raise ValueError("Initial battery energy exceeds capacity")
        self.preserved = self._flags(battery_preservation, "battery_preservation", 0)
        available = self._flags(ev_availability, "ev_availability", 1)
        if ev is None and ev_availability is not None:
            raise ValueError("EV availability requires an EV request")
        self.ev = ev = self._validate_ev(ev)

        def series(name, maximum=None, binary=False):
            return self._new_time_series("household", name, lowBound=0,
                                         upBound=maximum, binary=binary)

        grid_load = series("grid_load_kw", c.grid_import_kw)
        grid_charge = series("grid_charge_kw", c.charge_kw)
        solar_charge = series("solar_charge_kw", c.charge_kw)
        solar_export = series("solar_export_kw")
        discharge = series("discharge_kw", c.discharge_kw)
        energy = series("energy_kwh", c.capacity_kwh)
        direction = series("battery_direction", binary=True)
        solar_ev = series("solar_ev_kw")
        current = series("ev_current_a", c.ev_max_current_a)
        for variable in current.values():
            variable.cat = pulp.LpInteger
        ev_on = series("ev_on", binary=True)
        ev_energy = series("ev_energy_kwh", ev.capacity_kwh if ev else 0)
        self.ev_shortfall = pulp.LpVariable("ev_minimum_shortfall_kwh", 0,
                                            ev.minimum_kwh if ev else 0)
        grid_direction = None
        if not c.allow_grid_charge_while_exporting:
            grid_direction = series("grid_direction", binary=True)

        self.direct_solar = np.minimum(self.solar_kw, self.load_kw)
        for i in self.intervals:
            residual = self.load_kw[i] - self.direct_solar[i]
            surplus = self.solar_kw[i] - self.direct_solar[i]
            ev_power = current[i] * c.ev_voltage / 1000
            self.problem += grid_load[i] + discharge[i] + solar_ev[i] == residual + ev_power
            self.problem += solar_charge[i] + solar_export[i] + solar_ev[i] == surplus
            # Explicitly prevent PV assigned to the EV from exceeding its load.
            self.problem += solar_ev[i] <= ev_power
            self.problem += grid_load[i] + grid_charge[i] <= c.grid_import_kw
            self.problem += grid_charge[i] + solar_charge[i] <= c.charge_kw * direction[i]
            self.problem += discharge[i] <= c.discharge_kw * (1 - direction[i])
            if c.charge_kw == 0 or c.discharge_kw == 0:
                self.problem += direction[i] == int(c.charge_kw > 0)
            if grid_direction is not None:
                self.problem += grid_load[i] + grid_charge[i] <= c.grid_import_kw * grid_direction[i]
                self.problem += solar_export[i] <= surplus * (1 - grid_direction[i])
            if self.preserved[i]:
                self.problem += discharge[i] == 0
                self.problem += solar_charge[i] == 0
            previous = self.initial_kwh if i == 0 else energy[i - 1]
            self.problem += energy[i] == previous + self.interval_hours * (
                (grid_charge[i] + solar_charge[i]) * c.charge_efficiency
                - discharge[i] / c.discharge_efficiency)
            self.problem += energy[i] >= c.minimum_kwh
            self.problem += current[i] >= c.ev_min_current_a * ev_on[i]
            self.problem += current[i] <= c.ev_max_current_a * ev_on[i]
            if ev:
                if i > ev.deadline_index or not available[i]:
                    self.problem += ev_on[i] == 0
                previous_ev = ev.initial_kwh if i == 0 else ev_energy[i - 1]
                self.problem += ev_energy[i] == previous_ev + ev_power * c.ev_efficiency * self.interval_hours
            else:
                self.problem += ev_on[i] == 0
                self.problem += ev_energy[i] == 0
        self.problem += energy[self.n_intervals - 1] >= c.terminal_kwh
        if ev:
            self.problem += ev_energy[ev.deadline_index] + self.ev_shortfall >= ev.minimum_kwh
        self._add_cost(pulp.lpSum(
            ((grid_load[i] + grid_charge[i]) * self.prices[i]
             - solar_export[i] * c.export_value
             + discharge[i] * c.wear_per_discharged_kwh
             - solar_charge[i] * c.early_solar_tie_break * (self.n_intervals - i))
            * self.interval_hours for i in self.intervals)
            - energy[self.n_intervals - 1] * c.terminal_value
            + self.ev_shortfall * c.ev_shortfall_penalty)
        if ev:
            self._add_cost(-ev_energy[ev.deadline_index] * c.ev_optional_price / c.ev_efficiency)

    def _flags(self, values, name, default):
        result = self._series(default if values is None else values, name, scalar=True)
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
                             pulp.PULP_CBC_CMD(msg=False, timeLimit=self.config.solver_seconds))

    def _validate_solution(self):
        super()._validate_solution()
        value = self.ev_shortfall.varValue
        if value is None or not np.isfinite(value) or not self.ev_shortfall.valid(1e-5):
            raise OptimizationError("Solver returned invalid EV shortfall")

    def result(self):
        """Return validated, unrounded model values for replay or an adapter."""
        series = self.get_time_series()["household"]
        return {
            "series": series,
            "objective": float(pulp.value(self.total_cost)),
            "ev_shortfall_kwh": float(self.ev_shortfall.value()),
        }
