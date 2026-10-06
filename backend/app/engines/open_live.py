"""已开封批的对齐层：所有出口共用 assess_lot 的同一次判定。

钉住语义：开封截止期 = opened_at + 批上 open_hours（开封瞬间钉死的剩余小时）。
这是一个绝对时刻，按钟点走、自然跨日；顶条、层页角标、过期下架、消费资格
都从这同一次判定取数，不存在“一处跨日、一处钉死”的两个世界。
修改默认开封小时不回溯已钉住的批；仅对“已开封但缺钉住小时”的历史行，
用当前默认小时兜底，保证老数据仍有确定的开封钟。
"""
from datetime import datetime

from app.engines.lot_status import assess_lot, hard_expire_reason


def _pin_legacy_hours(lot: dict, live_hours: float) -> dict:
    """已开封但批上没有钉住小时（历史行）：用当前默认兜底；已钉住的原样保留。"""
    if lot.get("opened_at") and lot.get("open_hours") is None:
        return {**lot, "open_hours": float(live_hours)}
    return lot


def fridge_row(lot: dict, now: datetime, warn_days: int, live_hours: float) -> dict:
    """层页角标/总表行：reason、level、剩余小时全部来自 assess_lot 一次判定。

    已开封标记（opened）与紧急原因字（reason）是两个独立字段，互不覆盖。
    """
    row = _pin_legacy_hours(dict(lot), live_hours)
    row.update(assess_lot(row, now=now, warn_days=warn_days))
    return row


def expire_reason(lot: dict, now: datetime, live_hours: float) -> str:
    """下架判定：与角标/顶条同一次 assess_lot；不需下架返回空串。

    已开封批在日历截止或钉住的开封截止任一到达即下架（更早者为生效原因），
    小时数一律用批上钉住值，不跟随后续默认开封小时的修改。
    """
    return hard_expire_reason(_pin_legacy_hours(lot, live_hours), now)


def sweep_hit(lot: dict, now: datetime, live_hours: float) -> bool:
    return bool(expire_reason(lot, now, live_hours))
