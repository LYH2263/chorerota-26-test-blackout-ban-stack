"""Round-robin weekly chore assignments + swap legality.

约束（均为可选，缺省时行为与纯 round-robin 完全一致）：
- blackouts（忌日）: {member_id: set(day)}，该成员当天不得出现在任何任务格。
- exclusions（禁配）: {task_id: set(member_id)}，该成员不得承担该任务（任意天）。
"""

from __future__ import annotations


class RotaInfeasible(Exception):
    """无任何可派成员可填入某格时抛出，violations 给出该格所有候选被挡原因。"""

    def __init__(self, message: str, violations: list[dict]):
        super().__init__(message)
        self.violations = violations


def _is_blocked(member_id: int, day: int, task_id: int,
                blackouts: dict, exclusions: dict) -> list[str]:
    """返回该候选填入 (day, task) 会触发的规则名列表（空=可派）。"""
    rules = []
    if day in blackouts.get(member_id, ()):
        rules.append("blackout")
    if member_id in exclusions.get(task_id, ()):
        rules.append("exclusion")
    return rules


def build_week_slots(member_ids: list[int], task_ids: list[int], days: int = 7,
                     blackouts: dict | None = None,
                     exclusions: dict | None = None) -> list[dict]:
    """Assign each (day, task) to members in round-robin by task then day.

    指针沿 round-robin 前进；候选命中忌日/禁配则顺延到下一位可派成员。
    某格全员不可派时抛 RotaInfeasible（纯函数，不产生半成品槽位）。
    """
    member_ids = list(dict.fromkeys(member_ids))
    task_ids = list(dict.fromkeys(task_ids))
    if not member_ids or not task_ids:
        return []
    blackouts = blackouts or {}
    exclusions = exclusions or {}
    n = len(member_ids)
    slots: list[dict] = []
    pos = 0
    for day in range(days):
        for tid in task_ids:
            chosen = None
            for step in range(n):
                cand = member_ids[(pos + step) % n]
                if not _is_blocked(cand, day, tid, blackouts, exclusions):
                    chosen = cand
                    pos = (pos + step + 1) % n
                    break
            if chosen is None:
                violations = []
                for mid in member_ids:
                    for rule in _is_blocked(mid, day, tid, blackouts, exclusions):
                        violations.append(
                            {"day": day, "task_id": tid, "member_id": mid, "rule": rule})
                raise RotaInfeasible(
                    f"no eligible member for day={day} task_id={tid}", violations)
            slots.append({"day": day, "task_id": tid, "member_id": chosen})
    return slots


def _find_slot(slots: list[dict], day: int, task: int):
    for s in slots:
        if s["day"] == day and s["task_id"] == task:
            return s
    return None


def _post_swap_violations(sa: dict, sb: dict, blackouts: dict, exclusions: dict) -> list[dict]:
    """对调落定后两格的违例：sb 的人进 A 格，sa 的人进 B 格。"""
    violations = []
    a_incoming, b_incoming = sb["member_id"], sa["member_id"]
    for rule in _is_blocked(a_incoming, sa["day"], sa["task_id"], blackouts, exclusions):
        violations.append({"day": sa["day"], "task_id": sa["task_id"],
                           "member_id": a_incoming, "rule": rule})
    for rule in _is_blocked(b_incoming, sb["day"], sb["task_id"], blackouts, exclusions):
        violations.append({"day": sb["day"], "task_id": sb["task_id"],
                           "member_id": b_incoming, "rule": rule})
    return violations


def swap_legal(slots: list[dict], a_day: int, a_task: int, b_day: int, b_task: int,
               blackouts: dict | None = None,
               exclusions: dict | None = None) -> dict:
    """Two slots may swap only if both exist, different assignees, same week grid,
    且对调后两格均不违忌日/禁配。"""
    sa, sb = _find_slot(slots, a_day, a_task), _find_slot(slots, b_day, b_task)
    if sa is None or sb is None:
        return {"ok": False, "reason": "slot_missing"}
    if a_day == b_day and a_task == b_task:
        return {"ok": False, "reason": "same_slot"}
    if sa["member_id"] == sb["member_id"]:
        return {"ok": False, "reason": "same_assignee"}
    violations = _post_swap_violations(sa, sb, blackouts or {}, exclusions or {})
    if violations:
        return {"ok": False, "reason": "constraint_violation", "violations": violations}
    return {
        "ok": True,
        "reason": "",
        "a_member": sa["member_id"],
        "b_member": sb["member_id"],
    }


def apply_swap(slots: list[dict], a_day: int, a_task: int, b_day: int, b_task: int,
               blackouts: dict | None = None,
               exclusions: dict | None = None) -> list[dict]:
    check = swap_legal(slots, a_day, a_task, b_day, b_task, blackouts, exclusions)
    if not check["ok"]:
        err = ValueError(check["reason"])
        err.violations = check.get("violations", [])
        raise err
    out = [dict(s) for s in slots]
    ia = next(i for i, s in enumerate(out) if s["day"] == a_day and s["task_id"] == a_task)
    ib = next(i for i, s in enumerate(out) if s["day"] == b_day and s["task_id"] == b_task)
    out[ia]["member_id"], out[ib]["member_id"] = out[ib]["member_id"], out[ia]["member_id"]
    return out
