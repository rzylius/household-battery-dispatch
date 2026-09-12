"""Synthetic 15-minute example; replace forecasts with aligned real inputs."""

import numpy as np
import pulp

from optim import EnergyOptimizer


def run_example():
    # EUR/kWh, including the variable charges relevant to your contract.
    # These are synthetic prices, not a live market feed.
    prices = [0.10] * 24 + [0.22] * 24 + [0.08] * 24 + [0.35] * 24
    count = len(prices)
    dt = 0.25
    demand_kw = np.full(count, 1.5)
    solar_kw = np.maximum(0, 5 * np.sin(np.pi * (np.arange(count) - 24) / 48))

    optimizer = EnergyOptimizer(n_intervals=count, interval_hours=dt)
    optimizer.add_mains_electricity_supply(
        "grid", max_import_power=10, import_hourly_prices=prices,
        max_export_power=5, export_hourly_prices=[0.05] * count,
    )
    optimizer.add_fixed_consumption("house", hourly_consumption=demand_kw)
    optimizer.add_solar_production("solar", estimated_hourly_production=solar_kw)
    # kWh throughout; a 15 kWh battery at 50% SOC starts with 7.5 kWh.
    minimum_soc = np.full(count, 3.0)
    minimum_soc[-1] = 7.5
    optimizer.add_battery(
        "battery", capacity=15, initial_soc=7.5, efficiency=0.95,
        max_charge_power=5, max_discharge_power=5,
        cost_of_cycle_kwh=0.03, final_energy_value_per_kwh=0.15,
        min_soc=minimum_soc,
    )
    optimizer.solve()
    series = optimizer.get_time_series()
    grid = series["grid"]
    battery = series["battery"]
    net_grid_cost = np.dot(grid["import"], prices) * dt + grid["export"].sum() * 0.05 * dt
    print(f"Intervals: {count}; duration: {dt * 60:g} minutes")
    print(f"Imported energy: {grid['import'].sum() * dt:.2f} kWh")
    print(f"Net grid cost: EUR {net_grid_cost:.2f}")
    print(f"Final stored energy: {battery['soc'][-1]:.2f} kWh")
    print(f"Objective including wear and terminal value: EUR {pulp.value(optimizer.total_cost):.2f}")
    print("First interval:", {key: float(values[0]) for key, values in battery.items()})
    return optimizer, series


if __name__ == "__main__":
    run_example()
