"""忌日×禁配叠加拒派测例。

- 引擎直调与 HTTP 生成路径各覆盖至少一组成功场景，共用同一断言助手（拍板：共用，
  因两侧格位形状一致，见 rota_constraint_asserts 模块 docstring）。
- 另含仅忌日、仅禁配对照；不可派不写格；收紧后「再打开」不改写旧周、违例对调必败。
"""
from dataclasses import replace

import pytest

from app.db import connect
from app.engines.rota import (
    UnfillableSlotError,
    apply_swap,
    build_week_slots,
    swap_legal,
)
from app.tests.rota_constraint_asserts import (
    assert_board_respects_constraints,
    assert_board_unchanged,
    assert_registrations_consistent,
)
from app.tests.rota_constraint_scenarios import (
    client,  # noqa: F401  —— pytest fixture，注册用
    get_board,
    read_back_constraints,
    scenario_blackout_only,
    scenario_forbidden_only,
    scenario_stacked,
    scenario_unfillable,
    seed_via_api,
)


def _tightened(mapping, key, values):
    out = {m: set(v) for m, v in mapping.items()}
    out.setdefault(key, set()).update(values)
    return out


# ---------- 引擎直调 ----------

def test_engine_stacked_blackout_and_forbidden_success():
    """叠加成功（引擎直调）：忌日当天无该成员、禁配人对不上禁配任务、总格数=任务数×天数。"""
    sc = scenario_stacked()
    slots = build_week_slots(sc.members, sc.tasks, days=sc.days,
                             blackout=sc.blackout, forbidden=sc.forbidden)
    assert_board_respects_constraints(slots, sc)


def test_engine_blackout_only_control():
    """对照：仅忌日。"""
    sc = scenario_blackout_only()
    slots = build_week_slots(sc.members, sc.tasks, days=sc.days, blackout=sc.blackout)
    assert_board_respects_constraints(slots, sc)


def test_engine_forbidden_only_control():
    """对照：仅禁配。"""
    sc = scenario_forbidden_only()
    slots = build_week_slots(sc.members, sc.tasks, days=sc.days, forbidden=sc.forbidden)
    assert_board_respects_constraints(slots, sc)


def test_engine_unfillable_raises_and_names_cell():
    """可派成员不足 → 抛 UnfillableSlotError，错误信息打印违例格。"""
    sc = scenario_unfillable()
    with pytest.raises(UnfillableSlotError) as exc_info:
        build_week_slots(sc.members, sc.tasks, days=sc.days,
                         blackout=sc.blackout, forbidden=sc.forbidden)
    msg = str(exc_info.value)
    assert "day=2" in msg and "task_id=10" in msg, f"未打印违例格: {msg}"


def test_engine_swap_after_tightening_fails():
    """生成后收紧忌日/禁配：新对调违例必败；未收紧的合法对调仍通（对照）。"""
    sc = scenario_stacked()
    slots = build_week_slots(sc.members, sc.tasks, days=sc.days,
                             blackout=sc.blackout, forbidden=sc.forbidden)
    at = {(s["day"], s["task_id"]): s for s in slots}
    a, b = at[(1, 20)], at[(1, 10)]  # m1@(1,20)，m3@(1,10)，原约束下互换代入均合法
    assert a["member_id"] != b["member_id"]

    ok = swap_legal(slots, 1, 20, 1, 10, blackout=sc.blackout, forbidden=sc.forbidden)
    assert ok["ok"], f"对照对调本应合法: {ok}"

    # 收紧禁配：a 格成员禁配 b 格任务 → 违例对调失败并列出违例格
    tight_f = _tightened(sc.forbidden, a["member_id"], {b["task_id"]})
    r = swap_legal(slots, 1, 20, 1, 10, blackout=sc.blackout, forbidden=tight_f)
    assert not r["ok"] and r["reason"] == "forbidden_violation" and r["violations"]
    with pytest.raises(ValueError):
        apply_swap(slots, 1, 20, 1, 10, blackout=sc.blackout, forbidden=tight_f)

    # 收紧忌日：b 格成员忌日铺满 a 格当天 → 违例对调失败
    tight_b = _tightened(sc.blackout, b["member_id"], {a["day"]})
    r = swap_legal(slots, 1, 20, 1, 10, blackout=tight_b, forbidden=sc.forbidden)
    assert not r["ok"] and r["reason"] == "blackout_violation" and r["violations"]
    with pytest.raises(ValueError):
        apply_swap(slots, 1, 20, 1, 10, blackout=tight_b, forbidden=sc.forbidden)


# ---------- HTTP 生成路径 ----------

def test_http_generate_stacked_success_pinned_to_registration(client):
    """叠加成功（HTTP）：看板格位与成员页忌日、任务/成员页禁配列表登记同钉。"""
    sc, week_id = seed_via_api(client, scenario_stacked())
    r = client.post(f"/api/weeks/{week_id}/generate", json={"days": sc.days})
    assert r.status_code == 200, r.text
    assert r.json()["count"] == sc.expected_cell_count

    board = get_board(client, week_id)
    # 同钉：断言基准完全来自登记接口读回，而非夹具自身
    blackout_rb, forb_by_member, forb_by_task = read_back_constraints(client, sc)
    assert_registrations_consistent(forb_by_member, forb_by_task)
    pinned = replace(sc, blackout=blackout_rb, forbidden=forb_by_member)
    assert_board_respects_constraints(board, pinned)


def test_http_unfillable_generate_fails_and_keeps_assignments(client):
    """可派成员不足 → 生成 409 且不写格：新周 0 行不变，旧周 14 行原样保留。"""
    sc, week1 = seed_via_api(client, scenario_stacked())
    assert client.post(f"/api/weeks/{week1}/generate", json={"days": sc.days}).status_code == 200
    before = get_board(client, week1)
    rows_before = connect().execute("SELECT COUNT(*) c FROM assignments").fetchone()["c"]
    assert rows_before == sc.expected_cell_count

    # 收紧到不可派：全体成员 day0 忌日（成员1 原已登记，INSERT OR IGNORE 幂等）
    for m in sc.members:
        client.post(f"/api/members/{m}/blackouts", json={"day": 0})

    week2 = client.post("/api/weeks", json={"label": "周-unfillable"}).json()["id"]
    r2 = client.post(f"/api/weeks/{week2}/generate", json={"days": sc.days})
    assert r2.status_code == 409 and "unfillable" in r2.json()["detail"], r2.text
    assert get_board(client, week2) == [], "失败生成不得写格（新周应仍 0 行）"

    r1 = client.post(f"/api/weeks/{week1}/generate", json={"days": sc.days})
    assert r1.status_code == 409, r1.text
    assert_board_unchanged(before, get_board(client, week1), what="旧周看板（失败重生成）")
    rows_after = connect().execute("SELECT COUNT(*) c FROM assignments").fetchone()["c"]
    assert rows_after == rows_before, (
        f"assignments 行数相对生成前不变: 前={rows_before} 后={rows_after}")


def test_http_tighten_after_generate_reopen_keeps_board_and_blocks_swap(client):
    """生成后收紧忌日：旧周看板「再打开」不改写；违例新对调 400，合法对调仍 200。"""
    sc, week_id = seed_via_api(client, scenario_stacked())
    assert client.post(f"/api/weeks/{week_id}/generate", json={"days": sc.days}).status_code == 200
    before = get_board(client, week_id)
    t10, t20 = sc.tasks

    # 收紧：给现居 (day1, t10) 的成员追加 day1 忌日 —— 旧看板随即违反新约束
    cell_b = next(s for s in before if s["day"] == 1 and s["task_id"] == t10)
    cell_a = next(s for s in before if s["day"] == 1 and s["task_id"] == t20)
    assert cell_a["member_id"] != cell_b["member_id"]
    client.post(f"/api/members/{cell_b['member_id']}/blackouts", json={"day": 1})

    # 「再打开」不得改写旧周看板
    assert_board_unchanged(before, get_board(client, week_id), what="旧周看板（再打开）")

    # 新对调若把被收紧成员换入其忌日格 → 400 blackout_violation
    bad = client.post(f"/api/weeks/{week_id}/swaps",
                      json={"a_day": 1, "a_task": t20, "b_day": 1, "b_task": t10})
    assert bad.status_code == 400 and bad.json()["detail"] == "blackout_violation", bad.text

    # 对照：不涉新忌日的对调仍合法（day2 两格互换代入均不违例）
    ok = client.post(f"/api/weeks/{week_id}/swaps",
                     json={"a_day": 2, "a_task": t20, "b_day": 2, "b_task": t10})
    assert ok.status_code == 200, ok.text
