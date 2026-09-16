#!/usr/bin/env python3
"""量化分析系统的"保守程度"：大盘门控分布、每日候选数量、各过滤条件淘汰占比。

用途：判断"过于保守"是感受还是数据事实，并定位保守主要来自哪个环节。
用法：python scripts/analyze_conservatism.py
"""

from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import webapp  # noqa: E402
from daily_picker.config import Config  # noqa: E402


def main() -> int:
    cfg = Config()

    # 1) 大盘门控分布（决定多少天允许交易）
    try:
        env = webapp.build_env_signal(cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"大盘信号计算失败：{exc}")
        env = {}
    if env:
        dist = Counter(env.values())
        total = len(env)
        print("=== 1. 大盘门控分布（历史全部交易日） ===")
        for k, v in dist.most_common():
            print(f"  {k or '（无信号）'}: {v} 天（{v/total*100:.0f}%）")
        blocked = dist.get("不适合入场", 0) + dist.get("观望为主", 0)
        print(f"  → 处于「非适合入场」状态的天数：{blocked}/{total}（{blocked/total*100:.0f}%）")

    # 2) 每日候选数量（回放数据）
    files = sorted(f for f in glob.glob(os.path.join(BASE, "daily_picker", "cache", "replay", "*.json"))
                   if not f.endswith("index.json"))
    if files:
        counts = []
        zero_days = 0
        for f in files:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            n = len(d.get("priority", []))
            counts.append(n)
            if n == 0:
                zero_days += 1
        print(f"\n=== 2. 每日「优先观察」候选数（{len(files)} 个交易日） ===")
        print(f"  平均 {sum(counts)/len(counts):.1f} 只/天，最多 {max(counts)} 只，最少 {min(counts)} 只")
        print(f"  候选为 0 的天数：{zero_days}/{len(files)}（{zero_days/len(files)*100:.0f}%）")

    # 3) 各过滤条件淘汰占比（从 excluded 的原因统计）
    reason_counter: Counter = Counter()
    total_excluded = 0
    for f in files[-30:]:  # 最近30个交易日
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        for c in d.get("excluded", []):
            for r in c.get("reasons", []):
                reason_counter[r] += 1
            total_excluded += 1
    if reason_counter:
        print(f"\n=== 3. 被淘汰原因 Top10（最近30日，共 {total_excluded} 条淘汰记录） ===")
        for r, n in reason_counter.most_common(10):
            label = r.split("（")[0][:30]
            print(f"  {n:5d} 次  {label}")

    # 4) 排雷/财务/ATR 三项硬过滤的影响
    lr_path = os.path.join(BASE, "daily_picker", "cache", "last_result.json")
    try:
        with open(lr_path, encoding="utf-8") as fh:
            r = (json.load(fh).get("result") or {})
        rej = r.get("risk_rejected") or []
        print(f"\n=== 4. 最近一次选股的硬过滤（一票否决） ===")
        print(f"  被否决 {len(rej)} 只 / 进入候选 {len(r.get('priority', [])) + len(r.get('strong', []))} 只")
        for x in rej[:8]:
            print(f"    {x.get('code')} {x.get('name')}: {'；'.join(x.get('reasons', []))[:60]}")
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
