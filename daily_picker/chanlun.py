"""缠论结构简化标注：分型 -> 笔 -> 最近中枢区间。

这是教学级简化实现（非严格缠论）：识别顶/底分型，由相邻分型构成笔，
取最近三笔的重叠区间作为中枢参考 [zg, zd]。
"""

from __future__ import annotations

from typing import Dict, List, Optional


def _fractals(bars: List[Dict]) -> List[Dict]:
    """识别分型：i-1 < i > i+1 为顶分型，i-1 > i < i+1 为底分型。"""
    out = []
    for i in range(1, len(bars) - 1):
        h_prev, h, h_next = bars[i - 1]["high"], bars[i]["high"], bars[i + 1]["high"]
        l_prev, l, l_next = bars[i - 1]["low"], bars[i]["low"], bars[i + 1]["low"]
        if h > h_prev and h > h_next:
            out.append({"i": i, "type": "top", "price": h, "date": bars[i]["date"]})
        elif l < l_prev and l < l_next:
            out.append({"i": i, "type": "bottom", "price": l, "date": bars[i]["date"]})
    return out


def _pens(fractals: List[Dict]) -> List[Dict]:
    """由交替分型构成笔（简化：不合并复杂形态）。"""
    pens = []
    for j in range(1, len(fractals)):
        a, b = fractals[j - 1], fractals[j]
        if a["type"] != b["type"]:
            pens.append({
                "start_i": a["i"], "end_i": b["i"],
                "start_price": a["price"], "end_price": b["price"],
                "direction": "up" if b["price"] > a["price"] else "down",
                "start_date": a["date"], "end_date": b["date"],
            })
    return pens


def zhongshu(pens: List[Dict]) -> Optional[Dict]:
    """从最近三笔开始向前滑窗，找第一组存在重叠区间的连续三笔作为中枢参考。"""
    if len(pens) < 3:
        return None
    for i in range(len(pens) - 3, -1, -1):
        last3 = pens[i : i + 3]
        lows = [min(p["start_price"], p["end_price"]) for p in last3]
        highs = [max(p["start_price"], p["end_price"]) for p in last3]
        zd = max(lows)
        zg = min(highs)
        if zg > zd:
            return {
                "zg": round(zg, 2),
                "zd": round(zd, 2),
                "start_date": last3[0]["start_date"],
                "end_date": last3[-1]["end_date"],
                "pens": len(last3),
            }
    return None


def annotate(bars: List[Dict], lookback: int = 90) -> Dict:
    """返回缠论标注：最近一根K线位置、分型、笔、中枢。"""
    tail = bars[-lookback:] if len(bars) > lookback else bars
    fractals = _fractals(tail)
    pens = _pens(fractals)
    zs = zhongshu(pens)
    # 供前端画线：最近中枢区间水平线
    levels = []
    if zs:
        levels = [
            {"price": zs["zg"], "type": "zg"},
            {"price": zs["zd"], "type": "zd"},
        ]
    return {
        "fractals": [
            {"i": f["i"], "type": f["type"], "price": f["price"]}
            for f in fractals[-12:]
        ],
        "pens": [
            {"start_i": p["start_i"], "end_i": p["end_i"],
             "start_price": p["start_price"], "end_price": p["end_price"],
             "direction": p["direction"]}
            for p in pens[-8:]
        ],
        "zhongshu": zs,
        "levels": levels,
    }
