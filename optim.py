import pulp
import numpy as np


class EnergyOptimizer():

    def __init__(self, n_hours):
        self.problem = pulp.LpProblem("Power_Optimization", pulp.LpMinimize)
        self.n_hours = n_hours
        self.hours = range(n_hours)
        # Define decision variables
        self.total_cost = 0.0
        self.energy_balance = [0.0 for hour in self.hours]

    def _add_cost(self, cost):
        self.total_cost = self.total_cost + cost        

    def _add_to_energy_balance(self, hour, energy):
        self.energy_balance[hour] = self.energy_balance[hour] + energy   
        print(hour, self.energy_balance[hour])

    def add_mains_electricity_supply(self, name, max_power, hourly_prices):
        """Add mains electricy supply the system."""
        assert len(hourly_prices) == self.n_hours
        electricity_import = pulp.LpVariable.dicts(name + "_import", self.hours, lowBound=0, upBound=max_power)
        electricity_cost = pulp.lpSum(electricity_import[hour] * hourly_prices[hour] for hour in self.hours)
        self._add_cost(electricity_cost)
        for hour in self.hours:
            self._add_to_energy_balance(hour, electricity_import[hour])
        return electricity_import

    def add_battery(self, name, capacity, initial_soc, efficiency, max_charge_power, max_discharge_power, cost_of_cycle_kwh, final_energy_value_per_kwh):  
        """
        Add battery into the system
        Args:
          capacity - battery capacity in kwh
          cost_of_cycle_kwh - estimated battery round trip amortization cost.
          final_energy_value_per_kwh - estimated final value of energy after the last known hour. 
        """  
        assert max_charge_power > 0
        assert max_discharge_power > 0
        charge_rate = pulp.LpVariable.dicts(name + "_charge_rate", self.hours, lowBound = 0, upBound= max_charge_power)
        discharge_rate = pulp.LpVariable.dicts(name + "_discharge_rate", self.hours, lowBound = -max_discharge_power, upBound=0)
        soc = pulp.LpVariable.dicts(name + "_soc", self.hours, lowBound=0, upBound=capacity)
        current_soc = initial_soc
        for hour in self.hours:
            self._add_to_energy_balance(hour, -charge_rate[hour] - discharge_rate[hour] * efficiency)
            self.problem += soc[hour] == current_soc + charge_rate[hour] + discharge_rate[hour] # Conservation of charge 
            current_soc = soc[hour]
        amortization_cost = pulp.lpSum(charge_rate[hour] * cost_of_cycle_kwh for hour in self.hours)    
        remaining_value = current_soc * efficiency * final_energy_value_per_kwh
        self._add_cost(amortization_cost - remaining_value)
        return soc, charge_rate, discharge_rate

    def add_fixed_consumption(self, name, hourly_consumption):  
        """
        Add simple fixed hourly consumption.
        """  
        assert len(hourly_consumption) == self.n_hours
        consumption = pulp.LpVariable.dicts(name + "_consumption", self.hours, lowBound = 0, upBound= np.amax(hourly_consumption))
        for hour in self.hours:
            assert hourly_consumption[hour] >= 0 
            self._add_to_energy_balance(hour, -consumption[hour])
            self.problem += consumption[hour] == hourly_consumption[hour]
        return consumption    

    def add_heating_consumption(self, name, max_heat_power, hourly_demand, tol_cumul_min, tol_cumul_max, final_energy_value_per_kwh):  
        """
        Models a heat pump consumption that may be throttled up or down within desired boubnds.
        We are operating in electricity kWh and not heat kWh (that would be further multipled by COP)
        hourly_demand must be expressed in electricity kWh
        Arguments:
           hourly_demeand - estimated hourly heat pump demand at the desired steady state temperature.
              It should be calculated using weather forecast data and known tabulated heating power values
              for a given house at a steady state
           tol_cumul_min/max - minimal and maximal deviations of planned cumulative heating power from its target;
              as house temperature change is proportional to the cumulative heating input minus external cooling loss,
              house temperature tolerance can be expressed in terms of cumulative heating toleance. 
              Cumulative power deviations from hourly_demand is proportional to temperature drop or rise inside the house.
              If the house is alsoready too cold/hot,
              the appropriate min/max value should be set to 0. tol_cumul_min <= 0; tol_cumul_max >= 0
              If tolerance is zero on both sides then the system has no space for heating optimizations.
           final_energy_value_per_kwh - estimated electricity price after the last hour with known price; depending on this
              value the planner may plan to leave the house slighly wormer or cooler.   
        """  
        assert tol_cumul_min <= 0
        assert tol_cumul_max >= 0
        assert len(hourly_demand) == self.n_hours
        heating_power = pulp.LpVariable.dicts(name + "_consumption", self.hours, lowBound = 0, upBound=max_heat_power)
        cumul_demand = 0.0
        cumul_power= 0.0
        for hour in self.hours:
            assert hourly_demand[hour] >= 0 
            cumul_demand = cumul_demand + hourly_demand[hour]
            cumul_power = cumul_power + heating_power[hour]
            self._add_to_energy_balance(hour, -heating_power[hour])
            self.problem += cumul_power >= cumul_demand + tol_cumul_min
            self.problem += cumul_power <= cumul_demand + tol_cumul_max
        # Reward for accumulating heat and penalize for final underheating.    
        self._add_cost((cumul_demand - cumul_power) * final_energy_value_per_kwh)
        return heating_power

    def solve(self):
        for hour in self.hours:
            self.problem += self.energy_balance[hour] == 0
        self.problem += self.total_cost    
        status = self.problem.solve()
        print(status)