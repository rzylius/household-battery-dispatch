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


def test_solar():

    n = 24
    optimizer = EnergyOptimizer(n)

    # Setup power grid connection.
    max_import_power = 10
    max_export_power = 9

    import_hourly_prices = [15, 18, 19, 10, 10, 10, 10, 10, 15, 15, 10, 12, 14, 15, 15, 13, 14, 12, 15, 14, 13, 11, 10, 13]
    export_hourly_prices = [9] * n

    # household consumption and PV generation forecast
    hourly_consumption = [1, 1, 2, 1, 1, 1, 2, 1, 2, 5, 1, 2, 3, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    hourly_solar_production = [0, 0, 0, 1, 4, 8, 8, 9, 9, 8, 6, 4, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0]

    min_soc = [3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 10]

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
        cost_of_cycle_kwh=1, final_energy_value_per_kwh=12, min_soc=min_soc)

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

    print("")
    print(f"PRICE | CONSUMPT SOLAR |  GRID |   BATT    SOC")
    print(f"----------------------------------------------")
    for i in range(24):
      print(
          f"{import_hourly_prices[i]:>5} |\
{hourly_consumption[i]:>8.1f} \
{hourly_solar_production[i]:>6.1f} | \
{series['mains']['export'][i] + series['mains']['import'][i]:>5.1f} | \
{series['battery']['discharge_rate'][i] + series['battery']['charge_rate'][i]:6.2f} \
{series['battery']['soc'][i]:6.2f} \
          "
      )

    return
    
test_solar()
