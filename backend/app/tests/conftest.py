"""场景夹具：引擎直调用纯数据周面，HTTP 路径用隔离 SQLite + TestClient。

与断言助手（assertions.py）刻意拆成两个模块：本模块只负责“摆场景”，
断言口径全部收敛在 assertions.py，引擎/HTTP 两条路径共用。
"""

import pytest

from app import seed
from app.db import connect
from app.main import app


DAYS = 2


# ---------- 引擎直调场景（纯数据，不落库） ----------

@pytest.fixture
def engine_grid():
    return {"members": [1, 2, 3], "tasks": [10, 20], "days": DAYS}


@pytest.fixture
def engine_combined(engine_grid):
    """叠加：成员 2 忌日 day0；任务 20 禁配成员 3。"""
    return {
        **engine_grid,
        "blackouts": {2: {0}},
        "exclusions": {20: {3}},
        "blackout_member": 2, "blackout_day": 0,
        "excluded_task": 20, "excluded_member": 3,
    }


@pytest.fixture
def engine_blackout_only(engine_grid):
    """对照：仅忌日（成员 2 day0）。"""
    return {**engine_grid, "blackouts": {2: {0}}, "exclusions": {},
            "blackout_member": 2, "blackout_day": 0}


@pytest.fixture
def engine_exclusion_only(engine_grid):
    """对照：仅禁配（任务 20 × 成员 3）。"""
    return {**engine_grid, "blackouts": {}, "exclusions": {20: {3}},
            "excluded_task": 20, "excluded_member": 3}


@pytest.fixture
def engine_infeasible(engine_grid):
    """day0/task20 三个人全被挡：1、2 忌日，3 被禁配 → 无可派成员。"""
    return {
        **engine_grid,
        "blackouts": {1: {0}, 2: {0}},
        "exclusions": {20: {3}},
        "dead_cell": {"day": 0, "task_id": 20},
    }


@pytest.fixture
def engine_tighten():
    """4 人 2 任务先无约束生成成功，随后收紧：1 忌日 day1、任务10 禁配 4。
    旧格位 (d0,t10)=1 ↔ (d1,t20)=4 这对对调在收紧后双向违例。"""
    slots_seed = {"members": [1, 2, 3, 4], "tasks": [10, 20], "days": DAYS}
    return {
        **slots_seed,
        "tight_blackouts": {1: {1}},
        "tight_exclusions": {10: {4}},
        "bad_swap": ((0, 10), (1, 20)),
    }


# ---------- HTTP 场景（每用例独立临时库） ----------

class HttpWorld:
    """周面操作台：登记忌日/禁配、走 HTTP 生成/对调、查看板与登记表。"""

    WEEK_ID = 1

    def __init__(self, client):
        self.client = client
        self.days = DAYS
        members = [m for m in client.get("/api/members").json()
                   if m["active"] and m["data_quality"] == "clean"]
        tasks = [t for t in client.get("/api/tasks").json()
                 if t["data_quality"] == "clean" and t["weight"] > 0]
        self.mids = [m["id"] for m in members]
        self.tids = [t["id"] for t in tasks]

    # 登记入口（成员页忌日、任务/成员页共用禁配表）
    def add_blackout(self, member_id, day, note=""):
        r = self.client.post("/api/members/blackouts",
                             json={"member_id": member_id, "day": day, "note": note})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    def add_exclusion(self, task_id, member_id, note=""):
        r = self.client.post("/api/tasks/exclusions",
                             json={"task_id": task_id, "member_id": member_id, "note": note})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    def blackouts(self):
        return self.client.get("/api/members/blackouts").json()

    def exclusions_by_task(self):
        return self.client.get("/api/tasks/exclusions").json()

    def exclusions_by_member(self):
        return self.client.get("/api/members/exclusions").json()

    def generate(self, days=None):
        return self.client.post(f"/api/weeks/{self.WEEK_ID}/generate",
                                json={"days": self.days if days is None else days})

    def board(self):
        r = self.client.get(f"/api/weeks/{self.WEEK_ID}/board")
        assert r.status_code == 200, r.text
        body = r.json()
        return body["week"], body["assignments"]

    def request_swap(self, a, b):
        return self.client.post(f"/api/weeks/{self.WEEK_ID}/swaps", json={
            "a_day": a[0], "a_task": a[1], "b_day": b[0], "b_task": b[1]})

    def confirm(self, swap_id):
        return self.client.post(f"/api/swaps/{swap_id}/confirm")

    def insert_pending_swap(self, a, b):
        c = connect()
        cur = c.execute(
            "INSERT INTO swap_requests(week_id,a_day,a_task,b_day,b_task,status,note)"
            " VALUES (?,?,?,?,?,?,'')",
            (self.WEEK_ID, a[0], a[1], b[0], b[1], "pending"))
        c.commit(); sid = cur.lastrowid; c.close()
        return sid

    def insert_sentinel_assignment(self, day=0, task_id=None, member_id=None):
        tid = task_id or self.tids[0]
        mid = member_id or self.mids[0]
        c = connect()
        c.execute("INSERT INTO assignments(week_id,day,task_id,member_id) VALUES (?,?,?,?)",
                  (self.WEEK_ID, day, tid, mid))
        c.commit(); c.close()
        return (day, tid, mid)

    def assignment_count(self):
        c = connect()
        n = c.execute("SELECT COUNT(*) c FROM assignments WHERE week_id=?",
                      (self.WEEK_ID,)).fetchone()["c"]
        c.close()
        return n

    def week_status(self):
        _, _ = self.board()
        c = connect()
        s = c.execute("SELECT status FROM weeks WHERE id=?", (self.WEEK_ID,)).fetchone()["status"]
        c.close()
        return s


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fastapi.testclient import TestClient
    with TestClient(app) as cl:
        yield cl


@pytest.fixture
def world(client):
    return HttpWorld(client)
