"""Integration tests and explicit counterexamples for the current thermal model.

All forecasts/equipment values are synthetic. Limitation tests deliberately
show that an optimal electrical schedule can fail a richer physical model.
"""

import json
import time

import numpy as np
import pulp
import pytest

from optim import EnergyOptimizer, OptimizationError

DT = 0.25
COP = 3.0


def household_case(storage=True, battery=False, grid_limit=7.0, ev_kwh=14.0,
                   negative_prices=False):
    n = 96
    prices = np.array([0.10] * 24 + [0.22] * 40 + [0.40] * 24 + [0.12] * 8)
    if negative_prices:
        prices[:8] = -0.10
    heat_demand_kw_th = np.full(n, 6.0)
    house_kw = np.full(n, 1.0)
    initial_heat_kwh_th = 6.0 if storage else 0.0
    capacity_heat_kwh_th = 12.0 if storage else 0.0
    availability = np.array([1] * 32 + [0] * 40 + [1] * 24)
    deadline = np.zeros(n)
    deadline[31] = ev_kwh  # 08:00, end of the 32nd interval.

    optimizer = EnergyOptimizer(n_intervals=n, interval_hours=DT)
    optimizer.add_mains_electricity_supply("grid", grid_limit, prices)
    optimizer.add_fixed_consumption("house", house_kw)
    hp = optimizer.add_heating_consumption(
        "hp", 4.0, heat_demand_kw_th / COP,
        -initial_heat_kwh_th / COP,
        (capacity_heat_kwh_th - initial_heat_kwh_th) / COP,
        final_energy_value_per_kwh=0,
    )
    # Existing API has no explicit terminal heat-state parameter. A hard
    # constraint is needed here to compare equal starting/ending storage.
    optimizer.problem += pulp.lpSum(hp.values()) * DT == float(heat_demand_kw_th.sum() * DT / COP)
    optimizer.add_flexible_consumption("ev", 7.2, deadline, availability=availability)
    if battery:
        minimum = np.zeros(n)
        maximum = np.full(n, 10.0)
        minimum[-1] = maximum[-1] = 5.0
        optimizer.add_battery("battery", 10.0, 5.0, 0.95, 4.0, 4.0, 0.03, 0,
                              min_soc=minimum, max_soc=maximum)
    start = time.perf_counter()
    optimizer.solve()
    elapsed = time.perf_counter() - start
    series = optimizer.get_time_series()
    heat_state = initial_heat_kwh_th + np.cumsum(
        (COP * series["hp"]["consumption"] - heat_demand_kw_th) * DT)
    grid = series["grid"]["import"]
    hp_kw = series["hp"]["consumption"]
    ev_kw = series["ev"]["consumption"]
    grid_bill = float(np.dot(grid, prices) * DT)
    wear = float(series["battery"]["charge_rate"].sum() * DT * 0.03) if battery else 0.0
    metrics = dict(grid_bill_eur=grid_bill, wear_eur=wear,
                   total_cost_eur=grid_bill + wear, import_kwh=float(grid.sum() * DT),
                   heat_pump_kwh_e=float(hp_kw.sum() * DT),
                   heat_output_kwh_th=float(hp_kw.sum() * DT * COP),
                   house_kwh=float(series["house"]["consumption"].sum() * DT),
                   ev_kwh=float(ev_kw.sum() * DT), ev_by_0800_kwh=float(ev_kw[:32].sum() * DT),
                   peak_grid_kw=float(grid.max()),
                   heat_min_kwh_th=float(heat_state.min()), heat_max_kwh_th=float(heat_state.max()),
                   heat_final_kwh_th=float(heat_state[-1]),
                   hp_on_transitions=int(np.count_nonzero(np.diff(np.r_[False, hp_kw > 1e-6].astype(int)) == 1)),
                   solve_seconds=elapsed)
    return optimizer, series, heat_state, metrics


@pytest.mark.parametrize("battery", [False, True])
def test_combined_household_heat_storage_ev_and_optional_battery(battery):
    optimizer, series, state, metrics = household_case(battery=battery)
    hp = series["hp"]["consumption"]
    ev = series["ev"]["consumption"]
    grid = series["grid"]["import"]
    np.testing.assert_allclose(series["house"]["consumption"], 1)
    assert grid.max() <= 7 + 1e-6
    assert hp.min() >= -1e-6 and hp.max() <= 4 + 1e-6
    assert ev.min() >= -1e-6 and ev.max() <= 7.2 + 1e-6
    np.testing.assert_allclose(ev[32:72], 0, atol=1e-6)
    assert metrics["ev_by_0800_kwh"] == pytest.approx(14, abs=1e-6)
    assert metrics["ev_kwh"] == pytest.approx(14, abs=1e-6)
    assert state.min() >= -1e-5 and state.max() <= 12 + 1e-5
    assert state[-1] == pytest.approx(6, abs=1e-5)
    assert metrics["heat_output_kwh_th"] == pytest.approx(144, abs=1e-5)
    demand = 1 + hp + ev
    if battery:
        b = series["battery"]
        np.testing.assert_allclose(grid, demand + b["charge_rate"] + b["discharge_rate"] * 0.95, atol=1e-6)
        np.testing.assert_allclose(b["soc"], 5 + np.cumsum((b["charge_rate"] + b["discharge_rate"]) * DT), atol=1e-5)
        assert b["soc"][-1] == pytest.approx(5, abs=1e-6)
        assert not np.any((b["charge_rate"] > 1e-6) & (b["discharge_rate"] < -1e-6))
    else:
        np.testing.assert_allclose(grid, demand, atol=1e-6)
        assert metrics["import_kwh"] == pytest.approx(86, abs=1e-6)


def test_storage_savings_use_equal_initial_and_final_energy():
    _, _, _, fixed = household_case(storage=False)
    _, _, _, thermal = household_case(storage=True)
    _, _, _, both = household_case(storage=True, battery=True)
    # Independent hand calculation: fixed household+HP costs 16.32 EUR;
    # 14 kWh of EV electricity fits in the cheapest window for 1.40 EUR.
    assert fixed["total_cost_eur"] == pytest.approx(17.72, abs=1e-6)
    assert thermal["total_cost_eur"] < fixed["total_cost_eur"]
    assert both["total_cost_eur"] < thermal["total_cost_eur"]


def test_negative_prices_still_respect_ev_cap_and_heat_capacity():
    _, series, state, metrics = household_case(negative_prices=True)
    assert metrics["ev_kwh"] == pytest.approx(14, abs=1e-6)
    assert metrics["heat_output_kwh_th"] == pytest.approx(144, abs=1e-5)
    assert state.min() >= -1e-5 and state.max() <= 12 + 1e-5
    assert series["grid"]["import"].max() <= 7 + 1e-6


def test_joint_power_shortage_is_infeasible_even_when_ev_alone_fits():
    # EV alone needs 14 kWh; grid supplies 24 kWh before 08:00. But the house
    # needs 8 kWh and heating needs at least 14 kWh after using initial heat.
    with pytest.raises(OptimizationError, match="Infeasible"):
        household_case(grid_limit=3)


def test_ideal_store_initial_state_maps_to_cumulative_bounds():
    optimizer = EnergyOptimizer(n_intervals=2, interval_hours=DT)
    optimizer.add_mains_electricity_supply("grid", 4, [0.40, 0.10])
    # 1.5 kWh thermal initially stored, 3 kWh thermal capacity, COP 3.
    # The two intervals consume 1.5 kWh thermal in total.
    hp = optimizer.add_heating_consumption("hp", 2, [1, 1], -0.5, 0.5, 0)
    optimizer.problem += pulp.lpSum(hp.values()) * DT == 0.5
    optimizer.solve()
    np.testing.assert_allclose(optimizer.get_time_series()["hp"]["consumption"], [0, 2], atol=1e-6)


def test_limitation_variable_cop_can_leave_heat_unserved():
    optimizer = EnergyOptimizer(n_intervals=2, interval_hours=DT)
    prices = np.array([0.12, 0.20])
    cop = np.array([2.0, 4.0])
    demand_kw_th = np.array([0.0, 4.0])
    optimizer.add_mains_electricity_supply("grid", 4, prices)
    hp = optimizer.add_heating_consumption("hp", 4, demand_kw_th / cop, 0, 1, 0)
    optimizer.problem += pulp.lpSum(hp.values()) * DT == 0.25
    optimizer.solve()
    power = optimizer.get_time_series()["hp"]["consumption"]
    np.testing.assert_allclose(power, [1, 0], atol=1e-6)
    heat_delivered = float(np.dot(power, cop) * DT)
    assert heat_delivered == pytest.approx(0.5)
    assert demand_kw_th.sum() * DT == pytest.approx(1.0)
    # Waiting would supply the full 1 kWh thermal for 0.05 EUR; preheating
    # enough at COP 2 would cost 0.06 EUR. Current schedule costs 0.03 EUR
    # only because it provides half the necessary heat.


def test_limitation_standing_losses_can_empty_a_feasible_store():
    optimizer = EnergyOptimizer(n_intervals=4, interval_hours=DT)
    optimizer.add_mains_electricity_supply("grid", 4, [0.01, 0.20, 0.30, 0.40])
    hp = optimizer.add_heating_consumption("hp", 4, [0, 0, 0, 4 / COP], 0, 1 / COP, 0)
    optimizer.problem += pulp.lpSum(hp.values()) * DT == 1 / COP
    optimizer.solve()
    power = optimizer.get_time_series()["hp"]["consumption"]
    heat = 0.0
    for index in range(4):
        # Deliberately exaggerated synthetic loss to expose the omission.
        heat = 0.9 * heat + power[index] * COP * DT - (1 if index == 3 else 0)
    assert heat == pytest.approx(-0.271, abs=1e-6)


def test_limitation_no_heat_pump_minimum_run_time():
    optimizer = EnergyOptimizer(n_intervals=4, interval_hours=DT)
    optimizer.add_mains_electricity_supply("grid", 2, [0.1, 0.4, 0.1, 0.4])
    hp = optimizer.add_heating_consumption("hp", 2, [1] * 4, 0, 0.25, 0)
    optimizer.problem += pulp.lpSum(hp.values()) * DT == 1
    optimizer.solve()
    # Economically optimal, but cannot implement a 30-minute minimum run.
    np.testing.assert_allclose(optimizer.get_time_series()["hp"]["consumption"], [2, 0, 2, 0], atol=1e-6)


def test_limitation_ev_has_no_minimum_charging_power():
    optimizer = EnergyOptimizer(n_intervals=1, interval_hours=DT)
    optimizer.add_mains_electricity_supply("grid", 7, [0.2])
    optimizer.add_fixed_consumption("house", [1])
    optimizer.add_flexible_consumption("ev", 7.2, [0.1])
    optimizer.solve()
    assert optimizer.get_time_series()["ev"]["consumption"][0] == pytest.approx(0.4)


def test_limitation_terminal_value_does_not_guarantee_heat_reserve():
    optimizer = EnergyOptimizer(n_intervals=4, interval_hours=DT)
    optimizer.add_mains_electricity_supply("grid", 4, [0.4] * 4)
    optimizer.add_heating_consumption("hp", 2, [1] * 4, -1, 1, 0.1)
    optimizer.solve()
    # Initial 3 kWh thermal completely drained despite positive terminal value.
    np.testing.assert_allclose(optimizer.get_time_series()["hp"]["consumption"], 0, atol=1e-6)


if __name__ == "__main__":
    metrics = {}
    for name, kwargs in [
        ("fixed_heating_plus_scheduled_ev", dict(storage=False)),
        ("heat_storage_plus_scheduled_ev", dict(storage=True)),
        ("heat_storage_ev_and_battery", dict(storage=True, battery=True)),
        ("negative_price_heat_storage_ev", dict(negative_prices=True)),
    ]:
        metrics[name] = household_case(**kwargs)[3]
    print(json.dumps(metrics, indent=2))
