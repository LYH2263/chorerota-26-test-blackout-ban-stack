import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect
from app.engines.rota import build_week_slots, swap_legal, apply_swap, RotaInfeasible


def load_constraints(c) -> tuple[dict, dict]:
    """从登记表读出 blackouts={member_id:{day}} 与 exclusions={task_id:{member_id}}。"""
    blackouts: dict[int, set] = {}
    for r in c.execute("SELECT member_id, day FROM member_blackouts"):
        blackouts.setdefault(r["member_id"], set()).add(r["day"])
    exclusions: dict[int, set] = {}
    for r in c.execute("SELECT task_id, member_id FROM task_exclusions"):
        exclusions.setdefault(r["task_id"], set()).add(r["member_id"])
    return blackouts, exclusions

app = FastAPI(title="Chorerota", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

@app.get("/api/health")
def health(): return {"ok": True, "project": "chorerota"}

@app.get("/api/members")
def list_members():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM members")]; c.close(); return rows

@app.post("/api/members")
def add_member(body: dict):
    c = connect()
    cur = c.execute("INSERT INTO members(name,active,data_quality) VALUES (?,?,?)",
                    (body.get("name","未命名"), int(body.get("active",1)), body.get("data_quality","clean")))
    c.commit(); mid = cur.lastrowid; c.close(); return {"id": mid}

@app.get("/api/tasks")
def list_tasks():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM tasks")]; c.close(); return rows

@app.post("/api/tasks")
def add_task(body: dict):
    c = connect()
    cur = c.execute("INSERT INTO tasks(title,weight,data_quality) VALUES (?,?,?)",
                    (body.get("title","任务"), int(body.get("weight",1)), body.get("data_quality","clean")))
    c.commit(); tid = cur.lastrowid; c.close(); return {"id": tid}

@app.get("/api/weeks")
def list_weeks():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM weeks")]; c.close(); return rows

@app.get("/api/members/blackouts")
def list_blackouts():
    c = connect()
    rows = [dict(r) for r in c.execute(
        "SELECT b.*, m.name AS member_name FROM member_blackouts b "
        "LEFT JOIN members m ON m.id=b.member_id ORDER BY b.id")]
    c.close(); return rows

class BlackoutBody(BaseModel):
    member_id: int
    day: int
    note: str = ""

@app.post("/api/members/blackouts")
def add_blackout(body: BlackoutBody):
    c = connect()
    cur = c.execute("INSERT INTO member_blackouts(member_id,day,note) VALUES (?,?,?)",
                    (body.member_id, body.day, body.note))
    c.commit(); bid = cur.lastrowid; c.close(); return {"id": bid}

@app.get("/api/tasks/exclusions")
def list_exclusions():
    c = connect()
    rows = [dict(r) for r in c.execute(
        "SELECT e.*, m.name AS member_name, t.title AS task_title FROM task_exclusions e "
        "LEFT JOIN members m ON m.id=e.member_id LEFT JOIN tasks t ON t.id=e.task_id ORDER BY e.id")]
    c.close(); return rows

class ExclusionBody(BaseModel):
    task_id: int
    member_id: int
    note: str = ""

@app.post("/api/tasks/exclusions")
def add_exclusion(body: ExclusionBody):
    c = connect()
    cur = c.execute("INSERT INTO task_exclusions(task_id,member_id,note) VALUES (?,?,?)",
                    (body.task_id, body.member_id, body.note))
    c.commit(); eid = cur.lastrowid; c.close(); return {"id": eid}

@app.get("/api/members/exclusions")
def list_exclusions_by_member():
    """成员页视角的禁配列表，与 /api/tasks/exclusions 同一张登记表。"""
    c = connect()
    rows = [dict(r) for r in c.execute(
        "SELECT e.*, m.name AS member_name, t.title AS task_title FROM task_exclusions e "
        "LEFT JOIN members m ON m.id=e.member_id LEFT JOIN tasks t ON t.id=e.task_id ORDER BY e.id")]
    c.close(); return rows

@app.get("/api/weeks/{week_id}/board")
def week_board(week_id: int):
    c = connect()
    week = c.execute("SELECT * FROM weeks WHERE id=?", (week_id,)).fetchone()
    if not week: c.close(); raise HTTPException(404, "week not found")
    assigns = [dict(r) for r in c.execute("SELECT * FROM assignments WHERE week_id=?", (week_id,))]
    members = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM members")}
    tasks = {r["id"]: r["title"] for r in c.execute("SELECT id,title FROM tasks")}
    c.close()
    for a in assigns:
        a["member_name"] = members.get(a["member_id"], "?")
        a["task_title"] = tasks.get(a["task_id"], "?")
    return {"week": dict(week), "assignments": assigns}

class GenBody(BaseModel):
    days: int = 7

@app.post("/api/weeks/{week_id}/generate")
def generate(week_id: int, body: GenBody = GenBody()):
    c = connect()
    week = c.execute("SELECT * FROM weeks WHERE id=?", (week_id,)).fetchone()
    if not week: c.close(); raise HTTPException(404, "week not found")
    mids = [r["id"] for r in c.execute("SELECT id FROM members WHERE active=1 AND data_quality='clean' ORDER BY id")]
    tids = [r["id"] for r in c.execute("SELECT id FROM tasks WHERE data_quality='clean' AND weight>0 ORDER BY id")]
    blackouts, exclusions = load_constraints(c)
    # 先在内存里算完整张周表；算不出来就直接失败，既不清表也不写格。
    try:
        slots = build_week_slots(mids, tids, days=body.days,
                                 blackouts=blackouts, exclusions=exclusions)
    except RotaInfeasible as e:
        c.close()
        raise HTTPException(status_code=409, detail={
            "reason": "insufficient_eligible_members",
            "message": str(e),
            "violations": e.violations,
        })
    c.execute("DELETE FROM assignments WHERE week_id=?", (week_id,))
    for s in slots:
        c.execute("INSERT INTO assignments(week_id,day,task_id,member_id) VALUES (?,?,?,?)",
                  (week_id, s["day"], s["task_id"], s["member_id"]))
    c.execute("UPDATE weeks SET status='ready' WHERE id=?", (week_id,))
    c.commit(); c.close()
    return {"count": len(slots), "slots": slots}

class SwapBody(BaseModel):
    a_day: int; a_task: int; b_day: int; b_task: int; note: str = ""

@app.post("/api/weeks/{week_id}/swaps")
def request_swap(week_id: int, body: SwapBody):
    c = connect()
    assigns = [dict(r) for r in c.execute("SELECT day,task_id,member_id FROM assignments WHERE week_id=?", (week_id,))]
    blackouts, exclusions = load_constraints(c)
    check = swap_legal(assigns, body.a_day, body.a_task, body.b_day, body.b_task,
                       blackouts, exclusions)
    if not check["ok"]:
        c.close()
        raise HTTPException(400, {"reason": check["reason"],
                                  "violations": check.get("violations", [])})
    cur = c.execute(
        "INSERT INTO swap_requests(week_id,a_day,a_task,b_day,b_task,status,note) VALUES (?,?,?,?,?,?,?)",
        (week_id, body.a_day, body.a_task, body.b_day, body.b_task, "pending", body.note))
    c.commit(); sid = cur.lastrowid; c.close()
    return {"id": sid, "status": "pending", **check}

@app.get("/api/swaps")
def list_swaps():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM swap_requests ORDER BY id DESC")]; c.close(); return rows

@app.post("/api/swaps/{swap_id}/confirm")
def confirm_swap(swap_id: int):
    c = connect()
    sw = c.execute("SELECT * FROM swap_requests WHERE id=?", (swap_id,)).fetchone()
    if not sw: c.close(); raise HTTPException(404, "swap not found")
    if sw["status"] != "pending":
        c.close(); raise HTTPException(400, "not_pending")
    assigns = [dict(r) for r in c.execute(
        "SELECT id,day,task_id,member_id FROM assignments WHERE week_id=?", (sw["week_id"],))]
    slots = [{"day": a["day"], "task_id": a["task_id"], "member_id": a["member_id"]} for a in assigns]
    blackouts, exclusions = load_constraints(c)
    try:
        new_slots = apply_swap(slots, sw["a_day"], sw["a_task"], sw["b_day"], sw["b_task"],
                               blackouts, exclusions)
    except ValueError as e:
        violations = getattr(e, "violations", [])
        print("[swap-confirm] 违例格:", violations)
        c.close()
        raise HTTPException(400, {"reason": "constraint_violation", "violations": violations})
    for a, s in zip(assigns, new_slots):
        c.execute("UPDATE assignments SET member_id=? WHERE id=?", (s["member_id"], a["id"]))
    c.execute("UPDATE swap_requests SET status='confirmed' WHERE id=?", (swap_id,))
    c.commit(); c.close()
    return {"ok": True, "swap_id": swap_id}

@app.get("/api/settings")
def get_settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows

@app.put("/api/settings")
def put_settings(body: dict):
    c = connect()
    for k, v in body.items():
        c.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    c.commit(); c.close(); return {"ok": True}
