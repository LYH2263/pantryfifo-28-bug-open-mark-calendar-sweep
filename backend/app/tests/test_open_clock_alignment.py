"""开封钟对齐测试：角标 / 紧急条 / 收走 / FEFO 必须是同一次判定。

全部用动态日期建批，不依赖种子数据里的固定到期日，任何日期跑都成立。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app

JUDGE_FIELDS = ("level", "reason_code", "reason", "opened", "open_hours",
                "open_deadline", "hours_left")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with TestClient(app) as c:
        yield c


def mk_lot(client: TestClient, item_id: int = 2, qty: float = 5, days: int = 30) -> int:
    """建一条日历到期在 days 天后的批，返回 lot id。"""
    expiry = (date.today() + timedelta(days=days)).isoformat()
    r = client.post("/api/lots", json={"item_id": item_id, "qty": qty, "expiry": expiry})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def fridge_row(client: TestClient, lot_id: int, layer: str | None = None) -> dict | None:
    q = "/api/fridge" + (f"?layer={layer}" if layer else "")
    return next((r for r in client.get(q).json() if r["id"] == lot_id), None)


def alerts_row(client: TestClient, lot_id: int) -> dict | None:
    return next((r for r in client.get("/api/alerts").json() if r["id"] == lot_id), None)


def open_lot(client: TestClient, lot_id: int) -> dict:
    r = client.post(f"/api/lots/{lot_id}/open")
    assert r.status_code == 200, r.text
    return r.json()


def backdate_open(lot_id: int, *, hours_ago: float | None = None, at: datetime | None = None):
    """直接改库，把 opened_at 挪到过去（模拟开封确认发生在彼时）。"""
    from app.db import connect
    assert (hours_ago is None) != (at is None)
    t = at or (datetime.now(timezone.utc) - timedelta(hours=hours_ago))
    c = connect()
    c.execute("UPDATE lots SET opened_at=? WHERE id=?", (t.isoformat(), lot_id))
    c.commit()
    c.close()


# ---------- 四处同判：开封超时的批，角标/顶条/收走/消费同一结论 ----------

def test_open_expired_lot_same_verdict_everywhere(client):
    lid = mk_lot(client, item_id=3, qty=5, days=30)   # 日历还远
    open_lot(client, lid)                              # 钉住 48h
    backdate_open(lid, hours_ago=60)                   # 开封钟已超时 12h

    row = fridge_row(client, lid)
    assert row["opened"] is True
    assert row["level"] == "expired"
    assert row["reason_code"] == "open_expired"
    assert row["reason"] == "开封超时"
    assert -13 < row["hours_left"] < -11

    # 顶条与角标是同一份判定字段
    a = alerts_row(client, lid)
    assert a is not None, "开封超时批必须上紧急条"
    for f in JUDGE_FIELDS:
        assert a[f] == row[f], f"顶条与角标字段 {f} 不一致"

    # 消费打不到它：有效截止再早也不属于 FEFO
    r = client.post("/api/consume", json={"item_id": 3, "qty": 1})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["reason"] == "short"
    assert lid in detail["expired_skipped"]
    assert all(d["lot_id"] != lid for d in detail["deductions"])

    # 收走：与顶条/角标同一判定
    swept = client.post("/api/expire-sweep").json()["expired_ids"]
    assert lid in swept
    assert fridge_row(client, lid) is None


def test_expired_lot_not_fefo_hit_while_alive_lot_is(client):
    """先到期的批（开封超时）打不到，FEFO 打到的是合格批。"""
    dead = mk_lot(client, item_id=3, qty=5, days=30)
    open_lot(client, dead)
    backdate_open(dead, hours_ago=60)
    alive = mk_lot(client, item_id=3, qty=5, days=30)

    r = client.post("/api/consume", json={"item_id": 3, "qty": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [d["lot_id"] for d in body["deductions"]] == [alive]
    assert dead in body["expired_skipped"]


def test_calendar_expired_unopened_lot_same_verdict(client):
    """未开封仍看日历日：昨天到期的批，四处同判日历到期。"""
    lid = mk_lot(client, item_id=3, qty=5, days=-1)
    row = fridge_row(client, lid)
    assert row["opened"] is False
    assert (row["level"], row["reason_code"], row["reason"]) == ("expired", "calendar_expired", "日历到期")
    a = alerts_row(client, lid)
    for f in JUDGE_FIELDS:
        assert a[f] == row[f]
    assert lid in client.post("/api/expire-sweep").json()["expired_ids"]


# ---------- 钉住不回溯：改默认开封小时，已开封批角标与收走仍按钉住值 ----------

def test_pinned_hours_immune_to_settings_change(client):
    lid = mk_lot(client, item_id=2, qty=3, days=30)
    resp = open_lot(client, lid)
    assert resp["open_hours"] == 48.0
    backdate_open(lid, hours_ago=10)                   # 已流逝 10h，钉住还剩 ~38h

    # 默认调小到 4h：若按当前默认算早已超时，按钉住值则安然无恙
    assert client.put("/api/settings", json={"default_open_hours": 4}).status_code == 200
    row = fridge_row(client, lid)
    assert row["open_hours"] == 48.0
    assert 37 < row["hours_left"] < 39, "角标必须仍按钉住的 48h，不得跟新默认"
    assert row["level"] == "ok"
    assert lid not in client.post("/api/expire-sweep").json()["expired_ids"], \
        "收走也必须按钉住值，不得按新默认小时"

    # 默认调大到 200h：角标同样不动
    assert client.put("/api/settings", json={"default_open_hours": 200}).status_code == 200
    row = fridge_row(client, lid)
    assert row["open_hours"] == 48.0
    assert 37 < row["hours_left"] < 39

    # 新默认只影响此后的开封
    lid2 = mk_lot(client, item_id=2, qty=1, days=30)
    assert open_lot(client, lid2)["open_hours"] == 200.0


# ---------- 开封预览/确认：不扣余量，确认后总表与分层页角标一致，失败回滚 ----------

def test_open_preview_and_confirm_never_touch_qty(client):
    lid = mk_lot(client, item_id=2, qty=7, days=30)

    pv = client.get(f"/api/lots/{lid}/open-preview").json()
    assert pv["pinned_open_hours"] == 48.0
    assert pv["qty_remain"] == 7
    row = fridge_row(client, lid)
    assert row["opened"] is False and row["qty_remain"] == 7, "预览不得改动任何状态"

    resp = open_lot(client, lid)
    assert resp["opened"] is True
    assert resp["open_hours"] == 48.0
    assert resp["qty_remain"] == 7 and resp["qty_unchanged"] is True, "开封绝不扣余量"
    assert 47.9 < resp["hours_left"] <= 48.0

    # 总表（全层）与分层页拿到同一份判定：该批两处都看得出已开封
    for layer in (None, "mid"):
        row = fridge_row(client, lid, layer=layer)
        assert row is not None and row["opened"] is True
        for f in JUDGE_FIELDS:
            assert row[f] == resp[f], f"layer={layer} 字段 {f} 与开封响应不一致"

    # 确认失败（重复开封）：余量/角标/开封标记全部保持开封后状态，不被失败写动
    before = fridge_row(client, lid)
    r = client.post(f"/api/lots/{lid}/open")
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "already_opened"
    after = fridge_row(client, lid)
    for f in ("qty_remain", "opened", "opened_at", "open_hours") + JUDGE_FIELDS:
        assert after[f] == before[f]


def test_open_failure_on_gone_lot_rolls_back(client):
    """批被消费完后开封确认失败：批状态保持 consumed，不留半截开封标记。"""
    # item 3 的在架合格批只有本条（种子批早已日历到期），FEFO 必然扣到它
    lid = mk_lot(client, item_id=3, qty=1, days=30)
    r = client.post("/api/consume", json={"item_id": 3, "qty": 1})
    assert r.status_code == 200
    assert [d["lot_id"] for d in r.json()["deductions"]] == [lid]
    r = client.post(f"/api/lots/{lid}/open")
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "not_on_shelf"
    assert fridge_row(client, lid) is None, "已消耗批不得因失败的开封重新出现在总表"


# ---------- 跨自然日：钉的是确认时剩下的绝对小时数，三处同一世界 ----------

def test_cross_midnight_same_world(client):
    lid = mk_lot(client, item_id=2, qty=5, days=30)
    open_lot(client, lid)                              # 钉住 48h

    # 确认发生在昨天 23:30(UTC)：截止在明天 23:30，横跨两个午夜仍未超时
    yesterday_2330 = datetime.combine(date.today(), time.min, tzinfo=timezone.utc) - timedelta(minutes=30)
    backdate_open(lid, at=yesterday_2330)
    row = fridge_row(client, lid)
    assert row["level"] == "ok" and 23.4 < row["hours_left"] < 47.6
    assert alerts_row(client, lid) is None
    assert lid not in client.post("/api/expire-sweep").json()["expired_ids"]

    # 同一批挪到超时（截止已过去 1h）：三处一起翻成开封超时，无一掉队
    backdate_open(lid, hours_ago=49)
    row = fridge_row(client, lid)
    assert (row["level"], row["reason_code"]) == ("expired", "open_expired")
    a = alerts_row(client, lid)
    assert a is not None and a["reason_code"] == "open_expired"
    assert lid in client.post("/api/expire-sweep").json()["expired_ids"]
