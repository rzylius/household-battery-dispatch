import ast

import pytest

from integrations.openhab.compare import load_legacy, validate_problem
from integrations.openhab.candidate import CONFIG_CONSTANTS, build_candidate
from optim import OptimizationError
import pulp


def source():
    constants = "\n".join(f"{name} = 1" for name in sorted(set(CONFIG_CONSTANTS.values())))
    return constants + '''
def forbidden_side_effect():
    raise AssertionError("Rule startup must not execute")
class ImeonQuarterHourOptimizer:
    def optimize(self, timestamps, prices, solar_kw, solar_p50_kw, load_kw,
                 initial_energy, ev_input=None, battery_preservation=None):
        return BATTERY_CAPACITY_KWH
    def dispatch(self):
        forbidden_side_effect()
forbidden_side_effect()
'''


def test_candidate_changes_only_optimize_method():
    before = ast.parse(source())
    after = ast.parse(build_candidate(source()))
    for tree in (before, after):
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        cls.body = [n for n in cls.body if n.name != "optimize"]
    assert ast.dump(before) == ast.dump(after)


def test_replay_loader_never_runs_rule_startup(tmp_path):
    path = tmp_path / "rule.py"
    path.write_text(source())
    method, problems, sha = load_legacy(path)
    assert method(None, [], [], [], [], [], 0) == 1
    assert not problems
    assert len(sha) == 64


def test_replay_refuses_feasible_but_unproven_result():
    problem = pulp.LpProblem()
    problem.status = pulp.LpStatusOptimal
    problem.sol_status = pulp.LpSolutionIntegerFeasible
    with pytest.raises(OptimizationError):
        validate_problem(problem)


def test_builder_rejects_changed_signature():
    with pytest.raises(ValueError):
        build_candidate(source().replace("battery_preservation=None", "new_argument=None"))
