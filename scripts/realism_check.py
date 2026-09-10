#!/usr/bin/env python3
"""回测真实性检查：找出回测中"假设过于乐观"的地方。

检查项：
1. 跳空穿越止损：次日开盘已低于止损价，却仍按止损价成交（实际成交更差）
2. 到期卖出占比与盈亏贡献（拖后腿的持有损耗）
3. 同日多笔信号（实盘需分仓，全仓复利不可行）
"""

from __future__ import annotations

import glob
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from daily_picker.strategy import run_backtest, load_kline_bars  # noqa: E402


def load_entries() -> list[dict]:
    entries: list[dict] = []
    for f in sorted(glob.glob(os.path.join(BASE, "daily_picker", "cache", "verify", "*.json"))):
        if f.endswith("index.json"):
            continue
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        for e in d.get("entries", []):
            e.setdefault("date", d.get("date", ""))
            e.setdefault("checked_on", d.get("checked_on", ""))
            entries.append(e)
    return entries


def main() -> int:
    entries = load_entries()
    params = {"target_pct": 0.08, "skip_weak": True, "max_atr_pct": 6.0}
    r = run_backtest(entries, params=params)
    trades = r["trades"]

    print(f"总交易 {len(trades)} 笔\n")

    # 1) 跳空穿越止损检查
    gap_through = []
    for t in trades:
        if t["reason"] != "止损":
            continue
        bars = load_kline_bars(t["code"])
        bd = {b["date"]: b for b in bars}
        bar = bd.get(t["exit_date"])
        if not bar:
            continue
        stop = t["stop"]
        if bar["open"] < stop:
            worsened = (bar["open"] / stop - 1) * 100
            gap_through.append((t["name"], t["exit_date"], stop, bar["open"], worsened))
    print("== 1. 跳空穿越止损（回测按止损价成交，实际应以开盘价成交） ==")
    if gap_through:
        for name, d, stop, op, w in gap_through:
            print(f"  {name} {d}: 止损价{stop:.2f} 实际开盘{op:.2f} → 额外亏损约 {w:.1f}%")
        print(f"  共 {len(gap_through)} 笔存在乐观偏差")
    else:
        print("  未发现（本次样本内止损均在盘中触发）")

    # 2) 到期卖出的损耗
    exp = [t for t in trades if t["reason"] == "到期卖出"]
    if exp:
        s = sum(t["pnl_pct"] for t in exp)
        print(f"\n== 2. 到期卖出拖累 ==")
        print(f"  {len(exp)}/{len(trades)} 笔到期离场，合计 {s:+.1f}%"
              f"（平均 {s/len(exp):+.2f}%），占全部亏损的相当比例")

    # 3) 同日并发信号
    from collections import Counter
    by_day = Counter(t["entry_date"] for t in trades)
    multi = {d: c for d, c in by_day.items() if c > 1}
    print(f"\n== 3. 同日并发信号（实盘需分仓） ==")
    if multi:
        for d, c in sorted(multi.items())[:8]:
            print(f"  {d}: 同日 {c} 笔（全仓复利假设不成立）")
        print(f"  共 {len(multi)} 个交易日出现并发信号")
    else:
        print("  无")
    return 0


if __name__ == "__main__":
    sys.exit(main())
