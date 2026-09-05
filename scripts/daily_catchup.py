#!/usr/bin/env python3
"""开机/登录自动补跑：检查历史回放是否缺最近交易日，缺则逐日补齐。

原理：
- 先算出“最近一个已收盘的交易日”：
    * 今天若为交易日且当前时间早于 18:00，则补到昨天（今日数据由 18:05 正常任务处理）；
    * 今天若已过 18:00 或今天不是交易日（周末回退到周五），则以 resolve_data_date 为准。
- 读取历史回放索引的最后一天；若落后于目标日，就逐日运行 daily_update.py。
- 当天 18:05 的正常任务仍由 com.ashare.daily-update 负责；本脚本只是兜底补漏。

用法：
    python scripts/daily_catchup.py [--proxy socks5h://127.0.0.1:7897] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, time as dtime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PY = "/Users/yexiyan/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
LOCK = "/tmp/astock_daily_catchup.lock"


CN_TZ = timezone(timedelta(hours=8))


def _now() -> datetime:
    return datetime.now(CN_TZ)


def _prev_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _next_weekday(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def latest_completed_trading_day() -> str:
    today = _now().date()
    # 周末回退到周五
    latest = today
    while latest.weekday() >= 5:
        latest -= timedelta(days=1)
    # 今天若是交易日但还没到 18:00 盘后，则只补到上一交易日
    if latest == today and latest.weekday() < 5 and _now().time() < dtime(18, 0):
        latest = _prev_weekday(latest)
    return latest.isoformat()


def replay_last_day() -> str | None:
    idx_path = os.path.join(BASE, "daily_picker", "cache", "replay", "index.json")
    try:
        with open(idx_path, encoding="utf-8") as f:
            days = json.load(f).get("days") or []
        return days[-1] if days else None
    except Exception:
        return None


def missing_days(last_day: str | None, target: str) -> list[str]:
    out: list[str] = []
    d = date.fromisoformat(last_day) if last_day else date.fromisoformat("2026-06-30")
    end = date.fromisoformat(target)
    while d < end:
        d = _next_weekday(d)
        if d > end:
            break
        out.append(d.isoformat())
    return out


def _lock_held() -> bool:
    try:
        if not os.path.exists(LOCK):
            return False
        with open(LOCK, encoding="utf-8") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        try:
            os.remove(LOCK)
        except Exception:
            pass
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="缺失交易日自动补跑")
    parser.add_argument("--proxy", default="", help="行情代理")
    parser.add_argument("--dry-run", action="store_true", help="只打印需要补跑的日期，不实际执行")
    args = parser.parse_args()

    target = latest_completed_trading_day()
    last = replay_last_day()
    days = missing_days(last, target)
    if not days:
        print(f"[补跑] 无需补跑：历史回放已更新到 {last or '—'}，目标 {target}", flush=True)
        return 0

    print(f"[补跑] 最近已收盘交易日：{target}；历史回放最后：{last or '—'}", flush=True)
    print(f"[补跑] 缺失 {len(days)} 个交易日：{', '.join(days)}", flush=True)
    if args.dry_run:
        return 0

    if _lock_held():
        print("[补跑] 已有补跑进程在运行，本次退出（避免并发）", flush=True)
        return 1
    with open(LOCK, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    try:
        for i, day in enumerate(days, 1):
            print(f"\n[补跑] {i}/{len(days)} 正在更新 {day} …")
            cmd = [PY, os.path.join(BASE, "daily_update.py"), "--date", day]
            if args.proxy:
                cmd += ["--proxy", args.proxy]
            r = subprocess.run(cmd, text=True, timeout=1500)
            if r.returncode != 0:
                print(f"[补跑] {day} 更新失败，中止后续补跑")
                return 1
        print("\n[补跑] 全部补齐完成")
        return 0
    finally:
        try:
            os.remove(LOCK)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
