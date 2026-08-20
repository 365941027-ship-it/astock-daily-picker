"""技术指标计算（纯 Python 标准库实现，无第三方依赖）。"""

from __future__ import annotations

from typing import Dict, List, Optional

from .config import Config


def ema_series(values: List[float], period: int) -> List[float]:
    k = 2.0 / (period + 1.0)
    out: List[float] = []
    prev: Optional[float] = None
    for v in values:
        prev = v if prev is None else v * k + prev * (1.0 - k)
        out.append(prev)
    return out


def ma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def macd_series(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9):
    if len(closes) < slow + signal:
        return None
    ef = ema_series(closes, fast)
    es = ema_series(closes, slow)
    dif = [a - b for a, b in zip(ef, es)]
    dea = ema_series(dif, signal)
    hist = [(a - b) * 2.0 for a, b in zip(dif, dea)]
    return {"dif": dif, "dea": dea, "hist": hist}


def kdj_series(highs: List[float], lows: List[float], closes: List[float], n: int = 9):
    if len(closes) < n:
        return None
    k_vals: List[float] = []
    d_vals: List[float] = []
    k = d = 50.0
    for i in range(len(closes)):
        lo = max(0, i - n + 1)
        hh = max(highs[lo : i + 1])
        ll = min(lows[lo : i + 1])
        rsv = 50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100.0
        k = (2.0 * k + rsv) / 3.0
        d = (2.0 * d + k) / 3.0
        k_vals.append(k)
        d_vals.append(d)
    j_vals = [3.0 * kk - 2.0 * dd for kk, dd in zip(k_vals, d_vals)]
    return {"k": k_vals, "d": d_vals, "j": j_vals}


def pct_change(closes: List[float], n: int, i: Optional[int] = None) -> Optional[float]:
    """计算 closes[i] 相对 i-n 日的涨幅百分比；i 默认取最后一根。"""
    if i is None:
        i = len(closes) - 1
    if i < n or closes[i - n] == 0:
        return None
    return (closes[i] / closes[i - n] - 1.0) * 100.0


def analyze_bars(bars: List[Dict], cfg: Config) -> Optional[Dict]:
    """从日K列表计算指标快照，返回最后一根K线对应的指标字典。"""
    if not bars:
        return None
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    last = bars[-1]
    n = len(bars)

    out = {
        "date": last["date"],
        "close": last["close"],
        "pct_chg": last["pct_chg"],
        "amount": last["amount"],
        "turnover": last["turnover"],
        "bars": n,
        "ma5": ma(closes, 5),
        "ma10": ma(closes, 10),
        "ma20": ma(closes, 20),
        "gain_5d": pct_change(closes, 5),
        "gain_10d": pct_change(closes, 10),
        "prev_low": bars[-2]["low"] if n >= 2 else None,
        "prev_close": bars[-2]["close"] if n >= 2 else None,
    }
    macd = macd_series(closes)
    if macd:
        out.update({
            "dif": macd["dif"][-1], "dea": macd["dea"][-1],
            "hist": macd["hist"][-1], "hist_prev": macd["hist"][-2],
            "dif_prev": macd["dif"][-2], "dea_prev": macd["dea"][-2],
        })
    kdj = kdj_series(highs, lows, closes)
    if kdj:
        out.update({
            "k": kdj["k"][-1], "d": kdj["d"][-1], "j": kdj["j"][-1],
            "k_prev": kdj["k"][-2], "d_prev": kdj["d"][-2],
        })
    return out
