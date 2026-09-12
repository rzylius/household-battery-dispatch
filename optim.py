import pulp
import numpy as np
from numbers import Integral


class OptimizationError(RuntimeError):
    """No validated optimal dispatch is available."""


class EnergyOptimizer():

    def __init__(self, n_hours=None, *, n_intervals=None, interval_hours=1.0):
        """
        Plan equal-duration intervals. Use n_intervals=96, interval_hours=0.25
        for a normal 24-hour day of quarter-hour prices. The historical
        n_hours argument is an alias for the interval count; hourly operation
        remains the default for existing callers.

        Power is kW, stored/cumulative energy is kWh, and prices are in one
        consistent currency unit per kWh. Forecast arrays are average kW per
        interval, including arguments with historical 'hourly' names.
        """
        if (n_hours is None) == (n_intervals is None):
            raise ValueError("Supply exactly one of n_intervals or n_hours")
        count = n_intervals if n_intervals is not None else n_hours
        if isinstance(count, bool) or not isinstance(count, Integral) or count <= 0:
            raise ValueError("n_intervals must be a positive integer")
        self.interval_hours = self._number(interval_hours, "interval_hours", positive=True)
        self.problem = pulp.LpProblem("Power_Optimization", pulp.LpMinimize)
        self.n_intervals = int(count)
        self.intervals = range(self.n_intervals)
        self.n_hours = self.n_intervals  # Compatibility alias: count, not duration.
        self.hours = self.intervals
        self._finalized = False
        self._solved = False
        self.status = "Not Solved"
        # Total cost of the period of modelling. This variable gets updated every time when
        # consumers and producers get added to the system. This is the variable that gets
        # minimized by the linear solver
        self.total_cost = 0.0
        # Variables tracking energy balance as difference devices are added. Must sum to zero.
        self.energy_balance = [0.0 for hour in self.hours]
        # Nested dict containing all other variables.
        self.vars = {}

    @staticmethod
    def _number(value, name, *, minimum=None, positive=False):
        try:
            if isinstance(value, (bool, str, bytes)) or np.ndim(value) != 0:
                raise ValueError
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{name} must be a finite number") from None
        if not np.isfinite(value):
            raise ValueError(f"{name} must be finite")
        if positive and value <= 0:
            raise ValueError(f"{name} must be positive")
        if minimum is not None and value < minimum:
            raise ValueError(f"{name} must be at least {minimum}")
        return value

    def _series(self, values, name, *, scalar=False, minimum=None):
        try:
            result = np.asarray(values, dtype=float)
        except (TypeError, ValueError):
            raise ValueError(f"{name} must contain finite numbers") from None
        if scalar and result.ndim == 0:
            result = np.full(self.n_intervals, result.item())
        if result.shape != (self.n_intervals,) or not np.all(np.isfinite(result)):
            raise ValueError(f"{name} must contain {self.n_intervals} finite values")
        if minimum is not None and np.any(result < minimum):
            raise ValueError(f"{name} values must be at least {minimum}")
        return result

    def _add_cost(self, cost):
        self.total_cost = self.total_cost + cost

    def _add_to_energy_balance(self, hour, energy):
        """
        Updates energy balance for a given hour. All hourly balances are constraint to sum to zero.
        """
        self.energy_balance[hour] = self.energy_balance[hour] + energy

    def _new_time_series(self, device_name: str, var_name: str, lowBound=None, upBound=None, binary=False):
        """
        Create a new time series.
        """
        if self._finalized:
            raise RuntimeError("Create a new optimizer to change devices after solving")
        if not isinstance(device_name, str) or not device_name or not all(
            c.isascii() and (c.isalnum() or c == '_') for c in device_name
        ):
            raise ValueError("Device names must contain only ASCII letters, digits and underscores")
        # PuLP normalizes names. Reject collisions between device/variable pairs.
        new_names = {f"{device_name}_{var_name}_{i}" for i in self.intervals}
        existing_names = {
            v.name for device in self.vars.values() for series in device.values()
            for v in series.values()
        }
        if new_names & existing_names:
            raise ValueError(f"Duplicate variable names for {device_name}.{var_name}")
        if binary:
            var = pulp.LpVariable.dicts(device_name + "_" + var_name, self.hours, cat='Binary')
        else:
            var = pulp.LpVariable.dicts(device_name + "_" + var_name, self.hours, lowBound=lowBound, upBound=upBound)

        if not device_name in self.vars:
            self.vars[device_name] = {}
        device_vars = self.vars[device_name]
        device_vars[var_name] = var
        return var

    def solve(self, solver=None):
        """
        Solves the system of equations. A solution may or may not exist.
        Must be called after all devices are added.
        """
        if not self.vars:
            raise ValueError("Add at least one device before solving")
        self._solved = False
        self.status = "Not Solved"
        if not self._finalized:
            for hour in self.hours:
                self.problem += pulp.lpSum([self.energy_balance[hour]]) == 0
            self.problem += pulp.lpSum([self.total_cost])
            self._finalized = True
        try:
            status = self.problem.solve(solver or pulp.PULP_CBC_CMD(msg=False))
        except pulp.PulpSolverError as exc:
            self.status = "Solver Error"
            raise OptimizationError("Solver failed; no dispatch is available") from exc
        self.status = pulp.LpStatus[status]
        if status != pulp.LpStatusOptimal or self.problem.sol_status != pulp.LpSolutionOptimal:
            solution_status = pulp.LpSolution.get(self.problem.sol_status, "Unknown")
            raise OptimizationError(f"No optimal dispatch: {self.status}; {solution_status}")
        self._validate_solution()
        self._solved = True
        return self.status

    def _validate_solution(self):
        tolerance = 1e-5
        for device in self.vars.values():
            for series in device.values():
                for variable in series.values():
                    if (variable.varValue is None or not np.isfinite(variable.varValue)
                            or not variable.valid(tolerance)):
                        raise OptimizationError("Solver returned invalid dispatch values")
        if not all(c.valid(tolerance) for c in self.problem.constraints.values()):
            raise OptimizationError("Solver returned a dispatch that violates constraints")

    def get_time_series(self):
        """
        Returns a nested dictionary containing all time series as np ndarray.
        """
        if (not self._solved or self.problem.status != pulp.LpStatusOptimal
                or self.problem.sol_status != pulp.LpSolutionOptimal):
            raise OptimizationError("Call solve() successfully before reading dispatch")
        self._validate_solution()
        res = {}
        for device_name in self.vars:
            device_vars = self.vars[device_name]
            device_series = {}
            for var_name in device_vars:
                var = device_vars[var_name]
                device_series[var_name] = np.array([var[hour].varValue for hour in self.hours], dtype=np.float64)
            res[device_name] = device_series
        return res

    def print_time_series(self, ts=None, prefix=''):
        if ts is None:
            ts = self.get_time_series()
        for key, value in ts.items():
            if type(value) is dict:
                print(prefix + key + ':')
                self.print_time_series(value, prefix + '    ')
            else:
                print(prefix + key + ':', value)

    def add_mains_electricity_supply(self, name, max_import_power, import_hourly_prices, max_export_power=0, export_hourly_prices=None):
        """Add a grid connection with import/export limits in kW.

        import_hourly_prices/export_hourly_prices contain one price per
        interval, in the same currency unit per kWh. Despite their historical
        names, these arrays are not restricted to hourly prices. Export prices
        are required when max_export_power > 0. Imports and exports cannot
        occur simultaneously on this connection."""
        max_import_power = self._number(max_import_power, "max_import_power", minimum=0)
        max_export_power = self._number(max_export_power, "max_export_power", minimum=0)
        import_hourly_prices = self._series(import_hourly_prices, "import_hourly_prices")
        if max_export_power > 0:
            export_hourly_prices = self._series(export_hourly_prices, "export_hourly_prices")
        # dfirection: 0 for exports, 1 for imports.
        direction = self._new_time_series(name, "direction", binary=True)
        electricity_import = self._new_time_series(name, "import", lowBound=0, upBound=max_import_power)
        electricity_export = self._new_time_series(name, "export", lowBound=-max_export_power, upBound=0)
        electricity_import_cost = pulp.lpSum(electricity_import[hour] * import_hourly_prices[hour] * self.interval_hours for hour in self.hours)
        self._add_cost(electricity_import_cost)
        if max_export_power > 0:
            electricity_export_cost = pulp.lpSum(electricity_export[hour] * export_hourly_prices[hour] * self.interval_hours for hour in self.hours)
            # the later cost is negative, so it is actually a profit.
            self._add_cost(electricity_export_cost)
        for hour in self.hours:
            self._add_to_energy_balance(hour, electricity_import[hour] + electricity_export[hour])
            # Make sure that we either import or export, but do not do both at the same time.
            self.problem += electricity_import[hour] <= max_import_power * direction[hour]
            self.problem += electricity_export[hour] >= -max_export_power * (1 - direction[hour])
            if max_import_power == 0 or max_export_power == 0:
                self.problem += direction[hour] == int(max_import_power > 0)

        return electricity_import

    def add_battery(self, name, capacity, initial_soc, efficiency,
            max_charge_power, max_discharge_power, cost_of_cycle_kwh,
            final_energy_value_per_kwh, min_soc=None, max_soc=None):
        """Add a battery with energy/SOC in kWh and rates in kW.

        The historical loss convention is preserved: charging is lossless in
        the model and round-trip efficiency is applied on discharge. Thus
        discharge_rate and max_discharge_power refer to stored-energy removal;
        delivered AC power is -discharge_rate * efficiency. charge_rate is
        nonnegative and discharge_rate is nonpositive. They are mutually
        exclusive within each interval.

        min_soc/max_soc are scalars or per-interval end-of-interval bounds in
        kWh. initial_soc is also kWh, not a percentage. cost_of_cycle_kwh is a
        nonnegative cost per kWh charged (not a full-cycle price). Terminal
        stored energy is valued at efficiency * final_energy_value_per_kwh,
        in the same currency unit per kWh as grid prices."""
        capacity = self._number(capacity, "capacity", positive=True)
        initial_soc = self._number(initial_soc, "initial_soc", minimum=0)
        efficiency = self._number(efficiency, "efficiency", positive=True)
        if initial_soc > capacity or efficiency > 1:
            raise ValueError("initial_soc must not exceed capacity and efficiency must not exceed 1")
        max_charge_power = self._number(max_charge_power, "max_charge_power", minimum=0)
        max_discharge_power = self._number(max_discharge_power, "max_discharge_power", minimum=0)
        cost_of_cycle_kwh = self._number(cost_of_cycle_kwh, "cost_of_cycle_kwh", minimum=0)
        final_energy_value_per_kwh = self._number(final_energy_value_per_kwh, "final_energy_value_per_kwh")
        min_soc = self._series(0 if min_soc is None else min_soc, "min_soc", scalar=True, minimum=0)
        max_soc = self._series(capacity if max_soc is None else max_soc, "max_soc", scalar=True, minimum=0)
        if np.any(min_soc > max_soc) or np.any(max_soc > capacity):
            raise ValueError("SOC bounds must satisfy 0 <= min_soc <= max_soc <= capacity")
        direction = self._new_time_series(name, "direction", binary=True)
        charge_rate = self._new_time_series(name, "charge_rate", lowBound = 0, upBound= max_charge_power)
        discharge_rate = self._new_time_series(name, "discharge_rate", lowBound = -max_discharge_power, upBound=0)
        soc = self._new_time_series(name, "soc", lowBound=0, upBound=capacity)
        current_soc = initial_soc
        for hour in self.hours:
            self._add_to_energy_balance(hour, -charge_rate[hour] - discharge_rate[hour] * efficiency)
            self.problem += soc[hour] == current_soc + (charge_rate[hour] + discharge_rate[hour]) * self.interval_hours
            self.problem += charge_rate[hour] <= max_charge_power * direction[hour]
            self.problem += discharge_rate[hour] >= -max_discharge_power * (1 - direction[hour])
            if max_charge_power == 0 or max_discharge_power == 0:
                self.problem += direction[hour] == int(max_charge_power > 0)
            current_soc = soc[hour]
            self.problem += current_soc >= min_soc[hour]
            self.problem += current_soc <= max_soc[hour]
        amortization_cost = pulp.lpSum(charge_rate[hour] * cost_of_cycle_kwh * self.interval_hours for hour in self.hours)
        remaining_value = current_soc * efficiency * final_energy_value_per_kwh
        self._add_cost(amortization_cost - remaining_value)
        return soc, charge_rate, discharge_rate

    def add_fixed_consumption(self, name, hourly_consumption):
        """Add fixed demand as average kW in each interval.

        hourly_consumption is a historical argument name; provide one value
        per optimization interval. Divide interval kWh readings by
        interval_hours before passing them to this method."""
        hourly_consumption = self._series(hourly_consumption, "hourly_consumption", minimum=0)
        consumption = self._new_time_series(name, "consumption", lowBound = 0, upBound= np.amax(hourly_consumption))
        for hour in self.hours:
            self._add_to_energy_balance(hour, -consumption[hour])
            self.problem += consumption[hour] == hourly_consumption[hour]
        return consumption

    def add_solar_production(self, name, estimated_hourly_production):
        """Add available solar production as average kW per interval.

        estimated_hourly_production is a historical argument name. The model
        may curtail production; actual hardware must support that action."""
        estimated_hourly_production = self._series(estimated_hourly_production, "estimated_hourly_production", minimum=0)
        production = self._new_time_series(name, "production", lowBound = 0, upBound= np.amax(estimated_hourly_production))
        for hour in self.hours:
            self.problem += production[hour] >= 0
            self.problem += production[hour] <= estimated_hourly_production[hour]
            self._add_to_energy_balance(hour, production[hour])
        return production

    def add_flexible_consumption(self, name, max_power, min_cumulative_consuption,
                                 *, max_cumulative_consumption=None, availability=None):
        """Schedule flexible consumption with deadlines and an energy cap.

        max_power is kW. min_cumulative_consuption (historical spelling) is
        cumulative kWh required by the END of each interval; e.g. [0, 0, 1, 2]
        requires 1 kWh by interval 3 and 2 kWh by interval 4. Zero entries do
        not reset accumulated energy.

        max_cumulative_consumption is a scalar or per-interval upper bound
        in kWh; by default it is the largest minimum requirement. Set it
        explicitly if additional consumption is useful and physically possible.
        availability is a scalar or sequence of 0/1 (False/True); zero disables
        consumption in that interval. Defaults to available throughout."""
        max_power = self._number(max_power, "max_power", minimum=0)
        min_cumulative_consuption = self._series(min_cumulative_consuption, "min_cumulative_consuption", minimum=0)
        # A minimum-only EV model overconsumes when prices are negative.
        if max_cumulative_consumption is None:
            max_cumulative_consumption = float(np.max(min_cumulative_consuption))
        maximum = self._series(max_cumulative_consumption, "max_cumulative_consumption", scalar=True, minimum=0)
        if np.any(np.maximum.accumulate(min_cumulative_consuption) > maximum):
            raise ValueError("Cumulative consumption bounds are inconsistent")
        available = self._series(1 if availability is None else availability, "availability", scalar=True)
        if not np.all((available == 0) | (available == 1)):
            raise ValueError("availability must contain only 0/1 or False/True")
        consumption = self._new_time_series(name, "consumption", lowBound = 0, upBound=max_power)
        cumul_consumption = 0.0
        for hour in self.hours:
            self._add_to_energy_balance(hour, -consumption[hour])
            self.problem += consumption[hour] <= max_power * available[hour]
            cumul_consumption = cumul_consumption + consumption[hour] * self.interval_hours
            self.problem += cumul_consumption >= min_cumulative_consuption[hour]
            self.problem += cumul_consumption <= maximum[hour]
        return consumption

    def add_heating_consumption(self, name, max_heat_power, hourly_demand, tol_cumul_min, tol_cumul_max, final_energy_value_per_kwh):
        """Schedule heating electricity within cumulative energy tolerances.

        max_heat_power and hourly_demand are electrical kW, not thermal kW.
        hourly_demand contains one average-power value per interval despite
        its historical name. tol_cumul_min <= 0 and tol_cumul_max >= 0 are
        cumulative deviations in electrical kWh. Their units do not change
        when the interval duration changes. final_energy_value_per_kwh values
        the final surplus/deficit in the same currency unit as grid prices.

        This is an approximate thermal-storage model: it does not explicitly
        model room temperature, temperature-dependent heat loss or varying COP."""
        max_heat_power = self._number(max_heat_power, "max_heat_power", minimum=0)
        tol_cumul_min = self._number(tol_cumul_min, "tol_cumul_min")
        tol_cumul_max = self._number(tol_cumul_max, "tol_cumul_max", minimum=0)
        if tol_cumul_min > 0:
            raise ValueError("tol_cumul_min must be <= 0")
        hourly_demand = self._series(hourly_demand, "hourly_demand", minimum=0)
        final_energy_value_per_kwh = self._number(final_energy_value_per_kwh, "final_energy_value_per_kwh")
        heating_power = self._new_time_series(name, "consumption", lowBound = 0, upBound=max_heat_power)
        cumul_demand = 0.0
        cumul_power= 0.0
        for hour in self.hours:
            cumul_demand = cumul_demand + hourly_demand[hour] * self.interval_hours
            cumul_power = cumul_power + heating_power[hour] * self.interval_hours
            self._add_to_energy_balance(hour, -heating_power[hour])
            self.problem += cumul_power >= cumul_demand + tol_cumul_min
            self.problem += cumul_power <= cumul_demand + tol_cumul_max
        # Reward for accumulating heat and penalize for final underheating.
        self._add_cost((cumul_demand - cumul_power) * final_energy_value_per_kwh)
        return heating_power
