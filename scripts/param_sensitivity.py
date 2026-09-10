#!/usr/bin/env python3
"""参数敏感性测试：在同一批历史信号上跑一组参数，观察结果稳定性。

用途：判断推荐参数是"真实优势"还是"过拟合的偶然"。若某参数在邻近取值上表现
剧烈波动，说明该参数不可靠；若一片区域都表现良好，则该方向较稳健。

用法（在容器/服务器内）：
    python scripts/param_sensitivity.py
"""

from __future__ import annotations

import glob
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from daily_picker.strategy import run_backtest  # noqa: E402


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
    base = {"target_pct": 0.08, "skip_weak": True, "max_atr_pct": 6.0}

    def run(label: str, **kw) -> None:
        p = dict(base)
        p.update(kw)
        r = run_backtest(entries, params=p)
        print(f"{label:22s} {r['n_trades']:3d}笔 胜率{r['win_rate']:5.1f}% "
              f"收益{r['cum_return']:8.2f}% 回撤{r['max_drawdown']:7.2f}% 盈亏比{r['profit_factor']}")

    print(f"样本核对记录：{len(entries)} 条\n")
    print("== 基准 ==")
    run("L 基准(止盈8/持5)")
    print("\n== 止盈敏感度 ==")
    for t in (0.05, 0.06, 0.07, 0.10, 0.12):
        run(f"止盈{int(t*100)}%", target_pct=t)
    print("\n== 止损敏感度 ==")
    for s in (0.04, 0.05, 0.06, 0.08, 0.09):
        run(f"止损{int(s*100)}%", stop_pct=s)
    print("\n== 持有天数敏感度 ==")
    for h in (2, 3, 4, 6, 8):
        run(f"持有{h}天", hold_days=h)
    print("\n== ATR 阈值敏感度 ==")
    for a in (4.0, 5.0, 7.0, 8.0, 10.0):
        run(f"ATR上限{a}%", max_atr_pct=a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
