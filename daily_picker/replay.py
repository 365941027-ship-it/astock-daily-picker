"""历史回放：对指定日期区间内的每个交易日，用历史日K逐日执行选股。"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from .config import Config
from .indicators import kdj_series, macd_series, pct_change
from .screening import (
    Candidate,
    build_note,
    categorize,
    evaluate,
    score,
    structure_label,
)


def _rolling_ma(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i - period]
        if i + 1 >= period:
            out[i] = s / period
    return out


def analyze_days(bars: List[Dict], cfg: Config) -> Dict[str, Dict]:
    """把一只股票的全部日K一次性算成 {date: 指标字典}，供逐日回放查表。"""
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    ma5 = _rolling_ma(closes, 5)
    ma10 = _rolling_ma(closes, 10)
    ma20 = _rolling_ma(closes, 20)
    macd = macd_series(closes)
    kdj = kdj_series(highs, lows, closes)
    out: Dict[str, Dict] = {}
    for i, b in enumerate(bars):
        d = b["date"]
        # 新浪等源不提供涨幅/成交额/换手率，这里自行重算/估算：
        prev_close = bars[i - 1]["close"] if i >= 1 else None
        pct_calc = (b["close"] / prev_close - 1.0) * 100.0 if prev_close else 0.0
        est_amount = b["volume"] * b["close"]  # 新浪 volume 单位为股
        is_em = b.get("source") == "eastmoney"
        ind = {
            "date": d,
            "open": b["open"],
            "high": b["high"],
            "low": b["low"],
            "volume": b["volume"],
            "close": b["close"],
            "pct_chg": b["pct_chg"] if is_em and b["pct_chg"] else pct_calc,
            "amount": b["amount"] if is_em and b["amount"] else est_amount,
            "turnover": b["turnover"] if is_em and b["turnover"] else None,
            "est_amount": est_amount,
            "has_turnover": is_em,
            "bars": i + 1,
            "ma5": ma5[i],
            "ma10": ma10[i],
            "ma20": ma20[i],
            "gain_5d": pct_change(closes, 5, i),
            "gain_10d": pct_change(closes, 10, i),
        }
        if macd:
            ind.update({
                "dif": macd["dif"][i],
                "dea": macd["dea"][i],
                "hist": macd["hist"][i],
                "hist_prev": macd["hist"][i - 1] if i >= 1 else None,
                "dif_prev": macd["dif"][i - 1] if i >= 1 else None,
                "dea_prev": macd["dea"][i - 1] if i >= 1 else None,
            })
        if kdj:
            ind.update({
                "k": kdj["k"][i],
                "d": kdj["d"][i],
                "j": kdj["j"][i],
                "k_prev": kdj["k"][i - 1] if i >= 1 else None,
                "d_prev": kdj["d"][i - 1] if i >= 1 else None,
            })
        out[d] = ind
    return out


def run_replay(
    kline_map: Dict[str, List[Dict]],
    name_map: Dict[str, str],
    cfg: Config,
    start_date: str,
    end_date: str,
    progress: Optional[Callable[[int, int], None]] = None,
    env_signal: Optional[Dict[str, str]] = None,
) -> tuple[List[str], Dict[str, Dict]]:
    """对 [start_date, end_date] 区间内每个交易日执行选股。"""
    per_stock: Dict[str, Dict[str, Dict]] = {}
    for code, bars in kline_map.items():
        try:
            days = analyze_days(bars, cfg)
            if days:
                per_stock[code] = days
        except Exception:  # noqa: BLE001 - 单只失败不影响整体
            continue

    all_dates = sorted({d for days in per_stock.values() for d in days})
    dates = [d for d in all_dates if start_date <= d <= end_date]
    results: Dict[str, Dict] = {}
    env_signal = env_signal or {}

    for di, date in enumerate(dates):
        market_verdict = env_signal.get(date, "")
        rows = []
        inds = {}
        for code, days in per_stock.items():
            ind = days.get(date)
            if not ind or ind["bars"] < cfg.min_list_days:
                continue
            rows.append({
                "f12": code,
                "f14": name_map.get(code, code),
                "f2": ind["close"],
                "f3": ind["pct_chg"],
                "f6": ind["amount"],
                "f8": ind["turnover"],
            })
            inds[code] = ind

        priority: List[Candidate] = []
        strong: List[Candidate] = []
        excluded: List[Candidate] = []
        for row in rows:
            code = row["f12"]
            cand = Candidate(code=code, name=row["f14"], snapshot=row, ind=inds[code])
            ok, reasons = evaluate(cand, cfg)
            if not ok:
                cand.reasons = reasons
                excluded.append(cand)
                continue
            cand.score = score(cand, cfg, market_verdict)
            cand.category = categorize(cand, cfg)
            cand.structure = structure_label(cand, cfg)
            cand.note = build_note(cand, cfg)
            if cand.category == "强势但不宜追高":
                strong.append(cand)
            else:
                priority.append(cand)

        priority.sort(key=lambda c: c.score, reverse=True)
        # P0 大盘门控：弱市候选减半 / 观望压缩
        if market_verdict == "不适合入场":
            priority = priority[: max(1, cfg.top_n // 2)]
        elif market_verdict == "观望为主":
            priority = priority[: max(1, int(cfg.top_n * 0.75))]
        strong.sort(key=lambda c: c.pct_chg or 0, reverse=True)
        excluded.sort(key=lambda c: c.amount or 0, reverse=True)
        results[date] = {
            "date": date,
            "pool": len(rows),
            "priority": priority[: cfg.top_n],
            "strong": strong[: cfg.top_n],
            "excluded": excluded[: 10],
        }
        if progress:
            progress(di + 1, len(dates))

    return dates, results
