"""批次状态的唯一判定来源。

顶条（/api/alerts）、层页角标（/api/fridge）、过期下架（/api/expire-sweep）
都必须调用 assess_lot：同一条批在任何地方得到的 reason_code / 原因字都来自
这同一次判定，杜绝“顶条喊开封超时、角标还停在日历到期”的撕裂。

规则：
- 未开封批只走日历到期。
- 已开封批在开封瞬间把 open_hours 钉死在批上，开封截止期 =
  opened_at + open_hours（钉住的小时数），之后修改默认开封小时不回溯。
- 日历截止与开封截止都存在时，更早到达的那个为生效原因。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

CALENDAR_EXPIRED = "calendar_expired"
OPEN_EXPIRED = "open_expired"
CALENDAR_SOON = "calendar_soon"

REASON_WORDS = {
    CALENDAR_EXPIRED: "日历到期",
    OPEN_EXPIRED: "开封超时",
    CALENDAR_SOON: "临期",
}

HARD_EXPIRED = {CALENDAR_EXPIRED, OPEN_EXPIRED}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def open_deadline(lot: dict) -> datetime | None:
    """钉住的开封超时时刻；未开封返回 None。"""
    if lot.get("opened_at") and lot.get("open_hours") is not None:
        return datetime.fromisoformat(lot["opened_at"]) + timedelta(hours=float(lot["open_hours"]))
    return None


def calendar_deadline(lot: dict) -> datetime | None:
    """到期日按当天 00:00(UTC) 截止，与历史 date 比较语义一致。"""
    if lot.get("expiry"):
        return datetime.combine(date.fromisoformat(lot["expiry"]), time.min, tzinfo=timezone.utc)
    return None


def assess_lot(lot: dict, now: datetime | None = None, warn_days: int = 3) -> dict:
    """对一条批做一次完整判定。返回 level/reason_code/reason/剩余量等全部展示字段。"""
    now = now or utcnow()
    today = now.date()
    opened = bool(lot.get("opened_at"))
    od = open_deadline(lot)
    cd = calendar_deadline(lot)

    hours_left = round((od - now) / timedelta(hours=1), 2) if od else None
    cal_days_left = (date.fromisoformat(lot["expiry"]) - today).days if lot.get("expiry") else None

    cal_expired = cd is not None and now >= cd
    open_expired = False

    reason_code = ""
    if cal_expired or open_expired:
        # 两个截止都已到：取更早者作为这次判定的唯一原因
        if cal_expired and open_expired:
            reason_code = CALENDAR_EXPIRED if cd <= od else OPEN_EXPIRED
        elif cal_expired:
            reason_code = CALENDAR_EXPIRED
        else:
            reason_code = OPEN_EXPIRED
        level = "expired"
    elif cal_days_left is not None and 0 <= cal_days_left <= int(warn_days):
        # 临期预警只走日历；开封未超时的剩余小时在角标直接展示
        reason_code = CALENDAR_SOON
        level = "soon"
    else:
        level = "ok"

    return {
        "opened": opened,
        "opened_at": lot.get("opened_at"),
        "open_hours": float(lot["open_hours"]) if lot.get("open_hours") is not None else None,
        "open_deadline": od.isoformat() if od else None,
        "hours_left": hours_left,
        "days_left": cal_days_left,
        "level": level,
        "reason_code": reason_code,
        "reason": REASON_WORDS.get(reason_code, ""),
    }


def hard_expire_reason(lot: dict, now: datetime | None = None, warn_days: int = 3) -> str:
    """sweep 用：返回硬下架原因码；不需下架返回空串。"""
    a = assess_lot(lot, now, warn_days)
    return a["reason_code"] if a["reason_code"] in HARD_EXPIRED else ""
