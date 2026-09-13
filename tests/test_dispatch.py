"""Generic capabilities tested independently of any installation integration."""

from dataclasses import replace
from pathlib import Path
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

from dispatch import Battery, Charger, DispatchOptimizer, EVRequest, FlowPolicy, Grid
from optim import OptimizationError


def run(*, prices=(.2,), solar=(0,), load=(0,), initial=3, battery=None,
        grid=None, **kwargs):
    model = DispatchOptimizer(prices, solar, load, initial,
                              battery=battery or Battery(8, 3, 3),
                              grid=grid or Grid(8, 4), **kwargs)
    model.solve()
    return model, model.result()["series"]


@pytest.mark.parametrize("enabled,expected", [(False, 0), (True, 3)])
def test_battery_export_is_a_capability(enabled, expected):
    _, s = run(export_prices=.5, policy=FlowPolicy(allow_battery_export=enabled))
    assert s["battery_export_kw"][0] == pytest.approx(expected)
    assert s["discharge_kw"][0] == pytest.approx(expected)
    assert s["solar_export_kw"][0] == pytest.approx(0)


def test_export_limit_applies_to_combined_pv_and_battery():
    _, s = run(solar=[1], grid=Grid(0, 2), export_prices=.5,
               policy=FlowPolicy(allow_battery_export=True))
    assert s["solar_export_kw"][0] + s["battery_export_kw"][0] == pytest.approx(2)
    assert s["energy_kwh"][0] >= 1 - 1e-6


@pytest.mark.parametrize("allowed", [False, True])
def test_pv_curtailment_is_configurable(allowed):
    args = dict(solar=[5], battery=Battery(8, 0, 0), grid=Grid(0, 0),
                policy=FlowPolicy(allow_pv_curtailment=allowed))
    if allowed:
        _, s = run(**args)
        assert s["solar_curtailed_kw"][0] == pytest.approx(5)
    else:
        with pytest.raises(OptimizationError, match="Infeasible"):
            run(**args)


@pytest.mark.parametrize("priority,export", [(False, 1), (True, 0)])
def test_pv_priority_is_not_an_installation_assumption(priority, export):
    _, s = run(solar=[1], load=[1], export_prices=.5,
               policy=FlowPolicy(pv_priority_to_load=priority))
    assert s["solar_export_kw"][0] == pytest.approx(export)
    assert s["solar_load_kw"][0] == pytest.approx(1-export)


@pytest.mark.parametrize("simultaneous,charged", [(False, 0), (True, 1)])
def test_import_export_exclusion_is_configurable(simultaneous, charged):
    _, s = run(prices=[-.2], solar=[1], initial=7,
               export_prices=.5, battery=Battery(8, 1, 0),
               policy=FlowPolicy(allow_simultaneous_grid_import_export=simultaneous))
    assert s["grid_charge_kw"][0] == pytest.approx(charged)
    assert s["solar_export_kw"][0] == pytest.approx(1)


def test_permissions_control_sources_independently():
    # Disallow grid charging without forbidding PV charging.
    _, s = run(prices=[-.2], solar=[2], initial=1, grid_charge_allowed=[False],
               battery=Battery(8, 3, 3, terminal_kwh=3), policy=FlowPolicy(pv_priority_to_load=True))
    assert s["grid_charge_kw"][0] == pytest.approx(0)
    assert s["solar_charge_kw"][0] == pytest.approx(2)
    # Disallow PV charging without forbidding grid charging or export.
    _, s = run(solar=[2], initial=1, solar_charge_allowed=[False],
               battery=Battery(8, 3, 3, terminal_kwh=3),
               policy=FlowPolicy(allow_simultaneous_grid_import_export=True))
    assert s["solar_charge_kw"][0] == pytest.approx(0)
    assert s["grid_charge_kw"][0] == pytest.approx(2)
    _, s = run(load=[2], discharge_allowed=[False])
    assert s["discharge_kw"][0] == pytest.approx(0)
    assert s["grid_load_kw"][0] == pytest.approx(2)


def test_generic_charger_power_steps_and_hard_target():
    _, s = run(prices=[.1,.4], solar=[0,0], load=[0,0],
               battery=Battery(8, 0, 0), interval_hours=.5,
               charger=Charger(.5, 2, 8, efficiency=.8),
               ev=EVRequest(2, 3.2, 5, 1), ev_availability=[1,0])
    np.testing.assert_allclose(s["ev_steps"], [6,0])
    np.testing.assert_allclose(s["ev_power_kw"], [3,0])
    assert s["ev_energy_kwh"][-1] == pytest.approx(3.2)
    with pytest.raises(OptimizationError, match="Infeasible"):
        run(charger=Charger(.5, 2, 4), ev=EVRequest(0, 3, 5, 0))


def test_soft_ev_target_reports_shortfall():
    m, _ = run(charger=Charger(.5, 2, 4, shortfall_penalty=100),
               ev=EVRequest(0, 3, 5, 0))
    assert m.result()["ev_shortfall_kwh"] == pytest.approx(1)


def test_export_tariff_is_a_series_and_cost_is_currency_agnostic():
    m, s = run(prices=[.1,.1], solar=[0,0], load=[0,0], initial=3,
               export_prices=[.1,.5], policy=FlowPolicy(allow_battery_export=True))
    np.testing.assert_allclose(s["battery_export_kw"], [0,3])
    assert m.result()["objective"] == pytest.approx(-1.5)


@pytest.mark.parametrize("factory", [lambda: Battery(8, -1, 2),
    lambda: Battery(8, 2, 2, minimum_kwh=9), lambda: Grid(1, -2),
    lambda: Charger(.2, 1.5, 4), lambda: Charger(.2, 1, 4, efficiency=1.2),
    lambda: Charger(.2, 1, 4, shortfall_penalty=0), lambda: FlowPolicy(allow_battery_export=1)])
def test_invalid_equipment_rejected(factory):
    with pytest.raises(ValueError):
        factory()


def test_generic_example_runs_without_integration_files(tmp_path):
    root = Path(__file__).resolve().parents[1]
    for name in ("optim.py", "dispatch.py", "example_dispatch.py"):
        shutil.copyfile(root / name, tmp_path / name)
    completed = subprocess.run([sys.executable, str(tmp_path / "example_dispatch.py")],
                               cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(tmp_path)),
                               capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
    assert "EV power (kW):" in completed.stdout
