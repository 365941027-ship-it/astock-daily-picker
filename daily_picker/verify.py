"""预判核对：把前一交易日选出的候选，与次日实际走势逐条对比。

核对的三个核心问题：
1. 观察要点里给的支撑位（MA5/MA10）次日有没有被跌破；
2. 次日是“回踩不破重新拉起”还是“高开低走/放量下跌”；
3. 依据次日表现修正后续观察：兑现的继续跟踪，失效的剔除/降级。
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from .config import Config
from .replay import analyze_days, run_replay


def _support_of(ind: Dict) -> Optional[float]:
    """支撑位：MA5 与观察日最低价中较高者（更贴近真实短期承接位）。

    若当日最低价高于 MA5，说明 MA5 之下无有效回踩，取当日最低价更贴合实际承接；
    若最低价低于 MA5，说明当天已测试过 MA5，取 MA5 作为参考。
    """
    ma5 = ind.get("ma5")
    low = ind.get("low")
    vals = [v for v in (ma5, low) if v is not None]
    return max(vals) if vals else None


def judge(ind: Dict, nd: Dict) -> tuple[str, str]:
    """按用户口径判定次日走势，返回 (verdict, 说明)。

    核心：观察日给出的支撑位（MA5/MA10 较低者）+ 相对观察日收盘的涨跌方向。
    - 跌破支撑位 → 观察成功 (valid)：视为回踩到位/支撑被测试
    - 未跌破支撑 且 较观察日上涨 → 观察成功 (valid)
    - 未跌破支撑 但 较观察日下跌 → 观察失败 (failed)
    - 无支撑参考时，按涨跌方向判断（涨=成功，跌=失败）
    """
    support = _support_of(ind)
    prev_close = ind["close"]
    low = nd["low"]
    close = nd["close"]
    pct = nd.get("pct_chg")
    msgs: List[str] = []

    broke = support is not None and low <= support
    rising = close > prev_close

    if support is None:
        # 无支撑参考（如上市时间短），退化为涨跌判断
        if rising:
            verdict = "valid"
            msgs.append("无支撑参考，较观察日上涨")
        else:
            verdict = "failed"
            msgs.append("无支撑参考，较观察日下跌")
    elif broke:
        verdict = "valid"
        msgs.append(f"跌破支撑{support:.2f}")
        if rising:
            msgs.append(f"较观察日上涨{pct:+.1f}%")
        else:
            msgs.append(f"较观察日下跌{pct:+.1f}%")
    elif not rising:
        verdict = "failed"
        msgs.append(f"未跌破支撑{support:.2f}但较观察日下跌{pct:+.1f}%")
    else:
        verdict = "valid"
        msgs.append(f"未跌破支撑{support:.2f}且较观察日上涨{pct:+.1f}%")

    return verdict, "；".join(msgs)


VERDICT_TEXT = {
    "valid": "观察成功：跌破支撑位或未跌破且上涨，可继续跟踪",
    "failed": "观察失败：未跌破支撑且较观察日下跌，从观察池剔除",
    "strong": "未回踩直接走强，注意不追高",
    "weak": "未回踩且走弱，观察降级",
    "neutral": "走势不明，降级观察",
}


def group_of(verdict: str, rising: bool) -> str:
    """分组：
    red   = 观察成功 + 上涨
    yellow= 观察成功 + 下跌
    green = 观察失败 + 下跌
    （观察失败 + 上涨 在逻辑上不会出现）
    """
    if verdict == "valid":
        return "red" if rising else "yellow"
    return "green" if not rising else "gray"


def run_verify(
    kline_map: Dict[str, List[Dict]],
    name_map: Dict[str, str],
    cfg: Config,
    start_date: str,
    end_date: str,
    progress: Optional[Callable[[int, int], None]] = None,
    env_signal: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict]:
    """对区间内每个交易日 T，核对 T 日候选在 T+1 日的实际走势。"""
    dates, cands = run_replay(kline_map, name_map, cfg, start_date, end_date)
    per_stock: Dict[str, Dict[str, Dict]] = {}
    for code, bars in kline_map.items():
        try:
            days = analyze_days(bars, cfg)
            if days:
                per_stock[code] = days
        except Exception:  # noqa: BLE001
            continue

    out: Dict[str, Dict] = {}
    total = max(0, len(dates) - 1)
    env_signal = env_signal or {}
    for i in range(len(dates) - 1):
        t, n = dates[i], dates[i + 1]
        day = cands.get(t)
        entries: List[Dict] = []
        env_weak = env_signal.get(n) == "不适合入场"
        if day:
            for c in day["priority"] + day["strong"]:
                nd = per_stock.get(c.code, {}).get(n)
                if not nd:
                    continue
                if env_weak:
                    # P2：大盘弱势日“只观察不下结论”，不参与成败统计
                    entries.append({
                        "code": c.code,
                        "name": c.name,
                        "category": c.category,
                        "score": round(c.score, 1),
                        "prev_close": c.ind["close"],
                        "prev_pct": c.ind["pct_chg"],
                        "support": round(support, 2) if (support := _support_of(c.ind)) else None,
                        "next_open": nd["open"],
                        "next_high": nd["high"],
                        "next_low": nd["low"],
                        "next_close": nd["close"],
                        "next_pct": nd.get("pct_chg"),
                        "verdict": "observe",
                        "rising": nd["close"] > c.ind["close"],
                        "group": "observe",
                        "env_weak": True,
                        "detail": "大盘弱势日，仅观察不下结论",
                        "suggestion": "环境弱：仅跟踪，不参与成败统计",
                    })
                    continue
                verdict, detail = judge(c.ind, nd)
                support = _support_of(c.ind)
                rising = nd["close"] > c.ind["close"]
                entries.append({
                    "code": c.code,
                    "name": c.name,
                    "category": c.category,
                    "score": round(c.score, 1),
                    "prev_close": c.ind["close"],
                    "prev_pct": c.ind["pct_chg"],
                    "support": round(support, 2) if support else None,
                    "next_open": nd["open"],
                    "next_high": nd["high"],
                    "next_low": nd["low"],
                    "next_close": nd["close"],
                    "next_pct": nd.get("pct_chg"),
                    "verdict": verdict,
                    "rising": rising,
                    "group": group_of(verdict, rising),
                    "env_weak": False,
                    "detail": detail,
                    "suggestion": VERDICT_TEXT.get(verdict, ""),
                })
        # 排序：红(成功+涨) -> 黄(成功+跌) -> 绿(失败+跌)，组内按涨跌幅降序
        order = {"red": 0, "yellow": 1, "green": 2, "gray": 3}
        entries.sort(key=lambda e: (order.get(e["group"], 3), -(e["next_pct"] or 0)))
        scored = [e for e in entries if e["group"] != "observe"]
        total_n = len(scored)
        red = sum(1 for e in scored if e["group"] == "red")
        yellow = sum(1 for e in scored if e["group"] == "yellow")
        green = sum(1 for e in scored if e["group"] == "green")
        weak_count = len(entries) - total_n
        out[n] = {
            "date": t,
            "checked_on": n,
            "total": total_n,
            "valid": red + yellow,
            "failed": green,
            "red": red,
            "yellow": yellow,
            "green": green,
            "weak": weak_count,
            "env_weak": env_weak,
            "rates": {
                "up_success": round(red / total_n * 100, 1) if total_n else 0.0,
                "down_success": round(yellow / total_n * 100, 1) if total_n else 0.0,
                "down_failed": round(green / total_n * 100, 1) if total_n else 0.0,
            },
            "entries": entries,
        }
        if progress:
            progress(i + 1, total)
    return out
