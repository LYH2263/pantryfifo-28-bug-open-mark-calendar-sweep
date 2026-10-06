from datetime import datetime, timedelta, timezone

from app.db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, name TEXT, layer TEXT, unit TEXT);
CREATE TABLE IF NOT EXISTS lots(
  id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INT, qty_in REAL, qty_remain REAL,
  expiry TEXT, status TEXT, data_quality TEXT,
  opened_at TEXT, open_hours REAL
);
CREATE TABLE IF NOT EXISTS consumptions(id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT, result_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""

def _columns(c, table):
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}

def migrate(c):
    """老库补列：开封时刻与开封时钉住的剩余小时数。"""
    cols = _columns(c, "lots")
    if "opened_at" not in cols:
        c.execute("ALTER TABLE lots ADD COLUMN opened_at TEXT")
    if "open_hours" not in cols:
        c.execute("ALTER TABLE lots ADD COLUMN open_hours REAL")

def init_db():
    c = connect()
    c.executescript(SCHEMA)
    migrate(c)
    if c.execute("SELECT COUNT(*) c FROM settings").fetchone()["c"] == 0:
        c.executemany("INSERT INTO settings(key,value) VALUES (?,?)", [
            ("warn_days", "3"),
            ("default_open_hours", "48"),
        ])
    else:
        # 升级老设置：缺省默认开封小时
        if not c.execute("SELECT 1 FROM settings WHERE key='default_open_hours'").fetchone():
            c.execute("INSERT INTO settings(key,value) VALUES ('default_open_hours','48')")
    if c.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 0:
        c.executemany("INSERT INTO items(name,layer,unit) VALUES (?,?,?)", [
            ("牛奶", "upper", "盒"), ("鸡蛋", "mid", "个"), ("冻饺", "lower", "袋"),
        ])
        c.executemany(
            """INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality,opened_at,open_hours)
               VALUES (?,?,?,?,?,?,?,?)""",
            [
                (1, 2, 2, "2026-10-01", "on_shelf", "clean", None, None),
                (1, 1, 1, "2026-09-28", "on_shelf", "clean", None, None),
                (2, 12, 12, "2026-11-01", "on_shelf", "clean", None, None),
                (3, 1, 1, "2025-01-01", "on_shelf", "dirty", None, None),
                (2, -3, -3, "2026-12-01", "on_shelf", "dirty", None, None),
                # 已开封未超时（钉住 48h，已过 5h）；日历到期仍远
                (1, 1, 1, "2026-12-20", "on_shelf", "clean",
                 (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(), 48.0),
                # 已开封超时待下架（钉住 48h，已过 60h）；日历还没到
                (1, 1, 1, "2026-12-25", "on_shelf", "clean",
                 (datetime.now(timezone.utc) - timedelta(hours=60)).isoformat(), 48.0),
            ],
        )
    c.commit()
    c.close()
