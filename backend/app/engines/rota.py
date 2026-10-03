"""Round-robin weekly chore assignments + swap legality.

约束模型（均为可选，不传则行为与旧版一致）：
- blackout:  {member_id: {day, ...}}   成员忌日，当天不可排该成员
- forbidden: {member_id: {task_id, ...}} 人任禁配，该成员不可派该任务
"""


class UnfillableSlotError(ValueError):
    """某 (day, task) 格在现行忌日/禁配约束下无任何可派成员。"""

    def __init__(self, day: int, task_id: int, member_ids, blackout, forbidden):
        self.day = day
        self.task_id = task_id
        blocked = {
            m: {
                "blackout": sorted(blackout.get(m, ())),
                "forbidden": sorted(forbidden.get(m, ())),
            }
            for m in member_ids
        }
        super().__init__(
            f"unfillable slot: day={day} task_id={task_id} — "
            f"所有成员均被忌日/禁配挡住: {blocked}"
        )


def _eligible(member_id, day, task_id, blackout, forbidden) -> bool:
    return day not in blackout.get(member_id, ()) and task_id not in forbidden.get(member_id, ())


def build_week_slots(member_ids: list[int], task_ids: list[int], days: int = 7,
                     *, blackout: dict | None = None, forbidden: dict | None = None) -> list[dict]:
    """Assign each (day, task) to members in round-robin by task then day.

    跳过被忌日/禁配挡住的成员；整圈无人可派时抛 UnfillableSlotError（不写任何格）。
    """
    if not member_ids or not task_ids:
        return []
    blackout = blackout or {}
    forbidden = forbidden or {}
    slots = []
    idx = 0
    n = len(member_ids)
    for day in range(days):
        for tid in task_ids:
            chosen = None
            for step in range(n):
                mid = member_ids[(idx + step) % n]
                if _eligible(mid, day, tid, blackout, forbidden):
                    chosen = mid
                    idx = (idx + step + 1) % n
                    break
            if chosen is None:
                raise UnfillableSlotError(day, tid, member_ids, blackout, forbidden)
            slots.append({"day": day, "task_id": tid, "member_id": chosen})
    return slots


def _swap_violations(a_member, b_member, a_day, a_task, b_day, b_task, blackout, forbidden) -> list[dict]:
    """对调后两名成员各自落入的新格若违忌日/禁配，逐条列出违例格。"""
    out = []
    # a_member 将落入 (b_day, b_task)，b_member 将落入 (a_day, a_task)
    if b_day in blackout.get(a_member, ()):
        out.append({"kind": "blackout", "member_id": a_member, "day": b_day, "task_id": b_task})
    if a_day in blackout.get(b_member, ()):
        out.append({"kind": "blackout", "member_id": b_member, "day": a_day, "task_id": a_task})
    if b_task in forbidden.get(a_member, ()):
        out.append({"kind": "forbidden", "member_id": a_member, "day": b_day, "task_id": b_task})
    if a_task in forbidden.get(b_member, ()):
        out.append({"kind": "forbidden", "member_id": b_member, "day": a_day, "task_id": a_task})
    return out


def swap_legal(slots: list[dict], a_day: int, a_task: int, b_day: int, b_task: int,
               *, blackout: dict | None = None, forbidden: dict | None = None) -> dict:
    """Two slots may swap only if both exist, different assignees, same week grid,
    and the post-swaps placements respect 忌日/禁配."""
    def find(day, task):
        for s in slots:
            if s["day"] == day and s["task_id"] == task:
                return s
        return None
    sa, sb = find(a_day, a_task), find(b_day, b_task)
    if sa is None or sb is None:
        return {"ok": False, "reason": "slot_missing"}
    if sa["member_id"] == sb["member_id"]:
        return {"ok": False, "reason": "same_assignee"}
    if a_day == b_day and a_task == b_task:
        return {"ok": False, "reason": "same_slot"}
    violations = _swap_violations(sa["member_id"], sb["member_id"],
                                  a_day, a_task, b_day, b_task,
                                  blackout or {}, forbidden or {})
    if violations:
        return {"ok": False, "reason": violations[0]["kind"] + "_violation", "violations": violations}
    return {
        "ok": True,
        "reason": "",
        "a_member": sa["member_id"],
        "b_member": sb["member_id"],
    }


def apply_swap(slots: list[dict], a_day: int, a_task: int, b_day: int, b_task: int,
               *, blackout: dict | None = None, forbidden: dict | None = None) -> list[dict]:
    check = swap_legal(slots, a_day, a_task, b_day, b_task, blackout=blackout, forbidden=forbidden)
    if not check["ok"]:
        raise ValueError(check["reason"])
    out = [dict(s) for s in slots]
    ia = next(i for i, s in enumerate(out) if s["day"] == a_day and s["task_id"] == a_task)
    ib = next(i for i, s in enumerate(out) if s["day"] == b_day and s["task_id"] == b_task)
    out[ia]["member_id"], out[ib]["member_id"] = out[ib]["member_id"], out[ia]["member_id"]
    return out
