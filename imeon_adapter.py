"""Pure adapter for the saved openHAB optimizer's optimize() contract.

This module emits planned modes, not commands. The existing openHAB rule must
retain its fresh-state checks, ownership, recovery, dispatch and readback logic.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

from household import EVRequest, HouseholdConfig, HouseholdOptimizer


def optimize(timestamps, prices, solar_kw, solar_p50_kw, load_kw, initial_energy,
             ev_input=None, battery_preservation=None, *, config=None,
             timezone="Europe/Vilnius", solver=None):
    """Compatible return tuple: (rounded schedule rows, terminal value, EV result).

    Timestamps are consecutive UTC epoch seconds at full 15-minute boundaries.
    Missing quarters, duplicate instants, naive EV deadlines and deadlines beyond
    the price horizon are rejected. No telemetry-age claims are made here.
    Prices and default monetary configuration are cents per kWh.
    """
    c = config or HouseholdConfig()
    stamps = np.asarray(timestamps, dtype=float)
    count = len(prices)
    if (stamps.shape != (count,) or count == 0 or not np.all(np.isfinite(stamps))
            or np.any(stamps % 900 != 0) or np.any(np.diff(stamps) != 900)):
        raise ValueError("Timestamps must be consecutive full 15-minute UTC intervals")
    tz = ZoneInfo(timezone)
    ev = None
    if ev_input is not None:
        deadline = ev_input["deadline"]
        if isinstance(deadline, str):
            deadline = datetime.fromisoformat(deadline)
        if not isinstance(deadline, datetime) or deadline.utcoffset() is None:
            raise ValueError("EV deadline must be timezone-aware")
        end = deadline.timestamp()
        eligible = np.flatnonzero(stamps + 900 <= end)
        if not len(eligible) or end > stamps[-1] + 900:
            raise ValueError("EV deadline must include a full interval and be within the horizon")
        ev = EVRequest(ev_input["initial_energy_kwh"], ev_input["minimum_target_kwh"],
                       ev_input["full_target_kwh"], int(eligible[-1]))
    model = HouseholdOptimizer(prices, solar_kw, load_kw, initial_energy,
                               config=c, ev=ev, battery_preservation=battery_preservation)
    p50 = model._series(solar_p50_kw, "solar_p50_kw", minimum=0)
    model.solve(solver)
    result = model.result()
    s = result["series"]
    rows = []
    for i in model.intervals:
        raw = {name: float(values[i]) for name, values in s.items()}
        current = int(round(raw["ev_current_a"]))
        mode = select_mode(raw, model.solar_kw[i], model.prices[i], model.preserved[i], c)
        rows.append({
            "timestamp": int(stamps[i]), "datetime": datetime.fromtimestamp(stamps[i], tz).isoformat(),
            "battery_preserved": bool(model.preserved[i]), "price": round(float(model.prices[i]), 6),
            "load_kw": round(float(model.load_kw[i]), 3), "solar_kw": round(float(model.solar_kw[i]), 3),
            "solar_p50_kw": round(float(p50[i]), 3), "solar_direct_kw": round(float(model.direct_solar[i]), 3),
            **{name: round(raw[name], 3) for name in (
                "solar_charge_kw", "solar_export_kw", "solar_ev_kw", "grid_load_kw",
                "grid_charge_kw", "discharge_kw", "energy_kwh")},
            "solar_curtailed_kw": 0.0,
            "grid_kw": round(raw["grid_load_kw"] + raw["grid_charge_kw"], 3),
            "charge_kw": round(raw["grid_charge_kw"] + raw["solar_charge_kw"], 3),
            "minimum_energy_kwh": c.minimum_kwh, "ev_current_a": current,
            "ev_power_kw": round(current * c.ev_voltage / 1000, 3),
            "ev_energy_kwh": round(raw["ev_energy_kwh"], 3) if ev else None, "mode": mode,
        })
    ev_result = None
    if ev:
        ev_result = dict(ev_input)
        ev_result.update(deadline=deadline.isoformat(),
                         shortfall_kwh=round(result["ev_shortfall_kwh"], 3),
                         planned_deadline_energy_kwh=round(float(s["ev_energy_kwh"][ev.deadline_index]), 3))
    return rows, c.terminal_value, ev_result


def select_mode(flows, solar_kw, price, preserved, config):
    """Preserve the authoritative mode policy; thresholds use unrounded flows."""
    epsilon = 0.05
    if flows["grid_charge_kw"] > epsilon:
        return "GRID_CHARGE" if flows["solar_charge_kw"] > epsilon else "GRID_ONLY_CHARGE"
    if preserved:
        return "PV_NO_CHARGE" if solar_kw > epsilon else "IDLE"
    if flows["discharge_kw"] > epsilon or flows["solar_charge_kw"] > epsilon:
        return "SELF_CONSUMPTION"
    support_price = config.export_value / config.discharge_efficiency + config.wear_per_discharged_kwh
    if solar_kw > epsilon and price > support_price:
        return "SELF_CONSUMPTION"
    return "PV_NO_CHARGE" if solar_kw > epsilon else "IDLE"
