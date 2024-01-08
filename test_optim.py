from optim import EnergyOptimizer



def simple_example():
    # Example usage:
    hourly_prices = [15, 18, 19, 10, 10, 10, 10, 10, 10, 10, 10, 12, 14, 15, 15, 13, 14, 12, 15, 14, 13, 11, 10, 13]
    # Without the battery the below consumption demand is unsolvable, as it iexceeds supply power
    hourly_consumption = [1, 1, 2, 1, 1, 1, 2, 1, 2, 5, 1, 2, 3, 14, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]

    optimizer = EnergyOptimizer(len(hourly_prices))

    electricity_import = optimizer.add_mains_electricity_supply(name='eso', max_power=10, hourly_prices=hourly_prices)
    soc, charge_rate, discharge_rate = optimizer.add_battery(name='battery', capacity=15, initial_soc=10, efficiency=0.95, max_charge_power=5, max_discharge_power=5, cost_of_cycle_kwh=1, final_energy_value_per_kwh=12)
    consumption = optimizer.add_fixed_consumption(name='consumption', hourly_consumption=hourly_consumption)

    optimizer.solve()

    for hour in range(len(hourly_prices)):
        print(hour, "consumption=", consumption[hour].varValue, "import=", electricity_import[hour].varValue, "soc=", soc[hour].varValue, "charge_rate=", charge_rate[hour].varValue, "discharge_rate=", discharge_rate[hour].varValue)


def simple_heatpump_example():
    # Example usage:
    hourly_prices = [15, 18, 19, 10, 10, 10, 10, 10, 10, 10, 10, 12, 14, 15, 15, 13, 14, 12, 15, 14, 13, 11, 10, 13]
    # Without the battery the below consumption demand is unsolvable, as it iexceeds supply power
    hourly_consumption = [1, 1, 2, 1, 1, 1, 2, 1, 2, 5, 1, 2, 3, 14, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    hourly_heating_demand= [1.2, 2, 1, 1.5, 1.7, 1.8, 1.3, 1.7, 2.1, 3.3, 1.2, 2.7, 1.2, 2.3, 1.2, 1.1, 1.3, 1.2, 1.7, 2.1, 2.5, 2.7, 2.8, 2.9]
    final_energy_value_per_kwh = 12
   
    optimizer = EnergyOptimizer(len(hourly_prices))

    electricity_import = optimizer.add_mains_electricity_supply(name='eso', max_power=10, hourly_prices=hourly_prices)
    soc, charge_rate, discharge_rate = optimizer.add_battery(name='battery', capacity=15, initial_soc=10, efficiency=0.95, max_charge_power=5, max_discharge_power=5, cost_of_cycle_kwh=1, final_energy_value_per_kwh=final_energy_value_per_kwh)
    consumption = optimizer.add_fixed_consumption(name='consumption', hourly_consumption=hourly_consumption)
    heating_power = optimizer.add_heating_consumption(name='heatpump', max_heat_power=3.0, hourly_demand=hourly_heating_demand, tol_cumul_min=-1.0, tol_cumul_max=1.0, final_energy_value_per_kwh=final_energy_value_per_kwh)  

    optimizer.solve()

    for hour in range(len(hourly_prices)):
        print(hour, "consumption=", consumption[hour].varValue, "import=", electricity_import[hour].varValue, "soc=", soc[hour].varValue, "charge_rate=", charge_rate[hour].varValue, "discharge_rate=", discharge_rate[hour].varValue, 'heating_power=', heating_power[hour].varValue)


simple_heatpump_example()