"""开封钟对齐回归：角标、紧急原因字、收走、消费资格必须是同一次判定。

覆盖的撕裂面（每个测试对应一类曾出现的坏现象）：
- 分层页标了开封，顶条却按入库到期日走日历字；
- 收走按另一套小时/日历口径，跟角标不在同一个世界；
- 改默认开封小时后角标/收走被回溯；
- 先到期的批还能被 FEFO 打到；
- 开封确认跨自然日后资格口径不一；
- 开封扣了余量 / 失败后状态没回到开封前。
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.engines.lot_status import assess_lot

FAR = "2027-06-01"
YESTERDAY = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.main import app
    with TestClient(app) as cl:
        yield cl


def insert_lot(item_id=1, qty=1.0, expiry=FAR, opened_at=None, open_hours=None,
               status="on_shelf"):
    c = connect()
    cur = c.execute(
        """INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality,opened_at,open_hours)
           VALUES (?,?,?,?,?,?,?,?)""",
        (item_id, qty, qty, expiry, status, "clean",
         opened_at.isoformat() if opened_at else None, open_hours))
    c.commit(); lid = cur.lastrowid; c.close()
    return lid


def insert_item(name="测试品", layer="mid"):
    """独立物品，避开种子数据对消费排序/资格的干扰。"""
    c = connect()
    cur = c.execute("INSERT INTO items(name,layer,unit) VALUES (?,?,?)", (name, layer, "份"))
    c.commit(); iid = cur.lastrowid; c.close()
    return iid


def get_lot(lot_id):
    c = connect()
    row = dict(c.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone())
    c.close()
    return row


def fridge_row(client, lot_id):
    rows = client.get("/api/fridge").json()
    return next((r for r in rows if r["id"] == lot_id), None)


def hours_ago(h):
    return datetime.now(timezone.utc) - timedelta(hours=h)


# ---------- 同一次判定：角标 / 顶条 / 收走 ----------

def test_open_expired_same_determination_everywhere(client):
    lid = insert_lot(opened_at=hours_ago(60), open_hours=48.0)  # 开封已超时，日历还远
    row = fridge_row(client, lid)
    assert row["opened"] is True
    assert row["level"] == "expired"
    assert row["reason_code"] == "open_expired"
    assert row["reason"] == "开封超时"           # 角标原因字：不是日历字

    alert = next(a for a in client.get("/api/alerts").json() if a["id"] == lid)
    assert alert["reason"] == "开封超时"          # 顶条与角标同一句话
    assert alert["reason_code"] == "open_expired"

    swept = client.post("/api/expire-sweep").json()
    assert swept["reasons"][str(lid)] == "open_expired"  # 收走按同一判定
    assert lid in swept["expired_ids"]
    assert fridge_row(client, lid) is None       # 收走后总表/分层页都不再见


def test_opened_but_calendar_expired_sweeps_by_calendar(client):
    # 已开封、开封未超时，但日历到期：更早的日历截止生效，收走不能漏
    lid = insert_lot(expiry=YESTERDAY, opened_at=hours_ago(1), open_hours=48.0)
    row = fridge_row(client, lid)
    assert row["reason_code"] == "calendar_expired"
    assert row["reason"] == "日历到期"
    swept = client.post("/api/expire-sweep").json()
    assert swept["reasons"][str(lid)] == "calendar_expired"


def test_unopened_lot_still_calendar_only(client):
    lid = insert_lot(expiry=YESTERDAY)
    row = fridge_row(client, lid)
    assert row["opened"] is False
    assert row["reason_code"] == "calendar_expired"
    assert row["hours_left"] is None


# ---------- 改默认开封小时：钉住值不回溯 ----------

def test_pinned_hours_immune_to_default_change(client):
    expired_lid = insert_lot(opened_at=hours_ago(60), open_hours=48.0)  # 钉 48h 已超时
    fresh_lid = insert_lot(opened_at=hours_ago(30), open_hours=48.0)    # 钉 48h 未超时

    r = client.put("/api/settings", json={"default_open_hours": 200})
    assert r.status_code == 200

    row = fridge_row(client, fresh_lid)
    assert row["open_hours"] == 48.0                 # 角标跟钉住值，不跟新默认
    assert 17.0 < row["hours_left"] < 18.1           # 48-30≈18h，不是 200-30
    assert row["level"] == "ok"

    # 收走也按钉住值：默认放大到 200h 救不回已超时批
    swept = client.post("/api/expire-sweep").json()
    assert expired_lid in swept["expired_ids"]
    assert fresh_lid not in swept["expired_ids"]

    # 反向：默认调小也不回溯未超时批
    client.put("/api/settings", json={"default_open_hours": 24})
    assert fridge_row(client, fresh_lid)["level"] == "ok"
    assert fresh_lid not in client.post("/api/expire-sweep").json()["expired_ids"]


# ---------- 消费资格：先到期的批不能再被打到 ----------

def test_consume_skips_hard_expired_lots(client):
    iid = insert_item()
    bad_open = insert_lot(item_id=iid, qty=5, opened_at=hours_ago(60), open_hours=48.0)
    bad_cal = insert_lot(item_id=iid, qty=5, expiry=YESTERDAY)
    good = insert_lot(item_id=iid, qty=2, expiry=FAR)

    r = client.post("/api/consume", json={"item_id": iid, "qty": 2})
    assert r.status_code == 200
    body = r.json()
    assert [d["lot_id"] for d in body["deductions"]] == [good]
    assert set(body["skipped_expired_ids"]) == {bad_open, bad_cal}
    assert get_lot(bad_open)["qty_remain"] == 5.0    # 到期批一克没被动
    assert get_lot(bad_cal)["qty_remain"] == 5.0

    # 合格库存不够时按短缺报，绝不拿到期批凑数
    r = client.post("/api/consume", json={"item_id": iid, "qty": 10})
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "short"


def test_fefo_prefers_soonest_effective_deadline_among_eligible(client):
    # 都未硬到期时：开封剩余 5h 的批排在日历明天到期的批前面
    iid = insert_item()
    opened = insert_lot(item_id=iid, qty=1, opened_at=hours_ago(43), open_hours=48.0)
    cal = insert_lot(item_id=iid, qty=1,
                     expiry=(datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat())
    r = client.post("/api/consume", json={"item_id": iid, "qty": 1})
    assert r.status_code == 200
    body = r.json()
    assert [d["lot_id"] for d in body["deductions"]] == [opened]
    assert body["skipped_expired_ids"] == []


# ---------- 开封：不扣余量、失败回滚、总表可见 ----------

def test_open_preview_and_confirm_never_deduct(client):
    lid = insert_lot(qty=3.0)
    before = get_lot(lid)["qty_remain"]

    prev = client.get(f"/api/lots/{lid}/open-preview").json()
    assert prev["qty_remain"] == before              # 预览只报将钉的小时
    assert prev["pinned_open_hours"] == 48.0
    assert get_lot(lid)["qty_remain"] == before      # 预览不落任何写

    r = client.post(f"/api/lots/{lid}/open")
    assert r.status_code == 200
    out = r.json()
    assert out["qty_unchanged"] is True
    assert out["qty_remain"] == before               # 开封不扣余量
    assert out["opened"] is True
    assert out["open_hours"] == 48.0
    assert get_lot(lid)["qty_remain"] == before


def test_open_confirm_visible_and_consistent_in_master_and_layer(client):
    lid = insert_lot(item_id=1, qty=2.0)             # 牛奶：upper 层
    client.post(f"/api/lots/{lid}/open")

    master = fridge_row(client, lid)                 # 总表（全层）同源 /fridge
    assert master["opened"] is True
    assert master["open_hours"] == 48.0
    assert 47.9 < master["hours_left"] <= 48.0

    layer_rows = client.get("/api/fridge?layer=upper").json()
    layer = next(r for r in layer_rows if r["id"] == lid)
    for k in ("opened", "open_hours", "open_deadline", "level", "reason_code", "reason"):
        assert layer[k] == master[k]                 # 分层页角标与总表同一份判定


def test_failed_open_confirm_rolls_back(client):
    lid = insert_lot(qty=2.0)
    client.post(f"/api/lots/{lid}/open")
    before = get_lot(lid)

    r = client.post(f"/api/lots/{lid}/open")         # 重复开封：失败
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "already_opened"
    assert get_lot(lid) == before                    # 余量/开封标记原样

    gone = insert_lot(qty=1.0, status="consumed")
    r = client.post(f"/api/lots/{gone}/open")        # 非在架：失败
    assert r.status_code == 409
    assert get_lot(gone)["opened_at"] is None        # 没留下半截开封标记
    assert fridge_row(client, gone) is None


# ---------- 开封钟跨自然日：按钟点走，不按日历日跳 ----------

def test_open_clock_crosses_midnight_by_clock_not_calendar():
    opened = datetime(2026, 10, 5, 23, 30, tzinfo=timezone.utc)
    lot = {"opened_at": opened.isoformat(), "open_hours": 2.0, "expiry": FAR}

    # 跨日 00:30：开封才过 1h，日历日变了但开封钟没超时
    a = assess_lot(lot, now=datetime(2026, 10, 6, 0, 30, tzinfo=timezone.utc))
    assert a["level"] == "ok"
    assert a["hours_left"] == pytest.approx(1.0)

    # 01:30 整到达钉住时刻；01:31 起超时——与是否跨日无关
    a = assess_lot(lot, now=datetime(2026, 10, 6, 1, 31, tzinfo=timezone.utc))
    assert a["level"] == "expired"
    assert a["reason_code"] == "open_expired"
    assert a["reason"] == "开封超时"


def test_both_deadlines_passed_earlier_one_wins():
    opened = datetime(2026, 10, 5, 20, 0, tzinfo=timezone.utc)
    # 开封截止 10-07 20:00，日历截止 10-06 00:00：日历更早，先生效
    lot = {"opened_at": opened.isoformat(), "open_hours": 48.0, "expiry": "2026-10-06"}
    a = assess_lot(lot, now=datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc))
    assert a["reason_code"] == "calendar_expired"

    # 开封截止更早：开封超时生效
    lot = {"opened_at": opened.isoformat(), "open_hours": 2.0, "expiry": "2026-10-06"}
    a = assess_lot(lot, now=datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc))
    assert a["reason_code"] == "open_expired"
