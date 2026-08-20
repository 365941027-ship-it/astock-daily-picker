"""缠论大盘研判 + 观察行业筛选 + 入场建议。

大盘：用三大指数日K计算缠论中枢，结合均线与位置给出当日是否适合入场。
行业：基于板块热度分档筛选 3-5 个观察行业。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .chanlun import annotate as chanlun_annotate
from .config import Config
from .data_fetch import fetch_kline, fetch_sectors


INDEX_INFO = {
    "上证指数": {"code": "000001", "symbol": "sh000001"},
    "深证成指": {"code": "399001", "symbol": "sz399001"},
    "创业板指": {"code": "399006", "symbol": "sz399006"},
}


def _index_bars(name: str, cfg: Config) -> List[Dict]:
    info = INDEX_INFO[name]
    try:
        # 新浪指数K线（指数无成交额也够用）
        from .data_fetch import _fetch_kline_sina_symbol

        return _fetch_kline_sina_symbol(info["symbol"], cfg)
    except Exception:
        return []


def _ma(values: List[float], n: int) -> Optional[float]:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def analyze_index(name: str, cfg: Config) -> Optional[Dict]:
    """对单个指数做缠论研判。"""
    bars = _index_bars(name, cfg)
    if len(bars) < 40:
        return None
    chan = chanlun_annotate(bars, lookback=120)
    last = bars[-1]
    closes = [b["close"] for b in bars]
    ma5 = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)
    prev_close = closes[-2] if len(closes) >= 2 else last["close"]
    pct = (last["close"] / prev_close - 1.0) * 100.0 if prev_close else 0.0
    zs = chan.get("zhongshu")

    position = "unknown"
    suggestion = ""
    if zs:
        close = last["close"]
        if close > zs["zg"]:
            position = "中枢上方"
            suggestion = "强势：回踩不破可考虑入场，追高需谨慎"
        elif close < zs["zd"]:
            position = "中枢下方"
            suggestion = "弱势：等待站稳中枢下沿再考虑，不急于入场"
        else:
            position = "中枢内部"
            suggestion = "震荡：区间操作或观望，等方向选择"
    # 均线修正
    trend = "多头" if (ma5 and ma10 and ma20 and ma5 > ma10 > ma20) else (
        "空头" if (ma5 and ma10 and ma20 and ma5 < ma10 < ma20) else "纠缠"
    )
    if position == "中枢上方" and trend == "多头":
        suggestion = "多头+中枢上方：环境适合，优选强势回踩品种"
    elif position == "中枢下方" and trend == "空头":
        suggestion = "空头+中枢下方：环境差，空仓或仅做超跌反弹观察"
    return {
        "name": name,
        "date": last["date"],
        "close": round(last["close"], 2),
        "pct_chg": round(pct, 2),
        "ma5": round(ma5, 2) if ma5 else None,
        "ma10": round(ma10, 2) if ma10 else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "trend": trend,
        "zhongshu": zs,
        "position": position,
        "suggestion": suggestion,
        "fractals": chan.get("fractals", []),
        "pens": chan.get("pens", []),
    }


def _verdict_from_positions(results: List[Dict]) -> str:
    """与 market_signal 相同的综合口径。"""
    strong = sum(1 for r in results if r.get("position") == "中枢上方")
    weak = sum(1 for r in results if r.get("position") == "中枢下方")
    if strong >= 2 and weak == 0:
        return "适合入场"
    if weak >= 2:
        return "不适合入场"
    return "观望为主"


def index_signal_series(
    bars_by_index: Dict[str, List[Dict]],
    dates: List[str],
) -> Dict[str, str]:
    """对每个历史日期，用截至当日的指数K线计算大盘环境信号（用于回测/核对）。"""
    out: Dict[str, str] = {}
    for d in dates:
        results = []
        for name, bars in bars_by_index.items():
            hist = [b for b in bars if b["date"] <= d]
            if len(hist) < 40:
                continue
            chan = chanlun_annotate(hist, lookback=120)
            closes = [b["close"] for b in hist]
            ma5 = _ma(closes, 5)
            ma10 = _ma(closes, 10)
            ma20 = _ma(closes, 20)
            zs = chan.get("zhongshu")
            position = "unknown"
            if zs:
                close = hist[-1]["close"]
                if close > zs["zg"]:
                    position = "中枢上方"
                elif close < zs["zd"]:
                    position = "中枢下方"
                else:
                    position = "中枢内部"
            trend = "多头" if (ma5 and ma10 and ma20 and ma5 > ma10 > ma20) else (
                "空头" if (ma5 and ma10 and ma20 and ma5 < ma10 < ma20) else "纠缠"
            )
            results.append({"position": position, "trend": trend})
        if results:
            out[d] = _verdict_from_positions(results)
    return out


def market_signal(cfg: Config) -> Dict:
    """综合三大指数给出当日入场建议。"""
    results = []
    for name in INDEX_INFO:
        r = analyze_index(name, cfg)
        if r:
            results.append(r)
    if not results:
        return {"ok": False, "error": "指数数据不可用"}

    strong = sum(1 for r in results if r["position"] == "中枢上方")
    weak = sum(1 for r in results if r["position"] == "中枢下方")
    if strong >= 2 and weak == 0:
        verdict = "适合入场"
        advice = "大盘结构偏强，可优选回踩不破的强势品种，注意不追高。"
    elif weak >= 2:
        verdict = "不适合入场"
        advice = "多数指数位于中枢下方，环境偏弱，建议空仓观察或仅做超跌反弹。"
    else:
        verdict = "观望为主"
        advice = "指数结构分化/震荡，轻仓试错，等方向明确。"
    return {
        "ok": True,
        "date": results[0]["date"],
        "verdict": verdict,
        "advice": advice,
        "indices": results,
    }


def watch_sectors(cfg: Config, top_n: int = 30, pick: int = 5) -> List[Dict]:
    """从板块热度中筛选 3-5 个观察行业（涨幅适中、非极端过热）。"""
    rows = fetch_sectors(cfg, top_n)
    # 观察行业偏好：涨幅 0.5% ~ 4% 之间（太弱不看，太热不追）
    candidates = [r for r in rows if r.get("pct_chg") is not None and 0.5 <= r["pct_chg"] <= 4.0]
    if len(candidates) < pick:
        candidates = [r for r in rows if r.get("pct_chg") is not None and r["pct_chg"] >= 0]
    selected = candidates[:pick]
    out = []
    for i, r in enumerate(selected, 1):
        pct = r.get("pct_chg") or 0
        reason = "涨幅适中、非过热" if 0.5 <= pct <= 4 else ("领涨但偏热，等回踩" if pct > 4 else "弱转强观察")
        out.append({
            "rank": i,
            "name": r.get("name"),
            "pct_chg": round(pct, 2),
            "leader": r.get("leader") or "",
            "reason": reason,
        })
    return out
