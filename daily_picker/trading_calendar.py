"""A股交易日历：周末 + 法定节假日休市。

数据来源（上海证券交易所公告）：
- 《2026年休市安排》: https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20251222_10802510.shtml
- 《关于上海证券交易所2025年部分节假日休市安排的通知》
- 《关于上海证券交易所2026年部分节假日休市安排的通知》（上证公告〔2025〕45号）

说明：
- 表里只列「工作日但休市」的日期；周末休市由 weekday() 判断覆盖，无需重复列出。
- 交易所一般在每年 12 月公布下一年度安排。若查询年份不在 COVERED_YEARS 内，
  is_trading_day() 会退化为「仅按周末判断」，同时 calendar_covers() 返回 False，
  调用方可据此提示「节假日日历需要更新」，避免静默出错。

用法：
    from daily_picker.trading_calendar import (
        is_trading_day, next_trading_day, prev_trading_day,
        last_trading_day_on_or_before, holiday_name, calendar_covers,
    )
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

# 休市日 -> 假期名（仅工作日；节假日调休上班的周末不补交易，A股不补班）
HOLIDAYS: dict[str, str] = {
    # ---------------- 2025 ----------------
    "2025-01-01": "元旦",
    "2025-01-28": "春节", "2025-01-29": "春节", "2025-01-30": "春节",
    "2025-01-31": "春节", "2025-02-03": "春节", "2025-02-04": "春节",
    "2025-04-04": "清明节",
    "2025-05-01": "劳动节", "2025-05-02": "劳动节", "2025-05-05": "劳动节",
    "2025-06-02": "端午节",
    "2025-10-01": "国庆节", "2025-10-02": "国庆节", "2025-10-03": "国庆节",
    "2025-10-06": "国庆节", "2025-10-07": "国庆节", "2025-10-08": "国庆节",
    # ---------------- 2026 ----------------
    "2026-01-01": "元旦", "2026-01-02": "元旦",
    "2026-02-16": "春节", "2026-02-17": "春节", "2026-02-18": "春节",
    "2026-02-19": "春节", "2026-02-20": "春节", "2026-02-23": "春节",
    "2026-04-06": "清明节",
    "2026-05-01": "劳动节", "2026-05-04": "劳动节", "2026-05-05": "劳动节",
    "2026-06-19": "端午节",
    "2026-09-25": "中秋节",
    "2026-10-01": "国庆节", "2026-10-02": "国庆节", "2026-10-05": "国庆节",
    "2026-10-06": "国庆节", "2026-10-07": "国庆节",
}

# 已收录完整休市安排的年份
COVERED_YEARS: tuple[int, ...] = (2025, 2026)


def _as_date(d: date | datetime | str) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, str):
        return datetime.strptime(d[:10], "%Y-%m-%d").date()
    return d


def calendar_covers(d: date | datetime | str) -> bool:
    """查询日期所在年份是否已有官方休市安排。用于提示「日历需更新」。"""
    return _as_date(d).year in COVERED_YEARS


def holiday_name(d: date | datetime | str) -> str | None:
    """若是法定休市日则返回假期名（如「国庆节」），否则 None。"""
    return HOLIDAYS.get(_as_date(d).isoformat())


def is_trading_day(d: date | datetime | str) -> bool:
    """是否 A 股交易日（周末与法定节假日均返回 False）。"""
    dd = _as_date(d)
    if dd.weekday() >= 5:
        return False
    return dd.isoformat() not in HOLIDAYS


def next_trading_day(d: date | datetime | str) -> date:
    """严格的下一个交易日（不含当天）。"""
    dd = _as_date(d) + timedelta(days=1)
    while not is_trading_day(dd):
        dd += timedelta(days=1)
    return dd


def prev_trading_day(d: date | datetime | str) -> date:
    """严格的上一个交易日（不含当天）。"""
    dd = _as_date(d) - timedelta(days=1)
    while not is_trading_day(dd):
        dd -= timedelta(days=1)
    return dd


def last_trading_day_on_or_before(d: date | datetime | str) -> date:
    """最近的交易日（含当天）：当天是交易日则返回当天，否则往前找。"""
    dd = _as_date(d)
    while not is_trading_day(dd):
        dd -= timedelta(days=1)
    return dd


def trading_days(start: date | datetime | str, end: date | datetime | str) -> list[date]:
    """闭区间 [start, end] 内的所有交易日。"""
    a, b = _as_date(start), _as_date(end)
    out: list[date] = []
    cur = a
    while cur <= b:
        if is_trading_day(cur):
            out.append(cur)
        cur += timedelta(days=1)
    return out
