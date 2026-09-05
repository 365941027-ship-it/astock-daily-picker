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
from datetime import datetime, date, time as dtime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from daily_picker.config import Config  # noqa: E402
from daily_picker.data_fetch import fetch_quotes_realtime  # noqa: E402
from daily_picker.indicators import kdj_series, macd_series  # noqa: E402
from daily_picker.risks import load_risk_cache  # noqa: E402
from daily_picker.strategy import build_cards, build_watch_cards, load_kline_bars  # noqa: E402
from daily_picker.userdata import load as load_userdata  # noqa: E402

CN_TZ = timezone(timedelta(hours=8))
STATUS_FILE = os.path.join(BASE, "daily_picker", "cache", "intraday_status.json")
PY = sys.executable
CFG_PROXY = ""


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


def compute_structure(code: str, quote: dict, in_session: bool) -> dict | None:
    """实时计算结构确认（1 价≥MA5 / 3 MACD未走弱 / 4 K>D）。

    盘中用“实时价补一根当日虚拟K”重算日线指标；非交易时段用最近收盘K。
    数据不足返回 None（调用方按“未知”处理，不降级）。
    """
    bars = load_kline_bars(code)
    if not bars or len(bars) < 35:
        return None
    bars = [dict(b) for b in bars]
    today = date.today().isoformat()
    # 若在交易时段且实时价有效，追加“今日”虚拟K，让 MA5/MACD/KDJ 反映盘中进展
    price = quote.get("price")
    if in_session and price is not None:
        prev = bars[-1]
        high = quote.get("high")
        low = quote.get("low")
        open_ = quote.get("open")
        bars.append({
            "date": today,
            "open": open_ if open_ is not None else prev["close"],
            "close": price,
            "high": high if high is not None else max(prev["close"], price),
            "low": low if low is not None else min(prev["close"], price),
            "volume": prev.get("volume", 0),
        })
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
    ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else None
    macd = macd_series(closes)
    kdj = kdj_series(highs, lows, closes)
    flags = {}
    reasons = []
    # 1) 现价 ≥ MA5
    flags["ma5"] = ma5 is not None and price is not None and price >= ma5
    if not flags["ma5"]:
        reasons.append("现价仍在MA5下方")
    # 3) MACD柱体未继续走弱：DIF≥DEA 或 柱体比上一日改善
    if macd and len(macd["dif"]) >= 2:
        dif, dea, hist = macd["dif"][-1], macd["dea"][-1], macd["hist"][-1]
        prev_hist = macd["hist"][-2]
        flags["macd"] = dif >= dea or hist > prev_hist
        if not flags["macd"]:
            reasons.append("MACD仍在走弱(DIF<DEA且柱体未改善)")
    else:
        flags["macd"] = False
        reasons.append("MACD数据不足")
    # 4) KDJ未死叉：K>D
    if kdj:
        flags["kdj"] = kdj["k"][-1] > kdj["d"][-1]
        if not flags["kdj"]:
            reasons.append("KDJ死叉(K<D)")
    else:
        flags["kdj"] = False
        reasons.append("KDJ数据不足")
    return {
        "ok": all(flags.values()),
        "price": price,
        "ma5": round(ma5, 2) if ma5 is not None else None,
        "ma10": round(ma10, 2) if ma10 is not None else None,
        "k": round(kdj["k"][-1], 2) if kdj else None,
        "d": round(kdj["d"][-1], 2) if kdj else None,
        "dif": round(macd["dif"][-1], 3) if macd else None,
        "dea": round(macd["dea"][-1], 3) if macd else None,
        "flags": flags,
        "reasons": reasons,
    }


def snapshot(proxy: str = "") -> dict:
    cfg = Config()
    if proxy:
        cfg.proxy = proxy
    payload, day, verdict = latest_replay_and_verdict()
    if not payload:
        return {"ok": False, "error": "暂无回放数据，请先运行每日更新"}
    cards = build_cards(payload, verdict, params={"target_pct": 0.08, "skip_weak": True})
    # 合并用户自选股（与系统候选同规则盯盘；同代码优先系统候选）
    try:
        ud = load_userdata()
        watch_items = ud.get("watchlist", []) or []
    except Exception:
        watch_items = []
    if watch_items:
        watch_cards = build_watch_cards(watch_items, cfg, verdict, params={"target_pct": 0.08, "skip_weak": True})
        seen = {c["code"] for c in cards}
        cards = cards + [c for c in watch_cards if c["code"] not in seen]
    risk_map = load_risk_cache()
    # 排雷一票否决剔除；只盯 priority+strong（弱市禁买仍显示“纪律禁买”供观察）
    watch = [c for c in cards if not c.get("vetoed")]
    codes = [c["code"] for c in watch]
    quotes = fetch_quotes_realtime(codes, cfg) if codes else {}
    in_session = _in_session(False)
    items = []
    for c in watch:
        q = quotes.get(c["code"], {})
        price = q.get("price")
        status, note = classify(price, q.get("low"), q.get("high"), c)
        struct = None
        if status == "tested":
            struct = compute_structure(c["code"], q, in_session)
            if struct and not struct["ok"]:
                status = "tested_weak"
                note = (f"回踩支撑 {c['support']:.2f} 后站回，但结构未确认（"
                        f"{'；'.join(struct['reasons'][:2]) or '数据不足'}），继续观察")
            elif struct and struct["ok"]:
                note += " · 结构确认（价≥MA5 / MACD未走弱 / K>D）"
        items.append({
            "code": c["code"],
            "name": c["name"],
            "source": "system" if c.get("category") != "自选盯盘" else "watch",
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
            "struct": struct,
        })
    items.sort(key=lambda x: ("broke" == x["status"], "target" == x["status"], "weak" == x["status"]),
               reverse=True)
    return {
        "ok": True,
        "generated_at": _now().isoformat(timespec="seconds"),
        "data_date": day,
        "market_verdict": verdict,
        "in_session": in_session,
        "items": items,
    }


def changed_events(prev: dict | None, cur: dict) -> list[dict]:
    if not prev:
        return []
    prev_map = {i["code"]: i["status"] for i in prev.get("items", [])}
    events = []
    for i in cur.get("items", []):
        old = prev_map.get(i["code"])
        if old and old != i["status"] and i["status"] in ("broke", "weak", "tested", "tested_weak", "target", "near"):
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
    parser.add_argument("--proxy", default="", help="行情代理，如 socks5h://127.0.0.1:7897")
    args = parser.parse_args()
    global CFG_PROXY
    CFG_PROXY = args.proxy

    in_session = _in_session(args.force)
    if not in_session and not args.watch:
        print("[盯盘] 当前非交易时段，仍执行一次快照供参考。")
    if not in_session and args.watch:
        print("[盯盘] 当前非交易时段，退出循环（可用 --force 测试）。")

    first = True
    while True:
        try:
            s = snapshot(proxy=args.proxy)
            if not s.get("ok"):
                print(s.get("error", "未知错误"))
                return 1
            prev = load_status()
            events = changed_events(prev, s) if not first else []
            s["events"] = events
            save_status(s)
            n = len(s["items"])
            print(f"[盯盘] {s['generated_at']} 数据日 {s['data_date']} 大盘:{s['market_verdict'] or '未知'} 候选 {n} 只")
            urgent = [i for i in s["items"] if i["status"] in ("broke", "weak", "tested", "tested_weak", "target")]
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
