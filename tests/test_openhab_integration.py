"""Physical/accounting regressions for the household profile and pure adapter."""

from dataclasses import replace
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pulp
import pytest

from integrations.openhab.profile import EVRequest, HouseholdConfig, build_optimizer
from integrations.openhab.adapter import optimize, select_mode
from optim import OptimizationError


def solve(prices, pv=None, load=None, initial=7, config=None, **kwargs):
    n = len(prices)
    model = build_optimizer(prices, [0] * n if pv is None else pv,
                               [1] * n if load is None else load, initial,
                               config=config, **kwargs)
    model.solve()
    return model, model.result()["series"]


def test_separate_efficiencies_and_discharge_wear():
    c = replace(HouseholdConfig(), terminal_kwh=7.95, terminal_value=0)
    m, s = solve([5], load=[0], config=c)
    assert s["grid_charge_kw"][0] == pytest.approx(4)
    assert s["energy_kwh"][0] == pytest.approx(7.95)
    assert m.result()["objective"] == pytest.approx(5)  # No wear on charging.
    c = replace(c, terminal_kwh=7, charge_kw=0)
    m, s = solve([40], load=[3.8], initial=8, config=c)
    assert s["discharge_kw"][0] == pytest.approx(3.8)
    assert s["energy_kwh"][0] == pytest.approx(7)
    assert m.result()["objective"] == pytest.approx(0.95 * c.wear_per_discharged_kwh)


def test_no_battery_export_even_when_export_is_valuable():
    c = replace(HouseholdConfig(), terminal_kwh=2.4, terminal_value=0, export_value=100)
    _, s = solve([30], load=[0], initial=8, config=c)
    assert s["solar_export_kw"][0] == pytest.approx(0)
    assert s["discharge_kw"][0] == pytest.approx(0)
    assert s["energy_kwh"][0] == pytest.approx(8)


def test_pv_priority_and_grid_only_charge_source_choice():
    c = replace(HouseholdConfig(), terminal_kwh=7.95)
    _, s = solve([5], pv=[5], load=[1], config=c)
    assert s["grid_load_kw"][0] == pytest.approx(0)
    assert s["grid_charge_kw"][0] == pytest.approx(4)
    assert s["solar_charge_kw"][0] == pytest.approx(0)
    assert s["solar_export_kw"][0] == pytest.approx(4)
    _, net = solve([5], pv=[5], load=[1], config=replace(c, allow_grid_charge_while_exporting=False))
    assert net["grid_charge_kw"][0] == pytest.approx(0)
    assert net["solar_charge_kw"][0] == pytest.approx(4)


def test_preserved_quarters_disable_both_discharge_and_pv_charge():
    _, s = solve([40, 40, 5, 5], pv=[0, 2, 0, 0], initial=9,
                 battery_preservation=[True, True, False, False])
    np.testing.assert_allclose(s["discharge_kw"][:2], 0)
    np.testing.assert_allclose(s["solar_charge_kw"][:2], 0)
    assert np.min(s["energy_kwh"][:2]) >= 9 - 1e-5


def test_negative_prices_do_not_cycle_full_battery_or_overfill_ev():
    c = replace(HouseholdConfig(), minimum_kwh=12, terminal_kwh=12)
    ev = EVRequest(17.99, 18, 18.1, 3)
    _, s = solve([-50] * 4, initial=12, config=c, ev=ev)
    np.testing.assert_allclose(s["discharge_kw"], 0, atol=1e-8)
    np.testing.assert_allclose(s["grid_charge_kw"], 0, atol=1e-8)
    # Less than one full 6 A interval fits: explicitly report shortfall, not
    # an impossible fractional-current schedule or energy beyond capacity.
    np.testing.assert_allclose(s["ev_steps"], 0)
    assert s["ev_energy_kwh"][-1] == pytest.approx(17.99)


def test_ev_deadline_availability_current_steps_losses_and_shortfall():
    ev = EVRequest(0, 5, 18.1, 2)
    m, s = solve([5] * 4, ev=ev, ev_availability=[0, 1, 1, 1])
    np.testing.assert_allclose(s["ev_steps"], [0, 32, 32, 0])
    assert s["ev_energy_kwh"][2] == pytest.approx(32 * .230 * .25 * .90 * 2)
    assert m.result()["ev_shortfall_kwh"] == pytest.approx(5 - 32 * .230 * .25 * .90 * 2)


@pytest.mark.parametrize("price,lower,upper", [(5, 18.0, 18.1), (30, 14.48, 14.85)])
def test_ev_optional_band_obeys_economics(price, lower, upper):
    _, s = solve([price] * 48, ev=EVRequest(13.032, 14.48, 18.1, 41))
    current = s["ev_steps"]
    assert np.all((current == 0) | ((current >= 6) & (current <= 32)))
    np.testing.assert_allclose(current, np.round(current))
    assert lower <= s["ev_energy_kwh"][41] <= upper
    np.testing.assert_allclose(current[42:], 0)


@pytest.mark.parametrize("count", [92, 96, 100])
def test_independent_energy_balance_and_objective_reconstruction(count):
    prices = [5 + (i * 7 % 31) for i in range(count)]
    pv = np.maximum(0, 5 * np.sin(np.arange(count) * np.pi / 48))
    load = np.full(count, 0.8)
    ev = EVRequest(12, 14.48, 18.1, 31)
    m, s = solve(prices, pv=pv, load=load, initial=8, ev=ev,
                 battery_preservation=[i < 4 for i in range(count)])
    c = HouseholdConfig()
    direct = np.minimum(pv, load)
    ev_power = s["ev_steps"] * c.ev_voltage / 1000
    np.testing.assert_allclose(direct + s["grid_load_kw"] + s["discharge_kw"] + s["solar_ev_kw"],
                               load + ev_power, atol=1e-6)
    np.testing.assert_allclose(direct + s["solar_charge_kw"] + s["solar_ev_kw"] + s["solar_export_kw"],
                               pv, atol=1e-6)
    charge = s["grid_charge_kw"] + s["solar_charge_kw"]
    np.testing.assert_allclose(s["energy_kwh"], 8 + np.cumsum(
        (charge * c.charge_efficiency - s["discharge_kw"] / c.discharge_efficiency) * .25), atol=1e-5)
    np.testing.assert_allclose(s["ev_energy_kwh"], ev.initial_kwh + np.cumsum(ev_power * .25 * c.ev_efficiency), atol=1e-5)
    assert np.all(charge * s["discharge_kw"] < 1e-6)
    assert np.max(s["grid_load_kw"] + s["grid_charge_kw"]) <= c.grid_import_kw + 1e-6
    assert np.min(s["energy_kwh"]) >= c.minimum_kwh - 1e-6
    assert s["energy_kwh"][-1] >= c.terminal_kwh - 1e-6
    objective = np.sum(((s["grid_load_kw"] + s["grid_charge_kw"]) * prices
                        - s["solar_export_kw"] * c.export_value
                        + s["discharge_kw"] * c.wear_per_discharged_kwh
                        - s["solar_charge_kw"] * c.early_solar_tie_break * np.arange(count, 0, -1)) * .25)
    objective -= s["energy_kwh"][-1] * c.terminal_value
    objective -= s["ev_energy_kwh"][ev.deadline_index] * c.ev_optional_price / c.ev_efficiency
    objective += m.result()["ev_shortfall_kwh"] * c.ev_shortfall_penalty
    assert objective == pytest.approx(m.result()["objective"])


def test_infeasible_household_never_exposes_schedule():
    m = build_optimizer([5], [0], [30], 7)
    with pytest.raises(OptimizationError):
        m.result()
    with pytest.raises(OptimizationError, match="Infeasible"):
        m.solve()
    with pytest.raises(OptimizationError):
        m.result()


def test_household_default_solver_is_bounded_and_rejects_incumbent(monkeypatch):
    m = build_optimizer([5], [0], [1], 7)
    def timed_out(solver):
        assert solver.timeLimit == 30
        m.problem.status = pulp.LpStatusOptimal
        m.problem.sol_status = pulp.LpSolutionIntegerFeasible
        return pulp.LpStatusOptimal
    monkeypatch.setattr(m.problem, "solve", timed_out)
    with pytest.raises(OptimizationError):
        m.solve()
    with pytest.raises(OptimizationError):
        m.result()


@pytest.mark.parametrize("changes", [dict(charge_efficiency=1.1), dict(ev_efficiency=0),
    dict(terminal_kwh=13), dict(minimum_kwh=8), dict(ev_min_current_a=1.5),
    dict(ev_max_current_a=5), dict(solver_seconds=0), dict(grid_import_kw=-1),
    dict(allow_grid_charge_while_exporting=1)])
def test_bad_configuration_rejected(changes):
    with pytest.raises(ValueError):
        replace(HouseholdConfig(), **changes)


@pytest.mark.parametrize("kwargs", [dict(battery_preservation=[0, 0]),
    dict(battery_preservation=[.5]), dict(ev_availability=[1]),
    dict(ev=EVRequest(0, 2, 1, 0)), dict(ev=EVRequest(0, 1, 2, -1))])
def test_bad_inputs_rejected(kwargs):
    with pytest.raises(ValueError):
        build_optimizer([5], [0], [1], 7, **kwargs)


@pytest.mark.parametrize("stamps", [[0, 0], [0, 1800], [1, 901], [0], [0, float("nan")]])
def test_adapter_rejects_misaligned_timestamps(stamps):
    with pytest.raises(ValueError):
        optimize(stamps, [5, 5], [0, 0], [0, 0], [1, 1], 7)


@pytest.mark.parametrize("deadline", [datetime(2026, 1, 1), datetime.fromtimestamp(0, timezone.utc),
                                    datetime.fromtimestamp(3600, timezone.utc)])
def test_adapter_rejects_invalid_deadline(deadline):
    ev = dict(initial_energy_kwh=0, minimum_target_kwh=1, full_target_kwh=2, deadline=deadline)
    with pytest.raises(ValueError):
        optimize([0, 900], [5, 5], [0, 0], [0, 0], [1, 1], 7, ev)


def test_adapter_does_not_round_small_ev_request_to_invalid_current():
    deadline = datetime.fromtimestamp(900, timezone.utc)
    ev = dict(initial_energy_kwh=0, minimum_target_kwh=.1, full_target_kwh=2, deadline=deadline)
    rows, terminal, result = optimize([0], [30], [0], [0], [0], 7, ev)
    assert rows[0]["ev_current_a"] == 6
    assert rows[0]["ev_power_kw"] == 1.38
    assert result["shortfall_kwh"] == 0
    assert terminal == 9


def test_mode_policy_keeps_cloud_support_and_preservation():
    flows = dict(grid_charge_kw=0, solar_charge_kw=0, discharge_kw=0)
    c = HouseholdConfig()
    assert select_mode(flows, 1, 30, False, c) == "SELF_CONSUMPTION"
    assert select_mode(flows, 1, 30, True, c) == "PV_NO_CHARGE"
    assert select_mode(flows, 0, 30, True, c) == "IDLE"
    assert select_mode(dict(flows, grid_charge_kw=4), 1, 5, True, c) == "GRID_ONLY_CHARGE"
    assert select_mode(dict(flows, grid_charge_kw=2, solar_charge_kw=2), 1, 5, False, c) == "GRID_CHARGE"


@pytest.mark.parametrize("month,day,count", [(3, 29, 92), (10, 25, 100)])
def test_adapter_preserves_dst_instants(month, day, count):
    zone = ZoneInfo("Europe/Vilnius")
    start = int(datetime(2026, month, day, tzinfo=zone).timestamp())
    stamps = [start + 900 * i for i in range(count)]
    rows, _, _ = optimize(stamps, [5]*count, [0]*count, [0]*count, [1]*count, 7)
    assert [r["timestamp"] for r in rows] == stamps
    assert len({datetime.fromisoformat(r["datetime"]).timestamp() for r in rows}) == count
    assert datetime.fromisoformat(rows[0]["datetime"]).utcoffset() != datetime.fromisoformat(rows[-1]["datetime"]).utcoffset()
