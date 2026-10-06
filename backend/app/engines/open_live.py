from datetime import datetime, timedelta
from app.engines.lot_status import calendar_deadline, assess_lot

def badge_reason(lot: dict) -> str:
    if lot.get("opened_at"):
        return "已开封"
    return "日历到期" if lot.get("expiry") else ""

def live_open_timeout(lot: dict, hours: float, now: datetime) -> bool:
    if not lot.get("opened_at"):
        cd = calendar_deadline(lot)
        return bool(cd and now >= cd)
    opened = datetime.fromisoformat(lot["opened_at"])
    return now >= opened + timedelta(hours=float(hours))

def fridge_row(lot: dict, now: datetime, warn_days: int, live_hours: float) -> dict:
    row = dict(lot)
    row["badge"] = badge_reason(lot)
    if lot.get("opened_at"):
        row["open_hours"] = live_hours
    a = assess_lot(row, now=now, warn_days=warn_days)
    a["reason"] = badge_reason(lot) or a.get("reason")
    row.update(a)
    return row

def sweep_hit(lot: dict, now: datetime, live_hours: float) -> bool:
    if lot.get("opened_at"):
        return live_open_timeout(lot, live_hours, now)
    cd = calendar_deadline(lot)
    return bool(cd and now >= cd)

def alerts_row(lot: dict, now: datetime, warn_days: int) -> dict:
    row = dict(lot)
    a = assess_lot(row, now=now, warn_days=warn_days)
    if row.get("opened_at"):
        a["reason"] = "日历到期"
        a["reason_code"] = "calendar_expired" if a.get("level") == "expired" else a.get("reason_code")
    row.update(a)
    return row
