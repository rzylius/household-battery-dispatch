from optim import EnergyOptimizer
import numpy as np


def test_battery():
    # Without the battery the below consumption demand is unsolvable, as it iexceeds supply power
    
    n = 24
    optimizer = EnergyOptimizer(n)

    # Setup power grid connection.
    max_mains_power = 10
    hourly_prices = [15, 18, 19, 10, 10, 10, 10, 10, 10, 10, 10, 12, 14, 15, 15, 13, 14, 12, 15, 14, 13, 11, 10, 13]
    optimizer.add_mains_electricity_supply(name='mains', max_power=max_mains_power, hourly_prices=hourly_prices)

    # Setup a battery.
    battery_capacity = 15
    initial_soc = 10
    efficiency = 0.95
    max_charge_power = 5
    max_discharge_power = 15
    optimizer.add_battery(
        name='battery', capacity=battery_capacity, initial_soc=initial_soc, efficiency=efficiency,
        max_charge_power=max_charge_power, max_discharge_power=max_discharge_power,
        cost_of_cycle_kwh=1, final_energy_value_per_kwh=12)
    
    # Set fixed consumption schedule.
    hourly_consumption = [1, 1, 2, 1, 1, 1, 2, 1, 2, 5, 1, 2, 3, 14, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    optimizer.add_fixed_consumption(name='consumption', hourly_consumption=hourly_consumption)

    # Solve the optimization problem.
    optimizer.solve()

    # Obtain solved time series as a nested dict of numpy arrays.
    series = optimizer.get_time_series()
    print(series)

    # Test constraints.
    assert np.all(series['mains']['import'] >= 0)
    assert np.all(series['mains']['import'] <= max_mains_power)
    assert np.all(series['battery']['discharge_rate'] <= 0)
    assert np.all(series['battery']['discharge_rate'] >= -max_discharge_power)
    assert np.all(series['battery']['charge_rate'] >= 0)
    assert np.all(series['battery']['charge_rate'] <= max_charge_power)

    # Test energy conservation.
    np.testing.assert_allclose(series['mains']['import'] - series['battery']['charge_rate'] - series['battery']['discharge_rate'] * efficiency, hourly_consumption, atol=1e-6)

    # Test charge conservation.
    soc = series['battery']['soc']
    previous_soc = np.zeros(n, dtype=np.float32)
    previous_soc[1:] = soc[0:-1]
    previous_soc[0] = initial_soc
    np.testing.assert_allclose(soc - previous_soc, series['battery']['charge_rate'] + series['battery']['discharge_rate'], atol=1e-6)


def simple_heatpump_example():
    # Example usage:
    hourly_prices = [15, 18, 19, 10, 10, 10, 10, 10, 10, 10, 10, 12, 14, 15, 15, 13, 14, 12, 15, 14, 13, 11, 10, 13]
    # Without the battery the below consumption demand is unsolvable, as it iexceeds supply power
    hourly_consumption = [1, 1, 2, 1, 1, 1, 2, 1, 2, 5, 1, 2, 3, 14, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    hourly_heating_demand= [1.2, 2, 1, 1.5, 1.7, 1.8, 1.3, 1.7, 2.1, 3.3, 1.2, 2.7, 1.2, 2.3, 1.2, 1.1, 1.3, 1.2, 1.7, 2.1, 2.5, 2.7, 2.8, 2.9]
    min_ev_charge_by_hour= [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,47,0,0,0,0,0,0]
    final_energy_value_per_kwh = 12
   
    optimizer = EnergyOptimizer(len(hourly_prices))

    electricity_import = optimizer.add_mains_electricity_supply(name='eso', max_power=10, hourly_prices=hourly_prices)
    soc, charge_rate, discharge_rate = optimizer.add_battery(name='battery', capacity=15, initial_soc=10, efficiency=0.95, max_charge_power=5, max_discharge_power=5, cost_of_cycle_kwh=1, final_energy_value_per_kwh=final_energy_value_per_kwh)
    consumption = optimizer.add_fixed_consumption(name='consumption', hourly_consumption=hourly_consumption)
    heating_power = optimizer.add_heating_consumption(name='heatpump', max_heat_power=3.0, hourly_demand=hourly_heating_demand, tol_cumul_min=-2, tol_cumul_max=2, final_energy_value_per_kwh=final_energy_value_per_kwh)  
    ev_charging = optimizer.add_consumption_by(name='ev_charging', max_power=5.0, min_cumulative_consuption=min_ev_charge_by_hour)  

    optimizer.solve()

    series = optimizer.get_time_series()
    print(series)


test_battery()