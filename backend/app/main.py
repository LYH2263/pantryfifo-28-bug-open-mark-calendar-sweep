import json
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect, write_tx
from app.engines.fefo import consume_fefo
from app.engines.lot_status import utcnow
from app.engines import open_live

app = FastAPI(title="Pantryfifo", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

@app.get("/api/health")
def health(): return {"ok": True, "project": "pantryfifo"}


# ---------- 共享读取：判定字段只在 assess_lot 这一个出口产出 ----------

LOT_COLS = "lots.*, items.name, items.layer, items.unit"

def _settings(c) -> dict:
    return {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}

def _default_open_hours(c) -> float:
    row = c.execute("SELECT value FROM settings WHERE key='default_open_hours'").fetchone()
    return float(row["value"]) if row else 48.0

def _warn_days(c) -> int:
    row = c.execute("SELECT value FROM settings WHERE key='warn_days'").fetchone()
    return int(row["value"]) if row else 3

def _annotate(lot: dict, now: datetime, warn_days: int, live_hours: float) -> dict:
    """把 assess_lot 的一次判定挂到批上；顶条、层页、总表拿到的是同一份字段。"""
    return open_live.fridge_row(lot, now, warn_days, live_hours)

def _get_lot(c, lot_id: int) -> dict | None:
    row = c.execute(f"SELECT {LOT_COLS} FROM lots JOIN items ON items.id=lots.item_id WHERE lots.id=?",
                    (lot_id,)).fetchone()
    return dict(row) if row else None


@app.get("/api/items")
def items():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM items")]; c.close(); return rows

@app.get("/api/fridge")
def fridge(layer: str | None = None):
    c = connect()
    warn = _warn_days(c); now = utcnow()
    q = f"""SELECT {LOT_COLS} FROM lots JOIN items ON items.id=lots.item_id
            WHERE lots.status='on_shelf'"""
    args = []
    if layer:
        q += " AND items.layer=?"; args.append(layer)
    live = _default_open_hours(c)
    rows = [_annotate(dict(r), now, warn, live) for r in c.execute(q, args)]
    c.close()
    return rows

@app.get("/api/alerts")
def alerts():
    """顶条：与层页角标共用 _annotate 的同一次判定口径，原因字不另起炉灶。"""
    c = connect()
    warn = _warn_days(c); now = utcnow(); live = _default_open_hours(c)
    rows = [dict(r) for r in c.execute(
        f"""SELECT {LOT_COLS} FROM lots JOIN items ON items.id=lots.item_id
            WHERE status='on_shelf' AND qty_remain>0""")]
    c.close()
    out = []
    for r in rows:
        a = _annotate(r, now, warn, live)
        if a["level"] in ("expired", "soon"):
            out.append(a)
    # 硬到期排前
    out.sort(key=lambda a: (a["level"] != "expired", a["id"]))
    return out


# ---------- 入库 ----------

class LotIn(BaseModel):
    item_id: int
    qty: float
    expiry: str

@app.post("/api/lots")
def inbound(body: LotIn):
    c = connect()
    item = c.execute("SELECT id FROM items WHERE id=?", (body.item_id,)).fetchone()
    if not item: c.close(); raise HTTPException(404, "item")
    cur = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) VALUES (?,?,?,?,?,?)",
        (body.item_id, body.qty, body.qty, body.expiry, "on_shelf", "clean"))
    c.commit(); lid = cur.lastrowid; c.close(); return {"id": lid}


# ---------- 开封：预览（只读，不动余量） ----------

@app.get("/api/lots/{lot_id}/open-preview")
def open_preview(lot_id: int):
    """对在架正余量、未开封的批：只报“将钉住的剩余小时数”，qty_remain 原样回显。"""
    c = connect()
    lot = _get_lot(c, lot_id)
    if not lot: c.close(); raise HTTPException(404, "lot")
    if lot["status"] != "on_shelf" or float(lot["qty_remain"]) <= 0:
        c.close(); raise HTTPException(409, {"reason": "not_on_shelf", "lot_id": lot_id})
    if lot["opened_at"]:
        c.close(); raise HTTPException(409, {"reason": "already_opened", "lot_id": lot_id})
    hours = _default_open_hours(c)
    now = utcnow()
    c.close()
    return {
        "lot_id": lot_id,
        "qty_remain": lot["qty_remain"],   # 保持，不扣减
        "pinned_open_hours": hours,         # 确认时将钉到批上的剩余小时
        "would_open_at": now.isoformat(),
        "would_open_deadline": (now + timedelta(hours=hours)).isoformat(),
    }


# ---------- 开封：确认（只写开封标记，绝不扣减余量） ----------

@app.post("/api/lots/{lot_id}/open")
def open_lot(lot_id: int):
    with write_tx() as c:
        lot = _get_lot(c, lot_id)
        if not lot: raise HTTPException(404, "lot")
        if lot["opened_at"]:
            raise HTTPException(409, {"reason": "already_opened", "lot_id": lot_id})
        if lot["status"] != "on_shelf" or float(lot["qty_remain"]) <= 0:
            raise HTTPException(409, {"reason": "not_on_shelf", "lot_id": lot_id,
                                     "status": lot["status"]})
        hours = _default_open_hours(c)
        now = utcnow()
        # 条件 UPDATE：并发下若该批刚被消费完/下架/开封，本条不落地
        cur = c.execute(
            """UPDATE lots SET opened_at=?, open_hours=?
               WHERE id=? AND status='on_shelf' AND qty_remain>0 AND opened_at IS NULL""",
            (now.isoformat(), hours, lot_id))
        if cur.rowcount != 1:
            raise HTTPException(409, {"reason": "concurrent_change", "lot_id": lot_id})
        row = c.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone()
        out = _annotate(dict(row) | {"name": lot["name"], "layer": lot["layer"], "unit": lot["unit"]},
                        now, _warn_days(c), hours)
        # 显式回显：余量与开封前一致
        out["qty_unchanged"] = out["qty_remain"] == lot["qty_remain"]
        return out


# ---------- FEFO 消费 ----------

class ConsumeIn(BaseModel):
    item_id: int
    qty: float
    note: str = ""

@app.post("/api/consume")
def consume(body: ConsumeIn):
    with write_tx() as c:
        # 持写锁后读取，FEFO 判定与扣减同属一个事务，不会读到半路状态
        lots = [dict(r) for r in c.execute(
            "SELECT * FROM lots WHERE item_id=? AND status='on_shelf' AND qty_remain>0",
            (body.item_id,))]
        # 消费资格与顶条/角标/下架同一次判定：硬到期（日历或开封超时）的批
        # 不再参与 FEFO，等下架；不会出现“先到期却还能打到该批”。
        now = utcnow(); live = _default_open_hours(c)
        eligible, skipped = [], []
        for l in lots:
            if open_live.sweep_hit(l, now, live):
                skipped.append(l["id"])
            else:
                eligible.append(l)
        result = consume_fefo(eligible, body.qty)
        result["skipped_expired_ids"] = skipped
        if not result["ok"] and result["reason"] == "qty_non_positive":
            raise HTTPException(400, result["reason"])
        if not result["ok"]:
            raise HTTPException(409, result)
        for d in result["deductions"]:
            cur = c.execute(
                """UPDATE lots SET qty_remain = qty_remain - ?
                   WHERE id=? AND status='on_shelf' AND qty_remain>=?""",
                (d["take"], d["lot_id"], d["take"]))
            if cur.rowcount != 1:
                # 与 sweep/open 并发输掉：整笔回滚，让三路写入只留一种结果
                raise HTTPException(409, {"reason": "concurrent_change", "lot_id": d["lot_id"]})
            rem = c.execute("SELECT qty_remain FROM lots WHERE id=?", (d["lot_id"],)).fetchone()["qty_remain"]
            if rem <= 0:
                cur2 = c.execute(
                    "UPDATE lots SET status='consumed', qty_remain=0 WHERE id=? AND qty_remain=0",
                    (d["lot_id"],))
                if cur2.rowcount != 1:
                    raise HTTPException(409, {"reason": "concurrent_change", "lot_id": d["lot_id"]})
        c.execute("INSERT INTO consumptions(note,result_json,created_at) VALUES (?,?,?)",
                  (body.note, json.dumps(result), utcnow().isoformat()))
        return result


# ---------- 过期/开封超时下架 ----------

@app.post("/api/expire-sweep")
def expire_sweep():
    """下架与顶条/角标同一次判定：原因码逐批回显，可核对是否同一世界。"""
    c = connect()
    now = utcnow()
    live = _default_open_hours(c)
    lots = [dict(r) for r in c.execute("SELECT * FROM lots WHERE status='on_shelf'")]
    reasons = {l["id"]: r for l in lots if (r := open_live.expire_reason(l, now, live))}
    for i in reasons:
        c.execute("UPDATE lots SET status='expired' WHERE id=? AND status='on_shelf'", (i,))
    c.commit(); c.close()
    return {"expired_ids": list(reasons), "reasons": reasons}


@app.get("/api/settings")
def settings():
    c = connect(); rows = _settings(c); c.close(); return rows

class SettingsIn(BaseModel):
    warn_days: int | None = None
    default_open_hours: float | None = None

@app.put("/api/settings")
def update_settings(body: SettingsIn):
    """改默认开封小时只影响此后的开封；已开封批的 open_hours 在开封时已钉住，
    无论是否已超时待下架，顶条/角标/下架都继续按钉住值走，不回溯。"""
    if body.warn_days is not None and body.warn_days < 0:
        raise HTTPException(400, "warn_days must be >= 0")
    if body.default_open_hours is not None and body.default_open_hours <= 0:
        raise HTTPException(400, "default_open_hours must be > 0")
    with write_tx() as c:
        if body.warn_days is not None:
            c.execute("INSERT INTO settings(key,value) VALUES('warn_days',?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(body.warn_days),))
        if body.default_open_hours is not None:
            c.execute("INSERT INTO settings(key,value) VALUES('default_open_hours',?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(body.default_open_hours),))
        out = _settings(c)
    return out
