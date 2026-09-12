import numpy as np
import pulp
import pytest

from optim import EnergyOptimizer, OptimizationError


def add_battery(optimizer, **overrides):
    parameters = dict(name="battery", capacity=10, initial_soc=0,
                      efficiency=0.9, max_charge_power=4, max_discharge_power=4,
                      cost_of_cycle_kwh=0, final_energy_value_per_kwh=0)
    parameters.update(overrides)
    return optimizer.add_battery(**parameters)


@pytest.mark.parametrize("count", [92, 96, 100])
def test_quarter_hour_grid_energy_and_cost(count):
    # No hard-coded day length: 23-, 24- and 25-hour price schedules work.
    optimizer = EnergyOptimizer(n_intervals=count, interval_hours=0.25)
    optimizer.add_mains_electricity_supply("grid", 5, [0.20] * count)
    optimizer.add_fixed_consumption("house", [4] * count)
    optimizer.solve()
    imported = optimizer.get_time_series()["grid"]["import"]
    assert imported.sum() * 0.25 == pytest.approx(count)
    assert pulp.value(optimizer.total_cost) == pytest.approx(count * 0.20)


def test_quarter_hour_charging_wear_and_terminal_value():
    optimizer = EnergyOptimizer(n_intervals=1, interval_hours=0.25)
    optimizer.add_mains_electricity_supply("grid", 4, [0.20])
    add_battery(optimizer, cost_of_cycle_kwh=0.03, final_energy_value_per_kwh=1)
    optimizer.solve()
    series = optimizer.get_time_series()
    assert series["battery"]["charge_rate"][0] == pytest.approx(4)
    assert series["battery"]["soc"][0] == pytest.approx(1)
    # 1 kWh imported + wear, less 0.9 kWh deliverable terminal energy.
    assert pulp.value(optimizer.total_cost) == pytest.approx(0.20 + 0.03 - 0.9)


def test_quarter_hour_discharge_losses():
    optimizer = EnergyOptimizer(n_intervals=1, interval_hours=0.25)
    optimizer.add_mains_electricity_supply("grid", 5, [0.20])
    optimizer.add_fixed_consumption("house", [3.6])
    add_battery(optimizer, initial_soc=1)
    optimizer.solve()
    series = optimizer.get_time_series()
    assert series["battery"]["discharge_rate"][0] == pytest.approx(-4)
    assert series["battery"]["soc"][0] == pytest.approx(0)
    assert series["grid"]["import"][0] == pytest.approx(0)


def test_solar_export_cost_and_power_limit():
    optimizer = EnergyOptimizer(n_intervals=1, interval_hours=0.25)
    optimizer.add_mains_electricity_supply("grid", 5, [0.20], 2, [0.10])
    optimizer.add_solar_production("solar", [4])
    optimizer.solve()
    series = optimizer.get_time_series()
    assert series["solar"]["production"][0] == pytest.approx(2)
    assert series["grid"]["export"][0] == pytest.approx(-2)
    assert pulp.value(optimizer.total_cost) == pytest.approx(-0.05)


def test_negative_price_cannot_cycle_full_battery():
    optimizer = EnergyOptimizer(n_intervals=1, interval_hours=0.25)
    optimizer.add_mains_electricity_supply("grid", 10, [-100])
    add_battery(optimizer, initial_soc=10, min_soc=10, max_soc=10,
                cost_of_cycle_kwh=1)
    optimizer.solve()
    series = optimizer.get_time_series()
    assert series["battery"]["charge_rate"][0] == pytest.approx(0)
    assert series["battery"]["discharge_rate"][0] == pytest.approx(0)
    assert series["grid"]["import"][0] == pytest.approx(0)


def test_ev_deadline_energy_cap_and_availability():
    optimizer = EnergyOptimizer(n_intervals=4, interval_hours=0.25)
    optimizer.add_mains_electricity_supply("grid", 10, [-100, -1, -2, -100])
    optimizer.add_flexible_consumption("ev", 4, [0, 0, 1, 0],
                                       availability=[0, 1, 1, 0])
    optimizer.solve()
    charging = optimizer.get_time_series()["ev"]["consumption"]
    np.testing.assert_allclose(charging, [0, 0, 4, 0], atol=1e-6)
    assert np.sum(charging) * 0.25 == pytest.approx(1)


def test_ev_can_explicitly_accept_more_energy():
    optimizer = EnergyOptimizer(n_intervals=2, interval_hours=0.25)
    optimizer.add_mains_electricity_supply("grid", 4, [-1, -2])
    optimizer.add_flexible_consumption("ev", 4, [0, 0.5],
                                       max_cumulative_consumption=[0, 1])
    optimizer.solve()
    np.testing.assert_allclose(optimizer.get_time_series()["ev"]["consumption"], [0, 4])


def test_ev_unreachable_quarter_hour_deadline_is_rejected():
    optimizer = EnergyOptimizer(n_intervals=1, interval_hours=0.25)
    optimizer.add_mains_electricity_supply("grid", 10, [1])
    optimizer.add_flexible_consumption("ev", 4, [2])
    with pytest.raises(OptimizationError, match="Infeasible"):
        optimizer.solve()


def test_heating_tolerance_is_energy_not_power():
    optimizer = EnergyOptimizer(n_intervals=2, interval_hours=0.25)
    optimizer.add_mains_electricity_supply("grid", 4, [0.10, 0.40])
    optimizer.add_heating_consumption("heat", 4, [2, 2], 0, 0.5, 0)
    optimizer.solve()
    np.testing.assert_allclose(optimizer.get_time_series()["heat"]["consumption"], [4, 0])
    assert pulp.value(optimizer.total_cost) == pytest.approx(0.10)


def test_heating_terminal_value_is_not_scaled_twice():
    optimizer = EnergyOptimizer(n_intervals=1, interval_hours=0.25)
    optimizer.add_mains_electricity_supply("grid", 4, [0.20])
    optimizer.add_heating_consumption("heat", 4, [2], 0, 0.5, 1)
    optimizer.solve()
    assert pulp.value(optimizer.total_cost) == pytest.approx(0.20 - 0.5)


def test_hourly_and_quarter_hour_models_agree():
    results = []
    for subdivisions in (1, 4):
        dt = 1 / subdivisions
        repeat = lambda values: np.repeat(values, subdivisions)
        optimizer = EnergyOptimizer(n_intervals=4 * subdivisions, interval_hours=dt)
        optimizer.add_mains_electricity_supply("grid", 5, repeat([0.10, 0.20, 0.50, 0.30]),
                                               2, repeat([0.02] * 4))
        optimizer.add_fixed_consumption("house", repeat([1, 1, 2, 2]))
        optimizer.add_solar_production("solar", repeat([0, 2, 1, 0]))
        add_battery(optimizer, capacity=4, initial_soc=1, cost_of_cycle_kwh=0.03,
                    final_energy_value_per_kwh=0.15)
        optimizer.solve()
        series = optimizer.get_time_series()
        results.append([pulp.value(optimizer.total_cost),
                        series["battery"]["soc"][-1],
                        series["grid"]["import"].sum() * dt])
    np.testing.assert_allclose(results[0], results[1], atol=1e-6)


def test_failed_solve_never_exposes_dispatch():
    optimizer = EnergyOptimizer(n_intervals=1, interval_hours=0.25)
    optimizer.add_mains_electricity_supply("grid", 1, [10])
    optimizer.add_fixed_consumption("house", [2])
    with pytest.raises(OptimizationError):
        optimizer.get_time_series()
    with pytest.raises(OptimizationError, match="Infeasible"):
        optimizer.solve()
    with pytest.raises(OptimizationError):
        optimizer.get_time_series()


def test_repeated_solve_and_solver_failure(monkeypatch):
    optimizer = EnergyOptimizer(n_hours=1)  # Historical API remains hourly.
    optimizer.add_mains_electricity_supply("grid", 2, [1])
    optimizer.add_fixed_consumption("house", [1])
    optimizer.solve()
    count = len(optimizer.problem.constraints)
    optimizer.solve()
    assert len(optimizer.problem.constraints) == count
    assert pulp.value(optimizer.total_cost) == pytest.approx(1)
    with pytest.raises(RuntimeError):
        optimizer.add_fixed_consumption("extra", [1])

    def fail(*args):
        raise pulp.PulpSolverError("solver unavailable")

    monkeypatch.setattr(optimizer.problem, "solve", fail)
    with pytest.raises(OptimizationError, match="Solver failed"):
        optimizer.solve()
    with pytest.raises(OptimizationError):
        optimizer.get_time_series()


def test_feasible_but_nonoptimal_incumbent_is_not_exposed(monkeypatch):
    optimizer = EnergyOptimizer(1)
    optimizer.add_fixed_consumption("house", [0])

    def timed_out(*args):
        optimizer.problem.status = pulp.LpStatusOptimal
        optimizer.problem.sol_status = pulp.LpSolutionIntegerFeasible
        return pulp.LpStatusOptimal

    monkeypatch.setattr(optimizer.problem, "solve", timed_out)
    with pytest.raises(OptimizationError):
        optimizer.solve()
    with pytest.raises(OptimizationError):
        optimizer.get_time_series()


def test_zero_limits_and_zero_cost_model():
    optimizer = EnergyOptimizer(1)
    optimizer.add_mains_electricity_supply("grid", 0, [0])
    add_battery(optimizer, max_charge_power=0, max_discharge_power=0)
    optimizer.solve()
    assert all(np.all(np.isfinite(values)) for device in optimizer.get_time_series().values()
               for values in device.values())


@pytest.mark.parametrize("kwargs", [dict(n_intervals=0), dict(n_intervals=1.5),
    dict(n_intervals=1, interval_hours=0), dict(n_intervals=1, interval_hours=float("nan")),
    dict(n_hours=1, n_intervals=1)])
def test_invalid_interval_configuration(kwargs):
    with pytest.raises(ValueError):
        EnergyOptimizer(**kwargs)


@pytest.mark.parametrize("overrides", [dict(efficiency=0), dict(efficiency=1.1),
    dict(capacity=-1), dict(initial_soc=11), dict(initial_soc=float("nan")),
    dict(min_soc=5, max_soc=4), dict(max_soc=11), dict(min_soc=-1)])
def test_invalid_battery_inputs(overrides):
    with pytest.raises(ValueError):
        add_battery(EnergyOptimizer(1), **overrides)


@pytest.mark.parametrize("values", [[1], [1, float("inf")], [1, -1]])
def test_invalid_consumption_forecasts(values):
    with pytest.raises(ValueError):
        EnergyOptimizer(n_intervals=2, interval_hours=0.25).add_fixed_consumption("house", values)
