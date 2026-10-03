"""忌日/禁配看板断言助手 —— 引擎直调与 HTTP 生成路径共用（拍板：同一份）。

两侧格位形状一致：[{"day", "task_id", "member_id"}]（HTTP 看板行多出的 id/week_id 等键不影响）。
所有助手失败时打印违例格明细。
"""
from app.tests.rota_constraint_scenarios import ConstraintScenario


def find_violations(slots: list[dict], sc: ConstraintScenario) -> list[str]:
    """逐格检查忌日/禁配/成员越界，返回人类可读的违例格描述列表。"""
    out = []
    for s in slots:
        m, d, t = s["member_id"], s["day"], s["task_id"]
        if m not in sc.members:
            out.append(f"越界成员: 成员{m} 不在场景成员表 {sc.members}，格={s}")
        if d in sc.blackout.get(m, ()):
            out.append(f"忌日违例: 成员{m} 在忌日 day{d} 被派任务{t}，格={s}")
        if t in sc.forbidden.get(m, ()):
            out.append(f"禁配违例: 成员{m} 被派禁配任务{t}（day{d}），格={s}")
    return out


def assert_board_respects_constraints(slots: list[dict], sc: ConstraintScenario) -> None:
    """核心断言：总格数 == 任务数×天数（单人口径）、格位不重复、无违例格。"""
    expected = sc.expected_cell_count
    assert len(slots) == expected, (
        f"[{sc.name}] 总格数应为 任务数×天数 = {len(sc.tasks)}×{sc.days} = {expected}，"
        f"实得 {len(slots)}，格位={slots}"
    )
    keys = [(s["day"], s["task_id"]) for s in slots]
    dups = sorted({k for k in keys if keys.count(k) > 1})
    assert not dups, f"[{sc.name}] 同一 (day,task) 被重复派格: {dups}，格位={slots}"
    violations = find_violations(slots, sc)
    assert not violations, (
        f"[{sc.name}] 看板违例格 {len(violations)} 处:\n" + "\n".join(violations)
    )


def normalized_cells(assignments: list[dict]) -> list[tuple]:
    """看板格位归一化（忽略行 id，只留 (day, task_id, member_id)），用于前后快照对比。"""
    return sorted((a["day"], a["task_id"], a["member_id"]) for a in assignments)


def assert_board_unchanged(before: list[dict], after: list[dict], what: str = "看板") -> None:
    """断言两次取回的看板格位完全一致（「再打开」不得改写旧周）。"""
    b, a = normalized_cells(before), normalized_cells(after)
    assert b == a, (
        f"{what}被改写！\n改动前 {len(b)} 格: {b}\n改动后 {len(a)} 格: {a}"
    )


def assert_registrations_consistent(by_member: dict, by_task: dict) -> None:
    """禁配的成员页视图与任务页视图必须同钉（同一批 (member, task) 登记）。"""
    pm = {(m, t) for m, ts in by_member.items() for t in ts}
    pt = {(m, t) for m, ts in by_task.items() for t in ts}
    assert pm == pt, f"禁配登记两视图不一致: 成员页={sorted(pm)} 任务页={sorted(pt)}"
