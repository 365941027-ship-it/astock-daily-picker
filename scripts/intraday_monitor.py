#!/usr/bin/env python3
"""盘中盯盘助手：实时监控最新候选池，按策略卡对比支撑/止损/止盈，状态变化时推送提醒。

用法：
    python scripts/intraday_monitor.py                 # 单次快照（写 intraday_status.json）
    python scripts/intraday_monitor.py --watch         # 交易时段循环盯盘，默认 30 秒一次
    python scripts/intraday_monitor.py --watch --interval 60 --force

说明：
- 候选来自最近一个交易日的回放 + 策略卡（推荐方案 I 口径），一票否决的自动剔除；
- 非交易时段默认只做一次并提示“已收盘”，循环模式到 15:10 自动停止；
- 推送依赖 ~/.config/astock_push.json 或环境变量（同 send_push.py），未配置时仅写日志。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, time as dtime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from daily_picker.config import Config  # noqa: E402
from daily_picker.data_fetch import fetch_quotes_realtime  # noqa: E402
from daily_picker.risks import load_risk_cache  # noqa: E402
from daily_picker.strategy import build_cards  # noqa: E402

CN_TZ = timezone(timedelta(hours=8))
STATUS_FILE = os.path.join(BASE, "daily_picker", "cache", "intraday_status.json")
PY = sys.executable


def _now() -> datetime:
    return datetime.now(CN_TZ)


def latest_replay_and_verdict():
    """返回 (payload, data_date, market_verdict)。"""
    replay_dir = os.path.join(BASE, "daily_picker", "cache", "replay")
    idx_path = os.path.join(replay_dir, "index.json")
    try:
        with open(idx_path, encoding="utf-8") as f:
            idx = json.load(f)
    except Exception:
        return None, "", ""
    days = idx.get("days") or []
    if not days:
        return None, "", ""
    day = days[-1]
    try:
        with open(os.path.join(replay_dir, f"{day}.json"), encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        payload = None
    verdict = ""
    try:
        with open(os.path.join(BASE, "site", "data", "latest.json"), encoding="utf-8") as f:
            verdict = json.load(f).get("market_verdict", "")
    except Exception:
        pass
    return payload, day, verdict


def _in_session(force: bool) -> bool:
    n = _now()
    if n.weekday() >= 5 and not force:
        return False
    if force:
        return True
    return dtime(9, 15) <= n.time() <= dtime(15, 10)


def classify(price, low, high, card) -> tuple[str, str]:
    """返回 (状态码, 中文说明)。基于策略卡的支撑/止损/止盈做盘中判断。"""
    support = card.get("support")
    stop = card.get("stop")
    target = card.get("target")
    if price is None or support is None:
        return "nodata", "暂无行情/支撑"
    if stop is not None and low is not None and low <= stop:
        return "broke", f"已跌破止损 {stop:.2f}（放弃，不再关注）"
    if target is not None and high is not None and high >= target:
        return "target", f"已触及目标 {target:.2f}（可落袋）"
    if low is not None and low <= support:
        if price >= support:
            return "tested", f"回踩支撑 {support:.2f} 后站回现价 {price:.2f}（观察触发候选）"
        return "weak", f"盘中跌破支撑 {support:.2f}，现价 {price:.2f}（等站回再考虑）"
    dist = (price - support) / support * 100 if support else 0
    if dist <= 1.5:
        return "near", f"接近支撑 {support:.2f}（现价 {price:.2f}，距支撑 {dist:.1f}%）"
    return "watch", f"未到支撑，现价 {price:.2f}（距支撑 {dist:.1f}%）"


def snapshot() -> dict:
    cfg = Config()
    payload, day, verdict = latest_replay_and_verdict()
    if not payload:
        return {"ok": False, "error": "暂无回放数据，请先运行每日更新"}
    cards = build_cards(payload, verdict, params={"target_pct": 0.08, "skip_weak": True})
    risk_map = load_risk_cache()
    # 排雷一票否决剔除；只盯 priority+strong（弱市禁买仍显示“纪律禁买”供观察）
    watch = [c for c in cards if not c.get("vetoed")]
    codes = [c["code"] for c in watch]
    quotes = fetch_quotes_realtime(codes, cfg) if codes else {}
    items = []
    for c in watch:
        q = quotes.get(c["code"], {})
        price = q.get("price")
        status, note = classify(price, q.get("low"), q.get("high"), c)
        items.append({
            "code": c["code"],
            "name": c["name"],
            "category": c.get("category", ""),
            "structure": c.get("structure", ""),
            "verdict_note": c.get("position", ""),
            "close_ref": c.get("close"),
            "support": c.get("support"),
            "stop": c.get("stop"),
            "target": c.get("target"),
            "price": price,
            "pct_chg": q.get("pct_chg"),
            "high": q.get("high"),
            "low": q.get("low"),
            "status": status,
            "note": note,
            "risks": c.get("risks", []),
        })
    items.sort(key=lambda x: ("broke" == x["status"], "target" == x["status"], "weak" == x["status"]),
               reverse=True)
    return {
        "ok": True,
        "generated_at": _now().isoformat(timespec="seconds"),
        "data_date": day,
        "market_verdict": verdict,
        "in_session": _in_session(False),
        "items": items,
    }


def changed_events(prev: dict | None, cur: dict) -> list[dict]:
    if not prev:
        return []
    prev_map = {i["code"]: i["status"] for i in prev.get("items", [])}
    events = []
    for i in cur.get("items", []):
        old = prev_map.get(i["code"])
        if old and old != i["status"] and i["status"] in ("broke", "weak", "tested", "target", "near"):
            events.append({"code": i["code"], "name": i["name"], "from": old, "to": i["status"], "note": i["note"]})
    return events


def send_notify(text: str, date_str: str) -> None:
    script = os.path.join(BASE, "scripts", "send_push.py")
    cmd = [PY, script, "--date", date_str, "--text", text]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.stdout.strip():
            print(r.stdout.strip())
    except Exception as exc:  # noqa: BLE001
        print(f"[盯盘] 推送调用异常：{exc}")


def load_status() -> dict | None:
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_status(s: dict) -> None:
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="盘中盯盘助手")
    parser.add_argument("--watch", action="store_true", help="循环盯盘（交易时段）")
    parser.add_argument("--interval", type=int, default=30, help="循环间隔秒数")
    parser.add_argument("--force", action="store_true", help="忽略交易时段限制（测试用）")
    args = parser.parse_args()

    in_session = _in_session(args.force)
    if not in_session and not args.watch:
        print("[盯盘] 当前非交易时段，仍执行一次快照供参考。")
    if not in_session and args.watch:
        print("[盯盘] 当前非交易时段，退出循环（可用 --force 测试）。")

    first = True
    while True:
        try:
            s = snapshot()
            if not s.get("ok"):
                print(s.get("error", "未知错误"))
                return 1
            prev = load_status()
            events = changed_events(prev, s) if not first else []
            s["events"] = events
            save_status(s)
            n = len(s["items"])
            print(f"[盯盘] {s['generated_at']} 数据日 {s['data_date']} 大盘:{s['market_verdict'] or '未知'} 候选 {n} 只")
            urgent = [i for i in s["items"] if i["status"] in ("broke", "weak", "tested", "target")]
            for i in urgent:
                print(f"   [{i['status'].upper():5s}] {i['code']} {i['name']} {i['note']}")
            if events:
                txt_lines = [f"盯盘状态变化 {s['data_date']}："]
                for e in events:
                    txt_lines.append(f"  {e['name']} {e['code']}：{e['note']}")
                send_notify("\n".join(txt_lines), s["data_date"])
            first = False
        except KeyboardInterrupt:
            print("\n[盯盘] 已停止")
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"[盯盘] 异常：{exc}", file=sys.stderr)
        if not args.watch or not _in_session(args.force):
            if args.watch and not _in_session(args.force):
                print("[盯盘] 已过 15:10，停止循环")
            return 0
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    sys.exit(main())
