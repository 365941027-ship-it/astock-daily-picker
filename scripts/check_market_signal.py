#!/usr/bin/env python3
"""核查大盘研判：打印三大指数近期收盘与均线，验证趋势/中枢判定的合理性。

用法：python scripts/check_market_signal.py
"""

from __future__ import annotations

import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from daily_picker.config import Config  # noqa: E402
from daily_picker.market_signal import INDEX_INFO, analyze_index  # noqa: E402
from daily_picker.chanlun import annotate as chanlun_annotate  # noqa: E402
from daily_picker.data_fetch import _fetch_kline_sina_symbol  # noqa: E402


def main() -> int:
    cfg = Config()
    for name, info in INDEX_INFO.items():
        try:
            bars = _fetch_kline_sina_symbol(info["symbol"], cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"{name}: 取数失败 {exc}")
            continue
        if not bars:
            print(f"{name}: 无数据")
            continue
        closes = [b["close"] for b in bars]
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10
        ma20 = sum(closes[-20:]) / 20
        chan = chanlun_annotate(bars, lookback=120)
        zs = chan.get("zhongshu") or {}
        last = bars[-1]
        prev = bars[-2]["close"]
        pct = (last["close"] / prev - 1) * 100
        print(f"\n【{name}】最新 {last['date']} 收盘 {last['close']:.2f} ({pct:+.2f}%)")
        print(f"  MA5={ma5:.2f}  MA10={ma10:.2f}  MA20={ma20:.2f}")
        trend = "多头排列(MA5>MA10>MA20)" if ma5 > ma10 > ma20 else (
            "空头排列(MA5<MA10<MA20)" if ma5 < ma10 < ma20 else "均线纠缠")
        print(f"  均线状态：{trend}")
        if zs:
            pos = "中枢上方" if last["close"] > zs["zg"] else (
                "中枢下方" if last["close"] < zs["zd"] else "中枢内部")
            print(f"  缠论中枢：ZG={zs['zg']:.2f} ZD={zs['zd']:.2f}（{zs.get('pens', '?')}笔）→ 收盘位于{pos}")
        print(f"  近10日收盘：{' '.join(f'{c:.0f}' for c in closes[-10:])}")
        r = analyze_index(name, cfg)
        if r:
            print(f"  研判：位置={r['position']} 趋势={r['trend']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
