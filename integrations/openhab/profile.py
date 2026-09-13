"""Installation settings and input translation for one openHAB household.

This module owns cents/kWh defaults, IMEON source policy and preservation rules.
It builds a generic DispatchOptimizer; no optimization equations live here.
"""

from dataclasses import dataclass
from numbers import Integral

import numpy as np

from dispatch import Battery, Charger, DispatchOptimizer, EVRequest, FlowPolicy, Grid
from optim import EnergyOptimizer


@dataclass(frozen=True)
class HouseholdConfig:
    capacity_kwh: float = 12.0
    minimum_kwh: float = 2.4
    terminal_kwh: float = 7.0
    charge_kw: float = 4.0
    discharge_kw: float = 5.0
    grid_import_kw: float = 22.0
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.95
    wear_per_discharged_kwh: float = 2425 * 100 / (12 * 6000)
    export_value: float = 9.0
    terminal_value: float = 9.0
    early_solar_tie_break: float = 0.001
    ev_voltage: float = 230.0
    ev_min_current_a: int = 6
    ev_max_current_a: int = 32
    ev_efficiency: float = 0.90
    ev_optional_price: float = 15.0
    ev_shortfall_penalty: float = 10000.0
    solver_seconds: float = 30.0
    # Reproduces the current source-accounting policy. This does NOT establish
    # whether a particular installation settles gross or net meter energy.
    allow_grid_charge_while_exporting: bool = True

    def __post_init__(self):
        positive = {"capacity_kwh", "charge_efficiency", "discharge_efficiency",
                    "ev_voltage", "ev_efficiency", "ev_shortfall_penalty", "solver_seconds"}
        signed = {"export_value", "terminal_value", "ev_optional_price"}
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if name == "allow_grid_charge_while_exporting":
                if not isinstance(value, bool):
                    raise ValueError(f"{name} must be boolean")
                continue
            if name in {"ev_min_current_a", "ev_max_current_a"}:
                if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
                    raise ValueError(f"{name} must be a positive integer")
                continue
            value = EnergyOptimizer._number(value, name, positive=name in positive,
                                            minimum=None if name in signed else 0)
            object.__setattr__(self, name, value)
        if not 0 <= self.minimum_kwh <= self.terminal_kwh <= self.capacity_kwh:
            raise ValueError("Require 0 <= minimum <= terminal <= capacity")
        if max(self.charge_efficiency, self.discharge_efficiency, self.ev_efficiency) > 1:
            raise ValueError("Efficiencies must be in (0, 1]")
        if self.ev_min_current_a > self.ev_max_current_a:
            raise ValueError("EV minimum current exceeds maximum")


def preservation_mask(values, count):
    mask = np.asarray(False if values is None else values)
    if mask.ndim == 0:
        mask = np.full(count, mask.item())
    if mask.shape != (count,) or not np.all((mask == 0) | (mask == 1)):
        raise ValueError("battery_preservation must contain one boolean/0/1 per interval")
    return mask.astype(bool)


def build_optimizer(prices, solar_kw, load_kw, initial_kwh, *, config=None,
                    interval_hours=0.25, ev=None, ev_availability=None,
                    battery_preservation=None):
    """Translate this home's policy into generic equipment and permissions."""
    c = config or HouseholdConfig()
    count = len(prices)
    preserved = preservation_mask(battery_preservation, count)
    battery = Battery(capacity_kwh=c.capacity_kwh, max_charge_kw=c.charge_kw,
                      max_discharge_kw=c.discharge_kw, minimum_kwh=c.minimum_kwh,
                      terminal_kwh=c.terminal_kwh, charge_efficiency=c.charge_efficiency,
                      discharge_efficiency=c.discharge_efficiency,
                      wear_per_discharged_kwh=c.wear_per_discharged_kwh,
                      terminal_value=c.terminal_value)
    # The saved rule has no additional export limit, and only PV may export.
    # The forecast maximum is therefore a sufficient bound for this profile.
    grid = Grid(c.grid_import_kw, float(np.max(solar_kw)) if count else 0)
    charger = None if ev is None else Charger(
        power_step_kw=c.ev_voltage / 1000, min_steps=c.ev_min_current_a,
        max_steps=c.ev_max_current_a, efficiency=c.ev_efficiency,
        terminal_value=c.ev_optional_price / c.ev_efficiency,
        shortfall_penalty=c.ev_shortfall_penalty)
    return DispatchOptimizer(
        prices, solar_kw, load_kw, initial_kwh, battery=battery, grid=grid,
        export_prices=c.export_value, interval_hours=interval_hours,
        policy=FlowPolicy(pv_priority_to_load=True, allow_battery_export=False,
                          allow_pv_curtailment=False,
                          allow_simultaneous_grid_import_export=c.allow_grid_charge_while_exporting),
        charger=charger, ev=ev, ev_availability=ev_availability,
        solar_charge_allowed=~preserved, discharge_allowed=~preserved,
        solar_charge_preference=c.early_solar_tie_break * np.arange(count, 0, -1),
        solver_seconds=c.solver_seconds)
