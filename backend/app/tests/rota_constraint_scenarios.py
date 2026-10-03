"""忌日/禁配场景夹具：纯数据场景 + HTTP 侧播种与登记读回。

本模块只负责「造场景、登记、读回」，不做任何断言；
断言助手见 app.tests.rota_constraint_asserts（两侧共用，拍板：同一份）。
"""
from dataclasses import dataclass, field, replace

import pytest


@dataclass
class ConstraintScenario:
    """一周排班约束场景。blackout=忌日 {member_id: {day}}，forbidden=禁配 {member_id: {task_id}}。"""
    name: str
    members: list[int]
    tasks: list[int]
    days: int
    blackout: dict[int, set[int]] = field(default_factory=dict)
    forbidden: dict[int, set[int]] = field(default_factory=dict)

    @property
    def expected_cell_count(self) -> int:
        """现行单人口径：每 (day, task) 一格一人 → 任务数×天数。"""
        return len(self.tasks) * self.days


# ---------- 纯数据场景（引擎直调与 HTTP 播种共用） ----------

def scenario_stacked() -> ConstraintScenario:
    """叠加场景：忌日与禁配同时存在，且都能咬住裸轮转（成员1 原会落在 day0/day3，成员2 原会接到任务10）。"""
    return ConstraintScenario(
        name="stacked",
        members=[1, 2, 3], tasks=[10, 20], days=7,
        blackout={1: {0, 3}},   # 成员1 忌日：day0、day3
        forbidden={2: {10}},    # 成员2 禁配任务10
    )


def scenario_blackout_only() -> ConstraintScenario:
    """对照：仅忌日。"""
    return ConstraintScenario(
        name="blackout_only",
        members=[1, 2, 3], tasks=[10, 20], days=7,
        blackout={2: {1, 2, 4}},
    )


def scenario_forbidden_only() -> ConstraintScenario:
    """对照：仅禁配。"""
    return ConstraintScenario(
        name="forbidden_only",
        members=[1, 2, 3], tasks=[10, 20], days=7,
        forbidden={3: {10}, 1: {20}},
    )


def scenario_unfillable() -> ConstraintScenario:
    """不可派：唯一成员在 day2 忌日 → (day2, 任务10) 无人可派。"""
    return ConstraintScenario(
        name="unfillable",
        members=[1], tasks=[10], days=3,
        blackout={1: {2}},
    )


# ---------- HTTP 侧夹具 ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    """独立 DATA_DIR 的 TestClient；清掉演示种子，整周数据全由场景登记。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fastapi.testclient import TestClient

    from app.db import connect
    from app.main import app

    with TestClient(app) as cl:  # startup 会 init_db + 演示种子
        c = connect()
        for t in ("assignments", "swap_requests", "member_blackouts",
                  "forbidden_pairs", "weeks", "members", "tasks"):
            c.execute(f"DELETE FROM {t}")
        c.commit(); c.close()
        yield cl


def seed_via_api(client, sc: ConstraintScenario) -> tuple[ConstraintScenario, int]:
    """把场景经 HTTP 登记进去（成员/任务/周/忌日/禁配），返回真实 id 版场景与 week_id。"""
    mid = {m: client.post("/api/members", json={"name": f"成员{m}"}).json()["id"] for m in sc.members}
    tid = {t: client.post("/api/tasks", json={"title": f"任务{t}"}).json()["id"] for t in sc.tasks}
    week_id = client.post("/api/weeks", json={"label": f"周-{sc.name}"}).json()["id"]
    for m, days in sc.blackout.items():
        for d in days:
            r = client.post(f"/api/members/{mid[m]}/blackouts", json={"day": d})
            assert r.status_code == 200, r.text
    for m, tids in sc.forbidden.items():
        for t in tids:
            r = client.post("/api/forbidden-pairs", json={"member_id": mid[m], "task_id": tid[t]})
            assert r.status_code == 200, r.text
    real = replace(
        sc,
        members=[mid[m] for m in sc.members],
        tasks=[tid[t] for t in sc.tasks],
        blackout={mid[m]: set(ds) for m, ds in sc.blackout.items()},
        forbidden={mid[m]: {tid[t] for t in ts} for m, ts in sc.forbidden.items()},
    )
    return real, week_id


def read_back_constraints(client, sc: ConstraintScenario):
    """从「成员页忌日」与「任务/成员页禁配列表」接口读回登记，作为看板断言的同钉基准。

    返回 (blackout, forbidden_by_member, forbidden_by_task)：禁配给两个视图，
    一致性由断言助手校验。
    """
    blackout = {}
    for m in sc.members:
        blackout[m] = set(client.get(f"/api/members/{m}/blackouts").json()["days"])
    by_member, by_task = {}, {}
    for m in sc.members:
        for row in client.get("/api/forbidden-pairs", params={"member_id": m}).json():
            by_member.setdefault(row["member_id"], set()).add(row["task_id"])
    for t in sc.tasks:
        for row in client.get("/api/forbidden-pairs", params={"task_id": t}).json():
            by_task.setdefault(row["member_id"], set()).add(row["task_id"])
    return blackout, by_member, by_task


def get_board(client, week_id: int) -> list[dict]:
    return client.get(f"/api/weeks/{week_id}/board").json()["assignments"]
