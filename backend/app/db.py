import os, sqlite3
from contextlib import contextmanager
from pathlib import Path

def db_path() -> Path:
    d = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "pantryfifo.db"

def connect():
    c = sqlite3.connect(db_path(), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA foreign_keys=ON")
    return c

@contextmanager
def write_tx():
    """BEGIN IMMEDIATE: 三路写入（开封/消费/下架）串行化，
    每个写事务读到的行与随后的条件 UPDATE 同属一个判定，
    只会留下一种 qty_remain/status/开封标记。"""
    c = connect()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
