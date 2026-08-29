"""选股过滤、评分与归类。

规则来自工作区历史日报（A股短线候选_2026-07-07）：
- 非 ST，剔除新股/退市风险标的；
- 今日逆势收红；
- 成交额>=5 亿元，换手率>=3%；
- 收盘站上 20 日线，优先 5 日线大于 10 日线；
- MACD 黄白线在 0 轴上方，或金叉/柱体改善；
- KDJ 金叉或低中位拐头，但不过热；
- 近 5/10 日涨幅不过度透支，涨停或接近涨停的票只列为“等回踩”。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import Config
from .indicators import analyze_bars


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def is_st(name: str) -> bool:
    upper = (name or "").upper()
    return "ST" in upper or "退" in name


def _limit_pct(code: str, cfg: Config) -> float:
    return cfg.chi_limit_pct if code.startswith(("300", "301", "688", "689")) else cfg.main_limit_pct


@dataclass
class Candidate:
    code: str
    name: str
    snapshot: Dict
    ind: Optional[Dict] = None
    category: str = "优先观察"
    structure: str = ""
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    note: str = ""
    risks: List[str] = field(default_factory=list)       # 风险提示（非否决）
    risk_detail: List[Dict] = field(default_factory=list)  # 原始风险事件

    @property
    def close(self) -> Optional[float]:
        return _num(self.snapshot.get("f2"))

    @property
    def pct_chg(self) -> Optional[float]:
        return _num(self.snapshot.get("f3"))

    @property
    def amount(self) -> Optional[float]:
        return _num(self.snapshot.get("f6"))

    @property
    def turnover(self) -> Optional[float]:
        return _num(self.snapshot.get("f8"))


def prefilter(snapshot_rows: List[Dict], cfg: Config) -> List[Dict]:
    """流动性预筛：只对可能进入候选的股票拉日K，节省请求。"""
    rows = []
    for row in snapshot_rows:
        code = str(row.get("f12") or "").zfill(6)
        name = str(row.get("f14") or "")
        if is_st(name) or name.startswith(("N", "C")):
            continue
        if code.startswith(("4", "8", "92")):  # 北交所等不在口径内
            continue
        amount = _num(row.get("f6"))
        turnover = _num(row.get("f8"))
        pct = _num(row.get("f3"))
        if amount is None or amount < cfg.min_amount:
            continue
        if turnover is None or turnover < cfg.min_turnover:
            continue
        if cfg.require_red and (pct is None or pct <= 0):
            continue
        rows.append(row)
    return rows


def market_stats(snapshot_rows: List[Dict], cfg: Config) -> Dict:
    up = down = flat = limit_up = 0
    for row in snapshot_rows:
        pct = _num(row.get("f3"))
        if pct is None:
            continue
        if pct > 0:
            up += 1
        elif pct < 0:
            down += 1
        else:
            flat += 1
        code = str(row.get("f12") or "").zfill(6)
        if pct >= _limit_pct(code, cfg):
            limit_up += 1
    return {
        "total": len(snapshot_rows),
        "up": up,
        "down": down,
        "flat": flat,
        "limit_up": limit_up,
    }


def evaluate(cand: Candidate, cfg: Config) -> tuple[bool, List[str]]:
    """硬性规则过滤，返回 (是否通过, 未通过原因)。"""
    ind = cand.ind
    reasons: List[str] = []
    if ind is None:
        return False, ["无日K数据"]
    if ind["bars"] < cfg.min_list_days:
        reasons.append(f"上市不足{cfg.min_list_days}个交易日（次新）")
    if cfg.require_red and (cand.pct_chg is None or cand.pct_chg <= 0):
        reasons.append("当日未收红")
    if ind.get("ma20") is None or cand.close is None or cand.close <= ind["ma20"]:
        reasons.append("未站上20日线")

    macd_ok = False
    if ind.get("dif") is not None:
        above_zero = cfg.macd_above_zero and ind["dif"] > 0
        improving = (
            cfg.macd_improving
            and ind["dif"] > ind["dea"]
            and ind["hist"] > ind["hist_prev"]
        )
        macd_ok = above_zero or improving
    if not macd_ok:
        reasons.append("MACD未在0轴上方且未金叉/柱体改善")

    kdj_ok = False
    if ind.get("k") is not None:
        golden = (
            cfg.kdj_allow_golden
            and ind["k_prev"] <= ind["d_prev"]
            and ind["k"] > ind["d"]
        )
        turning = cfg.kdj_allow_turning and ind["k"] > ind["d"] and ind["k"] < 80
        kdj_ok = golden or turning
    if not kdj_ok:
        reasons.append("KDJ无金叉且未拐头")
    return len(reasons) == 0, reasons


def score(cand: Candidate, cfg: Config, market_verdict: str = "") -> float:
    ind = cand.ind
    s = 0.0
    if ind.get("ma5") is not None and ind.get("ma10") is not None and ind["ma5"] > ind["ma10"]:
        s += 2
    if ind.get("ma20") is not None and cand.close is not None and cand.close > ind["ma20"]:
        s += 2
    if ind.get("dif") is not None:
        if ind["dif"] > 0:
            s += 2
        if ind["dif"] > ind["dea"]:
            s += 1
        if ind["hist"] > ind["hist_prev"]:
            s += 1
    if ind.get("k") is not None:
        if ind["k"] > ind["d"]:
            s += 1
        if 20 <= ind["j"] <= 90:
            s += 1
    if cand.turnover is not None and 3 <= cand.turnover <= 15:
        s += 1
    if cand.pct_chg is not None and 1 <= cand.pct_chg <= 6:
        s += 1
    # P1 评分因子：观察日涨幅 2~5% 加分（温和启动），8% 以上减分（追高），12% 以上重罚
    if cand.pct_chg is not None:
        if 2 <= cand.pct_chg < 5:
            s += 1
        elif cand.pct_chg > 8:
            s -= 3
        elif cand.pct_chg > 12:
            s -= 5
    # P0 大盘门控：大盘弱时统一降分（弱市追涨容易失败）
    if market_verdict == "不适合入场":
        s -= 3
    elif market_verdict == "观望为主":
        s -= 1
    if ind.get("gain_5d") is not None and ind["gain_5d"] > cfg.max_5d_gain * 0.8:
        s -= 1.5
    if ind.get("gain_10d") is not None and ind["gain_10d"] > cfg.max_10d_gain * 0.8:
        s -= 1.5
    return s


def structure_label(cand: Candidate, cfg: Config) -> str:
    ind = cand.ind
    if (
        ind.get("ma5") is not None
        and ind.get("ma10") is not None
        and ind["ma5"] > ind["ma10"]
        and (ind.get("dif") or 0) > 0
    ):
        return "二买/三买候选"
    if ind.get("k") is not None and ind["k"] > ind["d"]:
        return "金叉/拐头候选"
    return "结构转强候选"


def categorize(cand: Candidate, cfg: Config) -> str:
    ind = cand.ind
    limit = _limit_pct(cand.code, cfg)
    near_limit = cand.pct_chg is not None and cand.pct_chg >= limit
    extreme_chase = cand.pct_chg is not None and cand.pct_chg > 12
    overdrawn = (
        (ind.get("gain_5d") or 0) > cfg.max_5d_gain
        or (ind.get("gain_10d") or 0) > cfg.max_10d_gain
    )
    j_hot = ind.get("j") is not None and ind["j"] > cfg.kdj_max_j
    if near_limit or extreme_chase or overdrawn or j_hot:
        return "强势但不宜追高"
    return "优先观察"


def build_note(cand: Candidate, cfg: Config) -> str:
    ind = cand.ind
    ma5, ma10 = ind.get("ma5"), ind.get("ma10")
    support = "/".join(f"{v:.2f}" for v in (ma5, ma10) if v is not None)

    if cand.category == "强势但不宜追高":
        risks = []
        if cand.pct_chg is not None and cand.pct_chg >= _limit_pct(cand.code, cfg):
            risks.append("接近涨停，次日容易分歧")
        if (ind.get("gain_10d") or 0) > cfg.max_10d_gain:
            risks.append(f"10日涨幅{ind['gain_10d']:.1f}%透支")
        if (ind.get("gain_5d") or 0) > cfg.max_5d_gain:
            risks.append(f"5日涨幅{ind['gain_5d']:.1f}%偏快")
        if ind.get("j") is not None and ind["j"] > cfg.kdj_max_j:
            risks.append(f"KDJ J值{ind['j']:.0f}偏高")
        handle = f"只等回踩{support or '关键均线'}附近不破再看，不追高"
        return "风险点：" + "；".join(risks[:3]) + f"。处理方式：{handle}。"

    parts = []
    if ind.get("ma20") is not None and cand.close is not None and cand.close > ind["ma20"]:
        if ma5 is not None and ma10 is not None and ma5 > ma10:
            parts.append("站上5/10/20日线")
        else:
            parts.append("站上20日线")
    if ind.get("dif") is not None and ind["dif"] > 0:
        parts.append("MACD在0轴上方")
    if ind.get("k") is not None and ind["k"] > ind["d"]:
        parts.append("KDJ金叉" if ind["k_prev"] <= ind["d_prev"] else "KDJ拐头")
    note = "、".join(parts) + "。" if parts else "结构转强。"
    note += f"次日不追高，等回踩{support or '分时均价线'}附近不破并转强，可短线关注。"
    if ind.get("j") is not None and ind["j"] > 90:
        note += f" J值{ind['j']:.0f}偏高，注意节奏。"
    return note


def screen(
    snapshot_rows: List[Dict],
    kline_map: Dict[str, List[Dict]],
    cfg: Config,
    target_date: str,
    market_verdict: str = "",
) -> Dict:
    """执行完整选股流程，返回分好类的候选。"""
    rows = prefilter(snapshot_rows, cfg)
    priority: List[Candidate] = []
    strong: List[Candidate] = []
    excluded: List[Candidate] = []
    no_kline = 0

    for row in rows:
        code = str(row.get("f12") or "").zfill(6)
        bars = kline_map.get(code)
        if not bars:
            no_kline += 1
            continue
        ind = analyze_bars(bars, cfg)
        if not ind or ind["date"] != target_date:
            no_kline += 1
            continue
        cand = Candidate(
            code=code,
            name=str(row.get("f14") or ""),
            snapshot=row,
            ind=ind,
        )
        ok, reasons = evaluate(cand, cfg)
        cand.reasons = reasons
        if not ok:
            excluded.append(cand)
            continue
        cand.score = score(cand, cfg, market_verdict)
        cand.category = categorize(cand, cfg)
        cand.structure = structure_label(cand, cfg)
        cand.note = build_note(cand, cfg)
        if cand.category == "强势但不宜追高":
            strong.append(cand)
        else:
            priority.append(cand)

    # P0 大盘门控：弱市只保留最高分（候选数量减半，且优先观察上限压缩）
    priority.sort(key=lambda c: c.score, reverse=True)
    if market_verdict == "不适合入场":
        priority = priority[: max(1, cfg.top_n // 2)]
    elif market_verdict == "观望为主":
        priority = priority[: max(1, int(cfg.top_n * 0.75))]
    strong.sort(key=lambda c: c.pct_chg or 0, reverse=True)
    excluded.sort(key=lambda c: c.amount or 0, reverse=True)

    return {
        "priority": priority[: cfg.top_n],
        "strong": strong[: cfg.top_n],
        "excluded": excluded[: 10],
        "stats": {
            "prefiltered": len(rows),
            "analyzed": len(rows) - no_kline,
            "no_kline": no_kline,
        },
    }
