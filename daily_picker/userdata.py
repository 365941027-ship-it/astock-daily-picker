"""用户数据层：自选股、持仓、模拟交易记录（本地 JSON 持久化）。"""

from __future__ import annotations

import json
import os
import threading
from datetime import date
from typing import Dict, List, Optional


DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "daily_picker", "cache", "user_data.json",
)

_lock = threading.Lock()


def _default() -> Dict:
    return {
        "watchlist": [],
        "portfolio": {
            "positions": [],
            "trades": [],
            "cash": 100000.0,
        },
    }


def load() -> Dict:
    with _lock:
        if not os.path.exists(DATA_FILE):
            return _default()
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            d = _default()
            d.update(data)
            d.setdefault("watchlist", [])
            d.setdefault("portfolio", {"positions": [], "trades": [], "cash": 100000.0})
            return d
        except Exception:
            return _default()


def save(data: Dict) -> None:
    with _lock:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def add_watch(code: str, name: str, alert_price: Optional[float] = None, alert_type: str = "below") -> Dict:
    data = load()
    for w in data["watchlist"]:
        if w["code"] == code:
            w["alert_price"] = alert_price
            w["alert_type"] = alert_type
            save(data)
            return {"ok": True, "msg": "已更新提醒"}
    data["watchlist"].append({
        "code": code, "name": name,
        "alert_price": alert_price, "alert_type": alert_type,
        "added_at": date.today().isoformat(),
    })
    save(data)
    return {"ok": True, "msg": "已加入自选"}


def remove_watch(code: str) -> Dict:
    data = load()
    before = len(data["watchlist"])
    data["watchlist"] = [w for w in data["watchlist"] if w["code"] != code]
    save(data)
    return {"ok": True, "msg": "已删除" if len(data["watchlist"]) < before else "未找到"}


def _position(data: Dict, code: str) -> Optional[Dict]:
    for p in data["portfolio"]["positions"]:
        if p["code"] == code:
            return p
    return None


def trade(code: str, name: str, action: str, price: float, shares: int, trade_date: str = None) -> Dict:
    """模拟交易：buy 加仓，sell 减仓（可做空？不允许，只允许卖已有持仓）。"""
    data = load()
    if price <= 0 or shares <= 0:
        return {"ok": False, "msg": "价格和数量必须为正"}
    trade_date = trade_date or date.today().isoformat()
    pos = _position(data, code)

    if action == "buy":
        # 摊薄成本
        if pos:
            total_cost = pos["cost"] * pos["shares"] + price * shares
            pos["shares"] += shares
            pos["cost"] = round(total_cost / pos["shares"], 4)
        else:
            data["portfolio"]["positions"].append({
                "code": code, "name": name, "shares": shares, "cost": price,
                "added_at": trade_date,
            })
        data["portfolio"]["cash"] = round(data["portfolio"]["cash"] - price * shares, 2)
        msg = f"已买入 {code} {shares} 股 @ {price}"
    elif action == "sell":
        if not pos or pos["shares"] < shares:
            return {"ok": False, "msg": "持仓不足，无法卖出"}
        pos["shares"] -= shares
        data["portfolio"]["cash"] = round(data["portfolio"]["cash"] + price * shares, 2)
        if pos["shares"] == 0:
            data["portfolio"]["positions"] = [p for p in data["portfolio"]["positions"] if p["code"] != code]
        msg = f"已卖出 {code} {shares} 股 @ {price}"
    else:
        return {"ok": False, "msg": "未知操作"}

    data["portfolio"]["trades"].append({
        "date": trade_date, "code": code, "name": name,
        "action": action, "price": price, "shares": shares,
    })
    save(data)
    return {"ok": True, "msg": msg}


def close_position(code: str) -> Dict:
    data = load()
    before = len(data["portfolio"]["positions"])
    data["portfolio"]["positions"] = [p for p in data["portfolio"]["positions"] if p["code"] != code]
    save(data)
    return {"ok": True, "msg": "已平仓" if len(data["portfolio"]["positions"]) < before else "未找到"}
