"""FEFO consume: earliest *effective* deadline first.

有效截止期：未开封批=日历到期日；已开封批=min(日历到期, 开封钉住的超时时刻)。
与 engines.lot_status 的判定口径保持一致，避免“消费排序只看日历、报警却喊开封”。
"""
from datetime import datetime, time, timezone

from app.engines.lot_status import calendar_deadline, open_deadline

MAX_DT = datetime(9999, 12, 31, tzinfo=timezone.utc)


def effective_deadline(lot: dict) -> datetime | None:
    cd = calendar_deadline(lot)
    od = open_deadline(lot)
    ds = [d for d in (cd, od) if d is not None]
    return min(ds) if ds else None


def sort_lots_fefo(lots: list[dict]) -> list[dict]:
    return sorted(
        [l for l in lots if float(l.get("qty_remain", 0)) > 0],
        key=lambda l: (effective_deadline(l) or MAX_DT, l.get("id") or 0),
    )


def consume_fefo(lots: list[dict], qty: float) -> dict:
    """Return deductions list and leftover demand. Mutates copies only."""
    need = float(qty)
    if need <= 0:
        return {"ok": False, "reason": "qty_non_positive", "deductions": [], "short": 0.0}
    ordered = sort_lots_fefo(lots)
    deductions = []
    for lot in ordered:
        if need <= 0:
            break
        avail = float(lot["qty_remain"])
        take = min(avail, need)
        deductions.append({
            "lot_id": lot["id"],
            "take": take,
            "expiry": lot.get("expiry"),
            "opened": bool(lot.get("opened_at")),
        })
        need -= take
    if need > 1e-9:
        return {"ok": False, "reason": "short", "deductions": deductions, "short": round(need, 3)}
    return {"ok": True, "reason": "", "deductions": deductions, "short": 0.0}


def expire_lots(lots: list[dict], today: str) -> list[int]:
    """日历口径的下架判定（保留给纯函数测试/无开封场景）。

    线上 /api/expire-sweep 走 engines.lot_status 的统一判定，
    开封超时同样下架。此处仅按 expiry < today 处理。
    """
    out = []
    for l in lots:
        exp = l.get("expiry")
        if exp and exp < today and float(l.get("qty_remain", 0)) > 0:
            out.append(l["id"])
    return out
