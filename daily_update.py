#!/usr/bin/env python3
"""每个交易日盘后自动更新脚本。

依次执行：
1. 当日选股（更新 last_result.json，网页版自动读取最新结果）；
2. 历史回放增量（把新交易日加进回放）；
3. 预判核对增量（核对前一交易日候选 vs 当日实际）；
4. （可选）把当日报告发到邮箱。

用法：
    python daily_update.py                      # 默认最近交易日
    python daily_update.py --date 2026-08-14    # 指定交易日
    python daily_update.py --send-email --to 365941027@qq.com
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import webapp  # noqa: E402
from daily_picker.cli import _parse_date, resolve_data_date  # noqa: E402


def wait_job(timeout: int = 900) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if webapp.JOB.state in ("done", "error"):
            return webapp.JOB.state == "done"
        time.sleep(0.5)
    return False


def cache_dir_stale(cache_root: str, data_date: str) -> bool:
    """判断目标日期的缓存目录是否为旧数据（K线最后一根不是目标日期）。"""
    day_dir = os.path.join(cache_root, data_date.replace("-", ""))
    if not os.path.isdir(day_dir):
        return False
    files = sorted(glob.glob(os.path.join(day_dir, "kline_*.json")))[:10]
    if not files:
        return False
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                bars = json.load(fh)
            if bars and bars[-1].get("date") == data_date:
                return False
        except Exception:
            continue
    return True


def already_up_to_date(data_date: str) -> bool:
    """若回放索引最后一天已覆盖目标日期，说明当天数据已更新，无需重跑。"""
    base = os.path.dirname(os.path.abspath(__file__))
    idx_path = os.path.join(base, "daily_picker", "cache", "replay", "index.json")
    try:
        with open(idx_path, encoding="utf-8") as f:
            idx = json.load(f)
        days = idx.get("days") or []
        return bool(days and days[-1] >= data_date)
    except Exception:
        return False


def run_pick(data_date: str, proxy: str = "", refresh: bool = False) -> bool:
    params = {"date": data_date}
    if refresh or cache_dir_stale(os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_picker", "cache"), data_date):
        params["refresh"] = True
    if proxy:
        params["proxy"] = proxy
    webapp.run_pipeline(params)
    ok = wait_job()
    print(f"[选股] {'成功' if ok else '失败'}: {webapp.JOB.error or ''}")
    return ok


def run_replay_update(data_date: str, history_start: str = "2026-07-01") -> bool:
    params = {"start": history_start, "end": data_date}
    webapp.replay_pipeline(params)
    ok = wait_job()
    print(f"[回放] {'成功' if ok else '失败'}: {webapp.JOB.error or ''}")
    return ok


def run_verify_update(data_date: str, history_start: str = "2026-07-01") -> bool:
    params = {"start": history_start, "end": data_date}
    webapp.verify_pipeline(params)
    ok = wait_job()
    print(f"[核对] {'成功' if ok else '失败'}: {webapp.JOB.error or ''}")
    return ok


def send_email(data_date: str, to_addr: str) -> bool:
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "send_report_email.py")
    cmd = [sys.executable, script, "--date", data_date, "--to", to_addr]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            print(f"[邮件] 已发送：{to_addr}")
            return True
        print(f"[邮件] 发送失败：{r.stdout} {r.stderr}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[邮件] 发送异常：{exc}")
        return False


def publish_static(proxy: str = "") -> bool:
    """生成静态站点并推送到 GitHub Pages（若已配置 GitHub 仓库）。"""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "publish_ghpages.sh")
    cmd = ["bash", script]
    if proxy:
        cmd.append("--proxy")
        cmd.append(proxy)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            print("[GitHub] 静态站点已发布")
            return True
        print(f"[GitHub] 发布失败：{r.stdout} {r.stderr}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[GitHub] 发布异常：{exc}")
        return False


def build_strategy_report(proxy: str = "") -> bool:
    """生成策略回测报告（推荐方案 G：弱市禁买 + 止盈8%），输出 site/strategy.html。"""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "build_strategy_report.py")
    cmd = [sys.executable, script]
    if proxy:
        cmd.append("--proxy")
        cmd.append(proxy)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode == 0:
            print("[策略] 策略回测报告已生成（推荐方案 G）")
            return True
        print(f"[策略] 生成失败：{r.stdout} {r.stderr}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[策略] 生成异常：{exc}")
        return False


def send_push(data_date: str, verdict: str = "") -> bool:
    """发送完成通知（未配置 webhook 时静默跳过）。"""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "send_push.py")
    text = f"数据日期 {data_date}" + (f" · 大盘判定：{verdict}" if verdict else "")
    cmd = [sys.executable, script, "--date", data_date, "--text", text]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        print(r.stdout.strip())
        return r.returncode == 0
    except Exception as exc:  # noqa: BLE001
        print(f"[推送] 调用异常：{exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="A股每日盘后自动更新")
    parser.add_argument("--date", default=None, help="数据日期 YYYY-MM-DD，默认最近交易日")
    parser.add_argument("--proxy", default="", help="行情代理，如 socks5h://127.0.0.1:7897")
    parser.add_argument("--history-start", default="2026-07-01", help="历史回放/核对起始日期")
    parser.add_argument("--offline", action="store_true", help="仅使用本地缓存（不联网）")
    parser.add_argument("--refresh", action="store_true", help="强制重新拉取行情（默认自动检测过期缓存）")
    parser.add_argument("--send-email", action="store_true", help="同时发送邮件报告")
    parser.add_argument("--to", default="365941027@qq.com", help="收件邮箱")
    parser.add_argument("--no-publish", action="store_true", help="跳过 GitHub Pages 发布")
    args = parser.parse_args()

    data_date = (args.date or resolve_data_date(None).isoformat())
    print(f"== 每日盘后更新 {data_date} ==")
    if already_up_to_date(data_date):
        print(f"数据已更新到 {data_date}（回放索引最后一天），本次跳过。")
        return 0
    if args.offline:
        webapp.run_pipeline({"date": data_date, "offline": True})
        wait_job()
        ok1 = webapp.JOB.state == "done"
        print(f"[选股(离线)] {'成功' if ok1 else '失败'}: {webapp.JOB.error or ''}")
    else:
        ok1 = run_pick(data_date, args.proxy, refresh=args.refresh)
    ok2 = run_replay_update(data_date, args.history_start)
    ok3 = run_verify_update(data_date, args.history_start)
    if not args.no_publish and (ok1 or ok2 or ok3):
        build_strategy_report(args.proxy)
        publish_static(args.proxy)
        try:
            verdict = ""
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_picker", "cache", "last_result.json"), encoding="utf-8") as f:
                verdict = (json.load(f).get("result") or {}).get("market_verdict", "")
        except Exception:
            pass
        send_push(data_date, verdict)
    if args.send_email:
        send_email(data_date, args.to)
    if not (ok1 or ok2 or ok3):
        print("全部步骤失败，请检查上方日志")
        return 1
    print("== 更新完成 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
