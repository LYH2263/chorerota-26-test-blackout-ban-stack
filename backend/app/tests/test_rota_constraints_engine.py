"""引擎直调路径：忌日/禁配叠加、单约束对照、不可派失败、收紧后对调拦截。

断言全部走 tests.assertions（与 HTTP 路径共用同一助手）。
"""

import pytest

from app.engines.rota import build_week_slots, RotaInfeasible
from app.tests.assertions import (
    assert_constraints_hold,
    assert_engine_swap_blocked,
    print_violations,
)


def _build(scenario, **overrides):
    return build_week_slots(
        scenario["members"], scenario["tasks"], days=scenario["days"],
        blackouts=scenario.get("blackouts"), exclusions=scenario.get("exclusions"),
        **overrides,
    )


def test_engine_combined_blackout_and_exclusion(engine_combined):
    s = engine_combined
    slots = _build(s)
    tuples = assert_constraints_hold(
        slots, s["blackouts"], s["exclusions"], s["tasks"], s["days"])
    # 忌日当天无该成员
    assert not any(d == s["blackout_day"] and m == s["blackout_member"]
                   for d, _, m in tuples)
    # 禁配人对不出现在禁配任务格（任意天）
    assert not any(t == s["excluded_task"] and m == s["excluded_member"]
                   for _, t, m in tuples)


def test_engine_blackout_only_control(engine_blackout_only):
    s = engine_blackout_only
    slots = _build(s)
    tuples = assert_constraints_hold(
        slots, s["blackouts"], s["exclusions"], s["tasks"], s["days"])
    assert not any(d == s["blackout_day"] and m == s["blackout_member"]
                   for d, _, m in tuples)


def test_engine_exclusion_only_control(engine_exclusion_only):
    s = engine_exclusion_only
    slots = _build(s)
    tuples = assert_constraints_hold(
        slots, s["blackouts"], s["exclusions"], s["tasks"], s["days"])
    assert not any(t == s["excluded_task"] and m == s["excluded_member"]
                   for _, t, m in tuples)


def test_engine_infeasible_raises_with_violations(engine_infeasible):
    s = engine_infeasible
    with pytest.raises(RotaInfeasible) as ei:
        _build(s)
    violations = ei.value.violations
    print_violations(violations)
    dead = s["dead_cell"]
    assert violations, "不可派时必须回传违例格"
    assert all(v["day"] == dead["day"] and v["task_id"] == dead["task_id"]
               for v in violations)
    assert {v["rule"] for v in violations} == {"blackout", "exclusion"}


def test_engine_tighten_reopen_keeps_board_and_blocks_swap(engine_tighten):
    s = engine_tighten
    # 生成成功时尚无收紧约束
    slots = build_week_slots(s["members"], s["tasks"], days=s["days"])
    snapshot = [dict(x) for x in slots]

    # 收紧忌日、追加禁配。旧周看板的“再打开”是只读动作：不重新生成、不改格。
    reopened = [dict(x) for x in slots]
    (a_day, a_task), (b_day, b_task) = s["bad_swap"]
    assert [(x["day"], x["task_id"], x["member_id"]) for x in reopened] == \
           [(x["day"], x["task_id"], x["member_id"]) for x in snapshot]

    # 新对调若违忌日/禁配须失败，并回传/打印违例格
    assert_engine_swap_blocked(
        reopened, (a_day, a_task), (b_day, b_task),
        s["tight_blackouts"], s["tight_exclusions"])
    # 被拦截后原列表不被改动
    assert reopened == snapshot
