#!/usr/bin/env python3
"""诊断脚本：对比"选股页(last_result.json)"与"盘中盯盘(replay最新日)"两个数据源。

用途：说明两者口径差异（来源文件、弱市门控、排雷过滤、字段用途）。
"""

from __future__ import annotations

import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE, "daily_picker", "cache")


def main() -> int:
    lr_path = os.path.join(CACHE, "last_result.json")
    print("=== A. 选股页数据源：last_result.json（run_pipeline 产出） ===")
    try:
        with open(lr_path, encoding="utf-8") as f:
            lr = json.load(f).get("result") or {}
        print(f"  data_date={lr.get('data_date')} observe_date={lr.get('observe_date')} 大盘={lr.get('market_verdict')}")
        for k in ("priority", "strong", "excluded", "risk_rejected"):
            v = lr.get(k) or []
            print(f"  {k}: {len(v)} 只 {[c.get('code') for c in v]}")
        print(f"  channel={lr.get('channel')}  files={list((lr.get('files') or {}).keys())}")
    except Exception as exc:  # noqa: BLE001
        print(f"  （读取失败：{exc}）")

    print()
    print("=== B. 盯盘系统推荐数据源：replay 最新交易日 ===")
    try:
        with open(os.path.join(CACHE, "replay", "index.json"), encoding="utf-8") as f:
            idx = json.load(f)
        last = (idx.get("days") or [])[-1]
        with open(os.path.join(CACHE, "replay", f"{last}.json"), encoding="utf-8") as f:
            rep = json.load(f)
        print(f"  最新回放日={last}  pool={rep.get('pool')}")
        for k in ("priority", "strong", "excluded"):
            v = rep.get(k) or []
            print(f"  {k}: {len(v)} 只 {[c.get('code') for c in v]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  （读取失败：{exc}）")

    print()
    print("=== C. 盯盘快照实际展示（含自选合并与实时状态） ===")
    try:
        with open(os.path.join(CACHE, "intraday_status.json"), encoding="utf-8") as f:
            st = json.load(f)
        items = st.get("items") or []
        sys_codes = [i["code"] for i in items if i.get("source") == "system"]
        watch_codes = [i["code"] for i in items if i.get("source") == "watch"]
        print(f"  generated_at={st.get('generated_at')} 大盘={st.get('market_verdict')}")
        print(f"  系统候选 {len(sys_codes)} 只：{sys_codes}")
        print(f"  用户自选 {len(watch_codes)} 只：{watch_codes}")
        print(f"  排雷剔除 {len(st.get('rejected') or [])} 只")
    except Exception as exc:  # noqa: BLE001
        print(f"  （读取失败：{exc}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
