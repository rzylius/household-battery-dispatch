"""Shareable engine example using explicit, illustrative EUR/kWh inputs."""

from dispatch import Battery, Charger, DispatchOptimizer, EVRequest, FlowPolicy, Grid


def run_example():
    model = DispatchOptimizer(
        prices=[0.10, 0.15, 0.40, 0.25], solar_kw=[0, 4, 2, 0],
        load_kw=[1, 1.5, 2, 1], initial_kwh=3,
        battery=Battery(capacity_kwh=8, max_charge_kw=3, max_discharge_kw=3,
                        minimum_kwh=1, terminal_kwh=3, charge_efficiency=0.92,
                        discharge_efficiency=0.94, wear_per_discharged_kwh=0.02),
        grid=Grid(max_import_kw=11, max_export_kw=4),
        export_prices=[0.04, 0.04, 0.20, 0.08],
        interval_hours=1,
        policy=FlowPolicy(allow_battery_export=True),
        charger=Charger(power_step_kw=0.5, min_steps=2, max_steps=10, efficiency=0.9),
        ev=EVRequest(initial_kwh=5, minimum_kwh=8, capacity_kwh=20, deadline_index=3),
    )
    model.solve()
    result = model.result()
    print(f"Objective: EUR {result['objective']:.3f}")
    print("Battery energy (kWh):", result["series"]["energy_kwh"].tolist())
    print("EV power (kW):", result["series"]["ev_power_kw"].tolist())
    return result


if __name__ == "__main__":
    run_example()
