from optim import EnergyOptimizer
import numpy as np



def check_bounds(values, minv, maxv, atol=0):
    assert atol >= 0
    if atol == 0:
        assert np.all(values >= minv)
        assert np.all(values <= maxv)
    else:
        assert np.all(values >= minv-atol)
        assert np.all(values <= maxv+atol)

def check_at_most_one_nonzero(values1, values2, atol=0):
    m1 = np.absolute(values1) >= atol
    m2 = np.absolute(values2) >= atol
    assert np.all(np.logical_not(np.logical_and(m1, m2)))


def test_battery():
    # Without the battery the below consumption demand is unsolvable, as it iexceeds supply power
    
    n = 24
    optimizer = EnergyOptimizer(n)

    # Setup power grid connection.
    max_import_power = 10
    max_export_power = 0
    import_hourly_prices = [15, 18, 19, 10, 10, 10, 10, 10, 10, 10, 10, 12, 14, 15, 15, 13, 14, 12, 15, 14, 13, 11, 10, 13]
    optimizer.add_mains_electricity_supply(name='mains', max_import_power=max_import_power, import_hourly_prices=import_hourly_prices, max_export_power=max_export_power)

    # Setup a battery.
    battery_capacity = 15
    initial_soc = 10
    efficiency = 0.95
    max_charge_power = 5
    max_discharge_power = 15
    # minimum and maximum state of chare as time series. Could also be None.
    min_soc = [0] * 6 + [5] * 18
    max_soc = [battery_capacity] * 12 + [battery_capacity - 5] * 12
    optimizer.add_battery(
        name='battery', capacity=battery_capacity, initial_soc=initial_soc, efficiency=efficiency,
        max_charge_power=max_charge_power, max_discharge_power=max_discharge_power,
        cost_of_cycle_kwh=1, final_energy_value_per_kwh=12, min_soc=min_soc, max_soc=max_soc)
    
    # Set fixed consumption schedule.
    hourly_consumption = [1, 1, 2, 1, 1, 1, 2, 1, 2, 5, 1, 2, 3, 14, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    optimizer.add_fixed_consumption(name='consumption', hourly_consumption=hourly_consumption)

    # Solve the optimization problem.
    optimizer.solve()

    # Obtain solved time series as a nested dict of numpy arrays.
    series = optimizer.get_time_series()
    optimizer.print_time_series()

    atol = 1e-10
    # Test constraints.
    check_bounds(series['mains']['import'], 0.0, max_import_power, atol=atol) 
    check_bounds(series['mains']['export'], -max_export_power, 0.0, atol=atol)
    check_at_most_one_nonzero(series['mains']['import'], series['mains']['export'], atol=atol)
    check_bounds(series['battery']['discharge_rate'], -max_discharge_power, 0)
    check_bounds(series['battery']['charge_rate'], 0, max_charge_power)
    check_bounds(series['battery']['soc'], min_soc, max_soc)
    print(series['battery']['soc'])
    # Test energy conservation.
    np.testing.assert_allclose(series['mains']['import'] + series['mains']['export'] - series['battery']['charge_rate'] - series['battery']['discharge_rate'] * efficiency, hourly_consumption, atol=1e-6)

    # Test charge conservation.
    soc = series['battery']['soc']
    previous_soc = np.zeros(n, dtype=np.float32)
    previous_soc[1:] = soc[0:-1]
    previous_soc[0] = initial_soc
    np.testing.assert_allclose(soc - previous_soc, series['battery']['charge_rate'] + series['battery']['discharge_rate'], atol=1e-6)


def test_solar():
    n = 24
    optimizer = EnergyOptimizer(n)

    # Setup power grid connection.
    max_import_power = 10
    max_export_power = 5

    import_hourly_prices = [15, 18, 19, 10, 10, 10, 10, 10, 10, 10, 10, 12, 14, 15, 15, 13, 14, 12, 15, 14, 13, 11, 10, 13]
    export_hourly_prices = [15] * n

    hourly_consumption = [1, 1, 2, 1, 1, 1, 2, 1, 2, 5, 1, 2, 3, 14, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    hourly_solar_production = [0, 0, 0, 1, 4, 8, 12, 22, 12, 8, 6, 4, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0]

    optimizer.add_mains_electricity_supply(name='mains',
        max_import_power=max_import_power, import_hourly_prices=import_hourly_prices,
        max_export_power=max_export_power, export_hourly_prices=export_hourly_prices)
    optimizer.add_fixed_consumption(name='consumption', hourly_consumption=hourly_consumption)
    optimizer.add_solar_production(name='solar', estimated_hourly_production=hourly_solar_production)

    # Setup a battery.
    battery_capacity = 15
    initial_soc = 10
    efficiency = 0.95
    max_charge_power = 5
    max_discharge_power = 10
    # minimum and maximum state of chare as time series. Could also be None.
    optimizer.add_battery(
        name='battery', capacity=battery_capacity, initial_soc=initial_soc, efficiency=efficiency,
        max_charge_power=max_charge_power, max_discharge_power=max_discharge_power,
        cost_of_cycle_kwh=1, final_energy_value_per_kwh=12)
    
    # Solve the optimization problem.
    optimizer.solve()

    # Obtain solved time series as a nested dict of numpy arrays.
    series = optimizer.get_time_series()
    optimizer.print_time_series() 

    atol = 1e-10
    # Test constraints.
    check_bounds(series['mains']['import'], 0.0, max_import_power, atol=atol) 
    check_bounds(series['mains']['export'], -max_export_power, 0.0, atol=atol)
    check_at_most_one_nonzero(series['mains']['import'], series['mains']['export'], atol=atol) 
    check_bounds(series['battery']['discharge_rate'], -max_discharge_power, 0)
    check_bounds(series['battery']['charge_rate'], 0, max_charge_power)
    check_bounds(series['battery']['soc'], 0, battery_capacity)
    # Note: solar plant may be throttled down if it can not find consumotion or export opportunities.
    # For this reason we check bounds as opposed to equality.
    check_bounds(series['solar']['production'], 0, hourly_solar_production)


def simple_heatpump_example():
    # Example usage:
    hourly_prices = [15, 18, 19, 10, 10, 10, 10, 10, 10, 10, 10, 12, 14, 15, 15, 13, 14, 12, 15, 14, 13, 11, 10, 13]
    # Without the battery the below consumption demand is unsolvable, as it iexceeds supply power
    hourly_consumption = [1, 1, 2, 1, 1, 1, 2, 1, 2, 5, 1, 2, 3, 14, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    hourly_heating_demand= [1.2, 2, 1, 1.5, 1.7, 1.8, 1.3, 1.7, 2.1, 3.3, 1.2, 2.7, 1.2, 2.3, 1.2, 1.1, 1.3, 1.2, 1.7, 2.1, 2.5, 2.7, 2.8, 2.9]
    min_ev_charge_by_hour= [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,47,0,0,0,0,0,0]
    final_energy_value_per_kwh = 12
   
    optimizer = EnergyOptimizer(len(hourly_prices))

    electricity_import = optimizer.add_mains_electricity_supply(name='eso', max_import_power=10, import_hourly_prices=hourly_prices, max_export_power=0)
    soc, charge_rate, discharge_rate = optimizer.add_battery(name='battery', capacity=15, initial_soc=10, efficiency=0.95, max_charge_power=5, max_discharge_power=5, cost_of_cycle_kwh=1, final_energy_value_per_kwh=final_energy_value_per_kwh)
    consumption = optimizer.add_fixed_consumption(name='consumption', hourly_consumption=hourly_consumption)
    heating_power = optimizer.add_heating_consumption(name='heatpump', max_heat_power=3.0, hourly_demand=hourly_heating_demand, tol_cumul_min=-2, tol_cumul_max=2, final_energy_value_per_kwh=final_energy_value_per_kwh)  
    ev_charging = optimizer.add_flexible_consumption(name='ev_charging', max_power=5.0, min_cumulative_consuption=min_ev_charge_by_hour)  

    optimizer.solve()

    series = optimizer.get_time_series()
    optimizer.print_time_series()


test_battery()
test_solar()
simple_heatpump_example()