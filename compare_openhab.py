"""Offline comparison with a trusted local openHAB optimizer source file.

Only its optimize method and referenced numerical constants are loaded. No
HABApp imports, Rule constructor, HTTP, device dispatch or publication runs.
This is a replay utility, not a sandbox for executing untrusted Python files.
"""

import argparse
import ast
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pulp

from household import HouseholdConfig
from imeon_adapter import optimize
from make_openhab_candidate import CONFIG_CONSTANTS
from optim import OptimizationError


class RecordingCBC(pulp.PULP_CBC_CMD):
    def actualSolve(self, problem, **kwargs):
        self.problem = problem
        return super().actualSolve(problem, **kwargs)


def load_legacy(path):
    """Extract only the trusted pure method and its constant dependencies."""
    source = Path(path).read_text()
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "ImeonQuarterHourOptimizer")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "optimize")
    assignments = {node.targets[0].id: node for node in tree.body if isinstance(node, ast.Assign)
                   and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)}
    required = set()
    def collect(node):
        for name in {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and n.id.isupper()}:
            if name != "TZ" and name in assignments and name not in required:
                required.add(name)
                collect(assignments[name])
    collect(method)
    kept = [node for node in tree.body if isinstance(node, ast.Assign)
            and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in required]
    legacy_problems = []
    class PulpProxy:
        def __getattr__(self, name):
            return getattr(pulp, name)

        def LpProblem(self, *args, **kwargs):
            problem = pulp.LpProblem(*args, **kwargs)
            legacy_problems.append(problem)
            return problem

    namespace = dict(datetime=datetime, timedelta=timedelta,
                     TZ=ZoneInfo("Europe/Vilnius"), pulp=PulpProxy())
    exec(compile(ast.Module(body=kept + [method], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["optimize"], legacy_problems, hashlib.sha256(source.encode()).hexdigest()


def validate_problem(problem):
    if problem.status != pulp.LpStatusOptimal or problem.sol_status != pulp.LpSolutionOptimal:
        raise OptimizationError("Replay did not obtain a proven optimal solution")
    for variable in problem.variables():
        if variable.name == "__dummy" and variable.lowBound == variable.upBound == 0:
            continue
        if variable.value() is None or not np.isfinite(variable.value()) or not variable.valid(1e-5):
            raise OptimizationError("Replay solver returned invalid values")
    if not all(constraint.valid(1e-5) for constraint in problem.constraints.values()):
        raise OptimizationError("Replay solver returned infeasible values")


def compare_case(case, legacy, problems):
    solver = RecordingCBC(msg=False, timeLimit=30)
    args = [case[key] for key in ("timestamps", "prices", "solar_kw", "solar_p50_kw", "load_kw", "initial_energy")]
    ev = case.get("ev_input")
    if ev:
        ev = dict(ev, deadline=datetime.fromisoformat(ev["deadline"]))
    kwargs = dict(ev_input=ev, battery_preservation=case.get("battery_preservation"))
    old_rows, _, old_ev = legacy(None, *args, **kwargs)
    config = HouseholdConfig(**{field: legacy.__globals__[constant]
                                for field, constant in CONFIG_CONSTANTS.items()})
    new_rows, _, new_ev = optimize(*args, **kwargs, solver=solver, config=config)
    validate_problem(problems[-1])
    validate_problem(solver.problem)
    old_objective = float(pulp.value(problems[-1].objective))
    new_objective = float(pulp.value(solver.problem.objective))
    delta = new_objective - old_objective
    # Rounded presentation rows may differ across equally optimal solutions.
    # Compare the raw objective; report action/state differences separately.
    tolerance = 0.001  # cents in the household profile (EUR 0.00001).
    return dict(
        name=case["name"], intervals=len(old_rows),
        equivalent_objective=abs(delta) <= tolerance,
        legacy_objective_ct=old_objective, candidate_objective_ct=new_objective,
        objective_delta_ct=delta,
        first_mode_legacy=old_rows[0]["mode"], first_mode_candidate=new_rows[0]["mode"],
        differing_mode_intervals=sum(a["mode"] != b["mode"] for a, b in zip(old_rows, new_rows)),
        differing_ev_current_intervals=sum(a["ev_current_a"] != b["ev_current_a"] for a, b in zip(old_rows, new_rows)),
        max_battery_state_delta_kwh=max(abs(a["energy_kwh"] - b["energy_kwh"]) for a, b in zip(old_rows, new_rows)),
        legacy_ev_shortfall_kwh=old_ev["shortfall_kwh"] if old_ev else None,
        candidate_ev_shortfall_kwh=new_ev["shortfall_kwh"] if new_ev else None,
    )


def synthetic_cases():
    start = int(datetime(2026, 9, 12, 18, tzinfo=timezone.utc).timestamp())
    for name, price, pv, initial, ev, preserved in [
        ("night_arbitrage", [40]*8+[5]*24+[30]*16, [0]*48, 9, None, None),
        ("preserved_ev_window", [40]*8+[5]*24+[30]*16, [0]*48, 9, None, [True]*8+[False]*40),
        ("pv_source_choice", [5]*24+[30]*24, [4]*24+[0]*24, 7, None, None),
        ("negative_prices", [-10]*16+[30]*32, [0]*48, 7, True, None),
        ("ev_expensive", [30]*48, [0]*48, 7, True, None),
        ("ev_cheap", [5]*48, [0]*48, 7, True, None),
    ]:
        stamps = [start + 900*i for i in range(48)]
        case = dict(name=name, timestamps=stamps, prices=price, solar_kw=pv,
                    solar_p50_kw=pv, load_kw=[.8]*48, initial_energy=initial,
                    battery_preservation=preserved)
        if ev:
            case["ev_input"] = dict(initial_energy_kwh=13.032, minimum_target_kwh=14.48,
                                    full_target_kwh=18.1,
                                    deadline=datetime.fromtimestamp(stamps[41]+900, timezone.utc).isoformat())
        yield case


def case_from_plan(path):
    plan = json.loads(Path(path).read_text())
    rows = plan["schedule"]
    # Re-solve the two implementations from the SAME saved, rounded forecasts;
    # do not mistake this for reproducing the original unrounded live solve.
    return dict(name="archived_plan_inputs", timestamps=[r["timestamp"] for r in rows],
                prices=[r["price"] for r in rows], solar_kw=[r["solar_kw"] for r in rows],
                solar_p50_kw=[r["solar_p50_kw"] for r in rows], load_kw=[r["load_kw"] for r in rows],
                initial_energy=plan["initial_energy_kwh"], ev_input=plan.get("ev"),
                battery_preservation=[r.get("battery_preserved", False) for r in rows])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-source", required=True, type=Path)
    parser.add_argument("--plan", type=Path, help="Optional saved optimizer_15m.json; stays local")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    legacy, problems, sha = load_legacy(args.legacy_source)
    cases = list(synthetic_cases())
    if args.plan:
        cases.append(case_from_plan(args.plan))
    results = []
    for case in cases:
        try:
            result = compare_case(case, legacy, problems)
        except (OptimizationError, RuntimeError, ValueError) as exc:
            result = dict(name=case["name"], equivalent_objective=False, error=str(exc))
        results.append(result)
    config = HouseholdConfig(**{field: legacy.__globals__[constant]
                                for field, constant in CONFIG_CONSTANTS.items()})
    report = dict(legacy_sha256=sha, config=config.__dict__,
                  all_objectives_equivalent=all(r["equivalent_objective"] for r in results), cases=results)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["all_objectives_equivalent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
