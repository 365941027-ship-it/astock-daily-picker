#!/usr/bin/env python3
"""对照实验：放松各项保守约束后，收益/回撤如何变化（全部按组合口径 + 扣成本）。

回答的问题："系统是不是太保守了？"——用"放松后到底赚更多还是亏更多"来判定。
"""

from __future__ import annotations

import glob
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from daily_picker.strategy import run_backtest, run_portfolio  # noqa: E402


def load_entries() -> list[dict]:
    out: list[dict] = []
    for f in sorted(glob.glob(os.path.join(BASE, "daily_picker", "cache", "verify", "*.json"))):
        if f.endswith("index.json"):
            continue
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        for e in d.get("entries", []):
            e.setdefault("date", d.get("date", ""))
            e.setdefault("checked_on", d.get("checked_on", ""))
            out.append(e)
    return out


def main() -> int:
    entries = load_entries()
    base = {"target_pct": 0.08, "skip_weak": True, "max_atr_pct": 6.0}

    variants = [
        ("L 现行（等回踩+弱市禁买+ATR6%）", {}),
        ("N 不等回踩（次日开盘买）", {"entry_mode": "open"}),
        ("O 不等回踩+不限弱市", {"entry_mode": "open", "skip_weak": False}),
        ("P 等回踩+不限弱市", {"skip_weak": False}),
        ("Q 全部放松（不等回踩+不限弱市+ATR10%）", {"entry_mode": "open", "skip_weak": False, "max_atr_pct": 10.0}),
    ]

    print(f"样本：{len(entries)} 条核对记录\n")
    print(f"{'方案':34s}{'交易':>5s}{'胜率':>8s}{'全仓收益':>10s}{'组合收益':>10s}{'组合回撤':>10s}")
    print("-" * 80)
    for label, kw in variants:
        params = dict(base)
        params.update(kw)
        r = run_backtest(entries, params=params)
        pf = run_portfolio(entries, params=params, max_positions=3, weight=1 / 3)
        print(f"{label:34s}{r['n_trades']:>5d}{r['win_rate']:>7.1f}%"
              f"{r['cum_return']:>9.2f}%{pf['cum_return']:>9.2f}%{pf['max_drawdown']:>9.2f}%")
    print("\n说明：全仓收益=逐笔全仓复利（乐观）；组合收益=最多3只等权分仓（接近实盘）；均扣0.35%往返成本。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
