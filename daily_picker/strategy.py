"""短线观察策略卡 + 模拟盘回测引擎（原型）。

规则（透明、可解释、避免未来函数）：
1. 信号：观察日 S 收盘后发布候选，给出支撑位 support = max(MA5, 当日最低)。
2. 触发：S+1 日最低价触及支撑且收盘站回支撑之上（回踩不破）。
3. 执行：S+2 日以开盘价买入；若高开超过支撑 5% 视为追高，放弃。
4. 风控：止损 = 买入价 -7%；止盈 = 买入价 +10%。
5. 期限：最长持有 5 个交易日，到期按收盘卖出；同日先止损后止盈（保守）。
"""

from __future__ import annotations

import glob
import json
import os
from typing import Dict, List, Optional

from .config import Config
from .risks import load_risk_cache

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_ROOT = os.path.join(BASE, "daily_picker", "cache")

DEFAULT_PARAMS: Dict[str, object] = {
    "stop_pct": 0.07,     # 止损 -7%
    "target_pct": 0.10,   # 止盈 +10%
    "hold_days": 5,       # 最长持有 5 个交易日
    "chase_skip": 1.05,   # 执行日开盘高开超支撑 5% 放弃（不追高）
    "skip_weak": False,   # 大盘弱势日是否直接过滤（不触发交易）
    "skip_risk": False,   # 有风险提示的候选是否直接过滤（不触发交易）
    "enforce_t1": True,   # A股 T+1：买入当日不可卖出，最早次日（下一交易日）才可平仓
    "require_shrink": False,  # 触发日需缩量回踩（量 < 近5日均量），降低隔夜兑现风险
    "max_atr_pct": 0.0,       # 若 >0，则过滤波动过大的票（ATR% 上限），控制隔夜跳空风险
    "conservative_fill": True,  # 保守成交：跳空跌破止损按开盘价成交；跳空高开不享受超额收益
    "cost_pct": 0.0035,         # 单笔往返成本：佣金0.025%×2 + 印花税0.1% + 滑点0.2% ≈ 0.35%
}


def load_kline_bars(code: str) -> List[Dict]:
    """从最新缓存日目录读取个股日K（含未来 bar，用于模拟持有期）。"""
    if not os.path.isdir(CACHE_ROOT):
        return []
    dirs = sorted(d for d in os.listdir(CACHE_ROOT) if d.isdigit())
    for d in reversed(dirs):
        p = os.path.join(CACHE_ROOT, d, f"kline_{code}.json")
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                continue
    return []


def _by_date(bars: List[Dict]) -> Dict[str, Dict]:
    return {b["date"]: b for b in bars}


def _next_date(dates: List[str], d: str) -> Optional[str]:
    for x in dates:
        if x > d:
            return x
    return None


def atr_pct_of(code: str, lookback: int = 14) -> Optional[float]:
    """计算个股平均真实波幅百分比（ATR%），用于 T+1 隔夜风险控制。

    取最近 lookback 根日K的 (high-low)/前收盘 均值×100。数据不足返回 None。
    """
    bars = load_kline_bars(code)
    if not bars or len(bars) < 3:
        return None
    win = bars[-(lookback + 1):]
    trs: List[float] = []
    for i in range(1, len(win)):
        prev_close = win[i - 1].get("close")
        if not prev_close:
            continue
        trs.append((win[i]["high"] - win[i]["low"]) / prev_close * 100.0)
    if not trs:
        return None
    return sum(trs) / len(trs)


def _merge_params(params: Optional[Dict]) -> Dict:
    return {**DEFAULT_PARAMS, **(params or {})}


def simulate(entry: Dict, bars: List[Dict], params: Optional[Dict] = None) -> Dict:
    """对一条核对记录模拟一笔交易，返回结果字典。params 可覆盖止损/止盈/期限/弱市过滤。"""
    p = _merge_params(params)
    code = str(entry.get("code") or "").zfill(6)
    support = entry.get("support")
    checked_on = entry.get("checked_on") or ""
    if support is None or not bars or not checked_on:
        return {"status": "skip", "reason": "无支撑或缺少K线", "code": code}
    if p["skip_weak"] and entry.get("env_weak"):
        return {
            "status": "no_trigger",
            "reason": "弱市过滤：大盘判定弱势，按纪律不交易",
            "code": code,
            "env_weak": True,
        }
    if p["skip_risk"] and entry.get("has_risk"):
        return {
            "status": "no_trigger",
            "reason": "风险过滤：候选存在风险提示，按纪律不交易",
            "code": code,
            "has_risk": True,
        }
    bd = _by_date(bars)
    c_bar = bd.get(checked_on)
    if not c_bar:
        return {"status": "skip", "reason": "缺核对日K线", "code": code}

    # 触发：回踩支撑不破（低点触及支撑且收盘站回）
    if not (c_bar["low"] <= support and c_bar["close"] >= support):
        return {"status": "no_trigger", "reason": "未回踩到支撑（低点未触及或收盘破位）", "code": code}

    dates = sorted(bd.keys())

    # ---- T+1 友好性过滤（均基于买入前已知信息，无未来函数） ----
    idx = dates.index(checked_on)
    if p.get("require_shrink"):
        lookback = [bd[d]["volume"] for d in dates[max(0, idx - 5):idx]]
        if lookback:
            avg_vol = sum(lookback) / len(lookback)
            if avg_vol > 0 and c_bar.get("volume", 0) >= avg_vol:
                return {"status": "no_trigger", "reason": "触发日未缩量回踩（放量回踩，隔夜风险高）", "code": code}
    if p.get("max_atr_pct"):
        trs = []
        for j in range(max(0, idx - 13), idx + 1):
            b = bd[dates[j]]
            if j - 1 >= 0:
                prev_close = bd[dates[j - 1]]["close"]
                if prev_close:
                    trs.append((b["high"] - b["low"]) / prev_close * 100.0)
        if trs:
            atr_pct = sum(trs) / len(trs)
            if atr_pct > float(p["max_atr_pct"]):
                return {"status": "no_trigger", "reason": f"波动过大（ATR {atr_pct:.1f}% > 上限 {p['max_atr_pct']}%），隔夜风险高", "code": code}

    exec_date = _next_date(dates, checked_on)
    if not exec_date:
        return {"status": "skip", "reason": "缺执行日K线（最新交易日数据未到）", "code": code}
    e_bar = bd[exec_date]
    if e_bar["open"] > support * p["chase_skip"]:
        return {"status": "no_trigger", "reason": "执行日高开超5%，按纪律不追高", "code": code}

    entry_price = e_bar["open"]
    stop = round(entry_price * (1 - p["stop_pct"]), 3)
    target = round(entry_price * (1 + p["target_pct"]), 3)

    # 从执行日起逐日检查（最多 hold_days 个交易日）
    days = [exec_date]
    d = exec_date
    for _ in range(p["hold_days"]):
        nxt = _next_date(dates, d)
        if not nxt:
            break
        days.append(nxt)
        d = nxt

    exit_price = exit_date = reason = None
    for i, day in enumerate(days):
        bar = bd[day]
        # A股 T+1：买入当日（i==0）不允许卖出，止损/止盈只能从次一交易日起生效
        if i == 0 and p["enforce_t1"]:
            continue
        if bar["low"] <= stop:
            fill = stop
            # 保守成交：若跳空低开已跌破止损，实际只能按更差的开盘价卖出
            if p.get("conservative_fill") and bar["open"] < stop:
                fill = bar["open"]
            exit_price, exit_date, reason = fill, day, "止损"
            break
        if bar["high"] >= target:
            # 保守成交：跳空高开也不计入超出目标的额外收益
            exit_price, exit_date, reason = target, day, "止盈"
            break
        if i == p["hold_days"]:  # 到期
            exit_price, exit_date, reason = bar["close"], day, "到期卖出"
            break
    if exit_price is None:  # 数据不足（比如最后一天）
        last = bd[days[-1]]
        exit_price, exit_date, reason = last["close"], days[-1], "到期卖出"

    # 扣除往返交易成本（佣金/印花税/滑点）
    pnl = exit_price / entry_price - 1 - float(p.get("cost_pct", 0.0))
    return {
        "status": "trade",
        "code": code,
        "name": entry.get("name") or code,
        "group": entry.get("group") or "",
        "env_weak": bool(entry.get("env_weak")),
        "signal_date": entry.get("date") or "",
        "checked_on": checked_on,
        "support": support,
        "entry_date": exec_date,
        "entry": round(entry_price, 3),
        "stop": stop,
        "target": target,
        "exit_date": exit_date,
        "exit": round(exit_price, 3),
        "pnl_pct": round(pnl * 100, 2),
        "reason": reason,
    }


def run_backtest(verify_entries: List[Dict], params: Optional[Dict] = None) -> Dict:
    """对全部核对记录跑模拟盘，返回指标与净值曲线数据。params 可覆盖规则。"""
    trades: List[Dict] = []
    stats = {"skip": 0, "no_trigger": 0}
    for e in verify_entries:
        code = str(e.get("code") or "").zfill(6)
        r = simulate(e, load_kline_bars(code), params=params)
        if r["status"] == "trade":
            trades.append(r)
        elif r["status"] == "no_trigger":
            stats["no_trigger"] += 1
        else:
            stats["skip"] += 1
    trades.sort(key=lambda t: (t["entry_date"], t["code"]))

    # 复利净值曲线
    curve = [1.0]
    curve_points: List[Dict] = []
    equity = 1.0
    peak = 1.0
    mdd = 0.0
    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    for t in trades:
        equity *= (1 + t["pnl_pct"] / 100)
        curve.append(round(equity, 4))
        curve_points.append({"date": t["exit_date"], "equity": round(equity, 4)})
        peak = max(peak, equity)
        mdd = min(mdd, equity / peak - 1)

    gross_win = sum(t["pnl_pct"] for t in wins)
    gross_loss = abs(sum(t["pnl_pct"] for t in losses)) or 1e-9
    return {
        "trades": trades,
        "n_trades": len(trades),
        "n_win": len(wins),
        "n_loss": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else 99.0,
        "cum_return": round((equity - 1) * 100, 2),
        "max_drawdown": round(mdd * 100, 2),
        "equity_curve": curve,
        "curve_points": curve_points,
        "no_trigger": stats["no_trigger"],
        "skip": stats["skip"],
    }


def run_portfolio(
    verify_entries: List[Dict],
    params: Optional[Dict] = None,
    max_positions: int = 3,
    weight: float = 1.0 / 3.0,
) -> Dict:
    """组合级回测（贴近实盘）：最多同时持有 max_positions 只，每只投入固定比例资金，其余留现金。

    与 run_backtest 的区别：不再假设"每笔全仓复利"，因此收益基数更真实；
    已包含 simulate 内的交易成本与保守成交价。
    """
    trades: List[Dict] = []
    for e in verify_entries:
        code = str(e.get("code") or "").zfill(6)
        r = simulate(e, load_kline_bars(code), params=params)
        if r["status"] == "trade":
            trades.append(r)
    trades.sort(key=lambda t: (t["entry_date"], t["code"]))

    dates = sorted({t["entry_date"] for t in trades} | {t["exit_date"] for t in trades})
    if not dates:
        return {"equity_curve": [1.0], "curve_points": [], "n_trades": 0,
                "cum_return": 0.0, "max_drawdown": 0.0, "skipped": 0, "trades": []}

    cash = 1.0
    open_pos: List[Dict] = []
    curve: List[float] = [1.0]
    curve_points: List[Dict] = []
    peak, mdd = 1.0, 0.0
    skipped = 0

    for day in dates:
        # 先处理平仓（释放资金）
        for p in [x for x in open_pos if x["exit_date"] == day]:
            cash += p["value"] * (1 + p["pnl"])
            open_pos.remove(p)
        # 再处理开仓（等权分配，资金不足则跳过）
        equity_now = cash + sum(x["value"] for x in open_pos)
        for t in [x for x in trades if x["entry_date"] == day]:
            if len(open_pos) >= max_positions:
                skipped += 1
                continue
            alloc = equity_now * weight
            if alloc <= 0 or cash < alloc:
                skipped += 1
                continue
            cash -= alloc
            open_pos.append({"value": alloc, "pnl": t["pnl_pct"] / 100.0, "exit_date": t["exit_date"]})
        equity = cash + sum(x["value"] for x in open_pos)
        curve.append(round(equity, 4))
        curve_points.append({"date": day, "equity": round(equity, 4)})
        peak = max(peak, equity)
        mdd = min(mdd, equity / peak - 1)

    final_equity = curve[-1]
    return {
        "equity_curve": curve,
        "curve_points": curve_points,
        "n_trades": len(trades),
        "cum_return": round((final_equity - 1) * 100, 2),
        "max_drawdown": round(mdd * 100, 2),
        "skipped": skipped,
        "trades": trades,
    }


def build_cards(replay_payload: Dict, verdict: str, params: Optional[Dict] = None) -> List[Dict]:
    """为最新候选生成策略卡（回踩触发价 / 止损 / 目标 / 期限 / 建议仓位）。

    params 可覆盖止损/止盈；默认按推荐方案 G（止盈8%、弱市禁买）。
    """
    p = _merge_params(params or {"target_pct": 0.08, "skip_weak": True})
    risk_cache = load_risk_cache()
    cards: List[Dict] = []
    cands = replay_payload.get("priority", []) + replay_payload.get("strong", [])
    for c in cands:
        code = str(c.get("code") or "").zfill(6)
        bars = load_kline_bars(code)
        if not bars:
            continue
        last = bars[-1]
        closes = [b["close"] for b in bars]
        ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
        support = round(max(ma5, last["low"]), 2) if ma5 else round(last["low"], 2)
        close = last["close"]
        risks = [r.get("note", "") for r in risk_cache.get(code, []) if not r.get("veto")]
        vetoed = [r.get("note", "") for r in risk_cache.get(code, []) if r.get("veto")]
        if p["skip_weak"] and verdict in ("不适合入场", "观望为主"):
            position = "弱市禁买：等待大盘转强再入场"
        elif verdict == "不适合入场":
            position = "空仓观察，不推荐入场"
        elif verdict == "观望为主":
            position = "轻仓试探，单票 ≤5%"
        else:
            position = "单票 ≤10%，不集中"
        stop_pct = p.get("stop_pct", 0.07)
        target_pct = p.get("target_pct", 0.08)
        cards.append({
            "code": code,
            "name": c.get("name") or code,
            "category": c.get("category") or "",
            "structure": c.get("structure") or "",
            "close": close,
            "pct_chg": c.get("pct_chg"),
            "support": support,
            "trigger": f"回踩 {support:.2f} 附近不破并转强",
            "stop": round(support * (1 - stop_pct * 0.7), 2),
            "target": round(close * (1 + target_pct), 2),
            "hold_days": int(p.get("hold_days", 5)),
            "position": position,
            "note": c.get("note") or "",
            "risks": risks,
            "vetoed": vetoed,
        })
    return cards


def build_watch_cards(
    watch_items: List[Dict],
    cfg,
    verdict: str,
    params: Optional[Dict] = None,
) -> List[Dict]:
    """为用户自选股票生成策略卡（按同一套推荐规则）。

    watch_items: [{"code": "000001", "name": "平安银行"}, ...]
    规则：支撑 = max(MA5, 最近收盘日最低)；止损 = 支撑×(1-5%)；目标 = 最近收盘×(1+8%)；
    大盘弱势时同样提示“弱市禁买/轻仓”。K线本地无缓存则联网拉取。
    """
    p = _merge_params(params or {"target_pct": 0.08, "skip_weak": True})
    risk_cache = load_risk_cache()
    from .data_fetch import fetch_kline  # noqa: E402（延迟导入，避免循环依赖）

    cards: List[Dict] = []
    for item in watch_items:
        code = str(item.get("code") or "").zfill(6)
        if not code or code == "000000":
            continue
        name = item.get("name") or code
        bars = load_kline_bars(code)
        if not bars:
            try:
                bars = fetch_kline(code, cfg)
            except Exception:
                bars = []
        if len(bars) < 10:
            continue
        last = bars[-1]
        closes = [b["close"] for b in bars[-30:]]
        if len(closes) < 5:
            continue
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else None
        support = round(max(ma5, last["low"]), 2)
        close = last["close"]
        stop_pct = p.get("stop_pct", 0.07)
        target_pct = p.get("target_pct", 0.08)
        if p["skip_weak"] and verdict in ("不适合入场", "观望为主"):
            position = "弱市禁买：等待大盘转强再入场"
        elif verdict == "不适合入场":
            position = "空仓观察，不推荐入场"
        elif verdict == "观望为主":
            position = "轻仓试探，单票 ≤5%"
        else:
            position = "单票 ≤10%，不集中"
        structure = "多头排列" if (ma10 and ma5 > ma10 and close > ma10) else (
            "站上5日线" if close > ma5 else "位于5日线下方"
        )
        risks = [r.get("note", "") for r in risk_cache.get(code, []) if not r.get("veto")]
        vetoed = [r.get("note", "") for r in risk_cache.get(code, []) if r.get("veto")]
        cards.append({
            "code": code,
            "name": name,
            "category": "自选盯盘",
            "structure": structure,
            "close": close,
            "pct_chg": last.get("pct_chg"),
            "support": support,
            "trigger": f"回踩 {support:.2f} 附近不破并转强",
            "stop": round(support * (1 - stop_pct * 0.7), 2),
            "target": round(close * (1 + target_pct), 2),
            "hold_days": int(p.get("hold_days", 5)),
            "position": position,
            "note": f"自选股策略卡：现价参考 {close:.2f}，等回踩 {support:.2f} 附近不破再考虑；跌破止损位即放弃。",
            "risks": risks,
            "vetoed": vetoed,
        })
    return cards
