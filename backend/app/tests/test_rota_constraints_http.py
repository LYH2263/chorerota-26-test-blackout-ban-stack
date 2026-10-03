"""HTTP 生成路径：叠加成功、仅忌日/仅禁配对照、不足失败不写格、
收紧后「再打开」不改写旧周看板且违例对调在申请/确认两端均失败。

与引擎直调路径共用 tests.assertions 同一断言助手（已拍板：共用）。
"""

from app.tests.assertions import (
    assert_board_aligned_with_registries,
    assert_http_rejected,
    assert_registry_rows_equal,
    assert_slots_unchanged,
    slot_tuples,
)


def _board_tuples(board):
    return slot_tuples(board)


def test_http_combined_blackout_and_exclusion(world):
    mids, tids = world.mids, world.tids
    world.add_blackout(mids[1], 0, note="成员忌日")
    world.add_exclusion(tids[1], mids[2], note="人任禁配")

    resp = world.generate()
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == len(tids) * world.days

    _, board = world.board()
    blackout_rows = world.blackouts()
    task_side = world.exclusions_by_task()
    member_side = world.exclusions_by_member()

    # 任务页与成员页禁配列表必须登记在同一张表
    assert_registry_rows_equal(task_side, member_side)
    # 看板格位与成员页忌日、任务/成员页禁配登记同钉；且格数=任务数×天数
    tuples = assert_board_aligned_with_registries(
        board, blackout_rows, task_side, tids, world.days)

    # 忌日当天无该成员
    assert not any(d == 0 and m == mids[1] for d, _, m in tuples)
    # 禁配人对不出现在禁配任务格
    assert not any(t == tids[1] and m == mids[2] for _, t, m in tuples)


def test_http_blackout_only_control(world):
    mids, tids = world.mids, world.tids
    world.add_blackout(mids[1], 0)
    resp = world.generate()
    assert resp.status_code == 200, resp.text
    _, board = world.board()
    tuples = assert_board_aligned_with_registries(
        board, world.blackouts(), world.exclusions_by_task(), tids, world.days)
    assert not any(d == 0 and m == mids[1] for d, _, m in tuples)


def test_http_exclusion_only_control(world):
    mids, tids = world.mids, world.tids
    world.add_exclusion(tids[1], mids[1])
    resp = world.generate()
    assert resp.status_code == 200, resp.text
    _, board = world.board()
    tuples = assert_board_aligned_with_registries(
        board, world.blackouts(), world.exclusions_by_task(), tids, world.days)
    assert not any(t == tids[1] and m == mids[1] for _, t, m in tuples)


def test_http_generate_infeasible_writes_nothing(world):
    mids, tids = world.mids, world.tids
    sentinel = world.insert_sentinel_assignment(day=0, task_id=tids[0], member_id=mids[0])
    before = world.assignment_count()

    # day0/tids[1] 三个人全被挡：前两人忌日，第三人禁配
    world.add_blackout(mids[0], 0)
    world.add_blackout(mids[1], 0)
    world.add_exclusion(tids[1], mids[2])

    resp = world.generate()
    body = assert_http_rejected(resp, expected_status=(409,))
    assert body["reason"] == "insufficient_eligible_members"
    dead = {"day": 0, "task_id": tids[1]}
    assert all(v["day"] == dead["day"] and v["task_id"] == dead["task_id"]
               for v in body["violations"])

    # 不写格：assignments 行数相对生成前不变，哨兵格原样留存
    assert world.assignment_count() == before
    _, board = world.board()
    assert _board_tuples(board) == [sentinel]
    assert world.week_status() == "draft"


def test_http_tighten_reopen_keeps_board_and_rejects_violating_swap(world):
    mids, tids = world.mids, world.tids
    resp = world.generate()
    assert resp.status_code == 200, resp.text
    _, board0 = world.board()
    snapshot = _board_tuples(board0)

    # 取两格：A=(d0,t0) 由 m0 承担，B=(d1,t1) 由 m1 承担（round-robin 保证不同人）
    a = (0, tids[0])
    b = (1, tids[1])
    ma = next(m for d, t, m in snapshot if (d, t) == a)
    mb = next(m for d, t, m in snapshot if (d, t) == b)
    assert ma != mb

    # 生成成功后收紧：mb 忌 A 格当天；ma 被追加禁配 B 格任务
    world.add_blackout(mb, a[0])
    world.add_exclusion(b[1], ma)

    # 「再打开」看板只是 GET，旧周看板不得被改写
    _, board1 = world.board()
    assert_slots_unchanged(board0, board1)
    assert _board_tuples(board1) == snapshot

    # 新对调在申请入口即失败（违忌日 + 违禁配两条违例格）
    body = assert_http_rejected(world.request_swap(a, b), expected_status=(400,))
    rules = {v["rule"] for v in body["violations"]}
    assert rules == {"blackout", "exclusion"}

    # 确认入口同样拦截（服务端 apply_swap 路径，失败打印违例格），且不改表
    sid = world.insert_pending_swap(a, b)
    assert_http_rejected(world.confirm(sid), expected_status=(400,))

    _, board2 = world.board()
    assert _board_tuples(board2) == snapshot
