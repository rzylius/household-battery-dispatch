"""Prepare a local openHAB rule candidate changing only its planning method.

Does not install files or contact openHAB. Review the resulting diff before
deployment, and make the library modules importable in the HABApp environment.
"""

import argparse
import ast
from pathlib import Path


CONFIG_CONSTANTS = {
    "capacity_kwh": "BATTERY_CAPACITY_KWH",
    "minimum_kwh": "MIN_ENERGY_KWH",
    "terminal_kwh": "FINAL_ENERGY_KWH",
    "charge_kw": "MAX_CHARGE_KW",
    "discharge_kw": "MAX_DISCHARGE_KW",
    "grid_import_kw": "MAX_GRID_IMPORT_KW",
    "charge_efficiency": "CHARGE_EFFICIENCY",
    "discharge_efficiency": "DISCHARGE_EFFICIENCY",
    "wear_per_discharged_kwh": "DEGRADATION_COST_PER_DISCHARGED_KWH",
    "export_value": "EXPORT_VALUE_PER_KWH",
    "terminal_value": "EXPORT_VALUE_PER_KWH",
    "early_solar_tie_break": "EARLY_SOLAR_TIE_BREAK",
    "ev_voltage": "EV_VOLTAGE",
    "ev_min_current_a": "EV_MIN_CURRENT_A",
    "ev_max_current_a": "EV_MAX_CURRENT_A",
    "ev_efficiency": "EV_CHARGE_EFFICIENCY",
    "ev_optional_price": "EV_OPTIONAL_MAX_PRICE_PER_KWH",
}


def build_candidate(source):
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ImeonQuarterHourOptimizer")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "optimize")
    expected = ["self", "timestamps", "prices", "solar_kw", "solar_p50_kw", "load_kw",
                "initial_energy", "ev_input", "battery_preservation"]
    if [arg.arg for arg in method.args.args] != expected or method.decorator_list:
        raise ValueError("Unrecognized optimize signature; review the integration manually")
    assigned = {target.id for n in tree.body if isinstance(n, ast.Assign)
                for target in n.targets if isinstance(target, ast.Name)}
    if not set(CONFIG_CONSTANTS.values()) <= assigned:
        raise ValueError("Missing household configuration constants")
    body = [
        "    def optimize(self, timestamps, prices, solar_kw, solar_p50_kw, load_kw,",
        "                 initial_energy, ev_input=None, battery_preservation=None):",
        '        """Delegate planning; all surrounding dispatch safeguards remain here."""',
        "        from integrations.openhab.profile import HouseholdConfig",
        "        from integrations.openhab.adapter import optimize as plan_household",
        "        config = HouseholdConfig(",
        *[f"            {field}={constant}," for field, constant in CONFIG_CONSTANTS.items()],
        "        )",
        "        return plan_household(",
        "            timestamps, prices, solar_kw, solar_p50_kw, load_kw, initial_energy,",
        "            ev_input, battery_preservation=battery_preservation, config=config,",
        "        )",
    ]
    lines = source.splitlines(keepends=True)
    candidate = "".join(lines[:method.lineno - 1]) + "\n".join(body) + "\n" + "".join(lines[method.end_lineno:])
    compile(candidate, "<openhab-candidate>", "exec")
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        parser.error("Output must be a separate candidate file")
    candidate = build_candidate(args.source.read_text())
    with args.output.open("x") as output:
        output.write(candidate)


if __name__ == "__main__":
    main()
