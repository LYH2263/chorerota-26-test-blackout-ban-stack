"""断言助手：引擎直调与 HTTP 生成两条路径共用同一套口径。

- 槽位统一归一化为 [(day, task_id, member_id), ...]
- 忌日/禁配登记行可重建约束集，用于「看板格位 ↔ 成员页/任务页登记」对账
- 凡检出违例，先 print 违例格再 assert（满足“失败打印违例格”）
"""

import json

import pytest

from app.engines.rota import swap_legal, apply_swap


def slot_tuples(slots) -> list[tuple[int, int, int]]:
    return [(int(s["day"]), int(s["task_id"]), int(s["member_id"])) for s in slots]


def registries_to_constraints(blackout_rows, exclusion_rows):
    """从成员页忌日列表、任务/成员页禁配列表的登记行重建约束。"""
    blackouts: dict[int, set] = {}
    for r in blackout_rows:
        blackouts.setdefault(int(r["member_id"]), set()).add(int(r["day"]))
    exclusions: dict[int, set] = {}
    for r in exclusion_rows:
        exclusions.setdefault(int(r["task_id"]), set()).add(int(r["member_id"]))
    return blackouts, exclusions


def find_violations(slots, blackouts: dict, exclusions: dict) -> list[dict]:
    bad = []
    for day, tid, mid in slot_tuples(slots):
        if day in blackouts.get(mid, ()):
            bad.append({"day": day, "task_id": tid, "member_id": mid, "rule": "blackout"})
        if mid in exclusions.get(tid, ()):
            bad.append({"day": day, "task_id": tid, "member_id": mid, "rule": "exclusion"})
    return bad


def print_violations(bad) -> None:
    if bad:
        print("违例格: " + json.dumps(bad, ensure_ascii=False))


def assert_grid_complete(slots, task_ids, days):
    """现行单人口径：每个 (day, task) 一格，总格数 = 任务数 × 天数，无重无漏。"""
    tuples = slot_tuples(slots)
    assert len(tuples) == len(task_ids) * days, (
        f"总格数 {len(tuples)} != 任务数 {len(task_ids)} × 天数 {days}")
    cells = [(d, t) for d, t, _ in tuples]
    expect = [(d, t) for d in range(days) for t in task_ids]
    assert sorted(cells) == sorted(expect), "格位集合与 (天数 × 任务) 笛卡尔积不一致"
    assert len(cells) == len(set(cells)), "存在重复格"
    return tuples


def assert_constraints_hold(slots, blackouts, exclusions, task_ids, days):
    """成功生成的统一断言：格数口径 + 忌日当天无该成员 + 禁配人对不落格。"""
    tuples = assert_grid_complete(slots, task_ids, days)
    bad = find_violations(slots, blackouts or {}, exclusions or {})
    print_violations(bad)
    assert not bad, f"看板存在违例格: {bad}"
    return tuples


def assert_registry_rows_equal(rows_a, rows_b, keys=("task_id", "member_id")):
    """任务页与成员页两个禁配列表必须是同一张登记表（同钉）。"""
    ka = sorted(tuple(int(r[k]) for k in keys) for r in rows_a)
    kb = sorted(tuple(int(r[k]) for k in keys) for r in rows_b)
    assert ka == kb
    return ka


def assert_board_aligned_with_registries(board_slots, blackout_rows, exclusion_rows,
                                         task_ids, days):
    """本周看板格位须与成员页忌日、任务/成员页禁配登记同钉。"""
    blackouts, exclusions = registries_to_constraints(blackout_rows, exclusion_rows)
    return assert_constraints_hold(board_slots, blackouts, exclusions, task_ids, days)


def assert_slots_unchanged(before, after):
    b, a = slot_tuples(before), slot_tuples(after)
    assert a == b, f"旧周看板被改写:\nbefore={b}\nafter ={a}"


def assert_engine_swap_blocked(slots, a: tuple, b: tuple, blackouts, exclusions):
    """收紧约束后，旧格位上的违例对调：swap_legal 否决、apply_swap 抛错且带违例格。"""
    check = swap_legal(slots, a[0], a[1], b[0], b[1], blackouts, exclusions)
    assert check["ok"] is False
    violations = check.get("violations", [])
    print_violations(violations)
    assert violations, "违例对调未回传违例格"
    with pytest.raises(ValueError) as ei:
        apply_swap(slots, a[0], a[1], b[0], b[1], blackouts, exclusions)
    assert getattr(ei.value, "violations", None)
    return violations


def assert_http_rejected(resp, expected_status=(400, 409)):
    """HTTP 失败路径：状态码 + detail.violations 非空，并打印违例格。"""
    assert resp.status_code in expected_status, resp.text
    detail = resp.json()["detail"]
    body = detail if isinstance(detail, dict) else {"reason": detail, "violations": []}
    violations = body.get("violations", [])
    print_violations(violations)
    assert violations, f"响应未带违例格: {body}"
    return body
