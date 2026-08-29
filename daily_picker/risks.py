"""个股事件排雷：抓取东财公告，按关键词分类为“一票否决”或“风险提示”。

数据源：东方财富公告公开接口（https://np-anotice-stock.eastmoney.com）。
只用于个人研究，请遵守数据源使用条款。

设计原则：
- 只对“当前候选/持仓”的股票做实时抓取（不扫全市场）；
- 历史回放不套用“今天的公告”去判历史（避免未来函数），仅当日选股生效；
- 抓取失败时降级为“不排雷”，不阻塞主流程。
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Dict, List, Optional

from .config import Config
from .data_fetch import _request


EM_ANN = "https://np-anotice-stock.eastmoney.com/api/security/ann"

# 风险规则：(分类, 关键词列表, 是否一票否决, 展示说明)
RISK_RULES = [
    ("立案调查", ["立案调查", "立案侦查", "被立案", "中国证监会立案", "监管立案"], True, "立案调查，一票否决"),
    ("处罚/警示", ["行政处罚", "罚款", "警示函", "监管措施", "公开谴责", "市场禁入"], True, "收到处罚/警示函，一票否决"),
    ("业绩暴雷", ["业绩预亏", "预亏", "业绩预减", "预减", "大幅下滑", "亏损金额", "业绩预告修正", "由盈转亏"], True, "业绩预亏/预减，一票否决"),
    ("退市风险", ["退市风险警示", "可能被实施退市", "终止上市风险", "退市整理"], True, "退市风险警示，一票否决"),
    ("重组终止", ["终止重组", "终止筹划", "终止发行", "重组失败", "终止收购"], True, "重组终止，一票否决"),
    ("商誉减值", ["商誉减值", "计提减值", "资产减值损失"], True, "大额减值，一票否决"),
    ("减持计划", ["拟减持", "减持计划", "减持股份的预披露", "减持不超过", "减持预披露"], True, "股东拟减持，一票否决"),
    ("解禁", ["解除限售", "限售股上市", "限售股份上市流通", "首发原股东限售", "解禁"], False, "限售解禁临近，注意抛压"),
    ("质押", ["质押比例", "质押率", "股份被质押", "质押股份"], False, "大股东质押，注意风险"),
    ("问询/关注", ["问询函", "关注函", "监管问询", "年报问询"], False, "收到问询/关注函，注意"),
    ("诉讼/冻结", ["诉讼", "仲裁", "冻结", "财产保全", "失信被执行"], False, "涉诉/冻结，注意"),
    ("高管变动", ["辞职", "离任", "离职"], False, "高管辞职/离任，注意"),
]

# 先命中即生效；普通公告关键词（股东大会、分配预案等）不在列表内，不算风险。


def _norm(title: str) -> str:
    return re.sub(r"[\s:：()（）\"']", "", title or "")


def classify(title: str) -> Optional[Dict]:
    """对公告标题做关键词分类，返回风险事件字典；无风险返回 None。"""
    t = _norm(title)
    for cat, words, veto, note in RISK_RULES:
        if any(w in t for w in words):
            return {
                "category": cat,
                "title": title,
                "veto": veto,
                "note": note,
            }
    return None


def fetch_stock_announcements(code: str, cfg: Config, page_size: int = 30) -> List[Dict]:
    """抓取单只股票最近公告（默认 30 条，约覆盖近 1-2 周）。"""
    code = str(code).zfill(6)
    params = {
        "sr": -1,
        "page_size": page_size,
        "page_index": 1,
        "ann_type": "A",
        "client_source": "web",
        "stock_list": code,
        "f_node": 0,
        "s_node": 0,
    }
    url = EM_ANN + "?" + urllib.parse.urlencode(params)
    data = json.loads(_request(url, cfg))
    items = data.get("data", {}).get("list", []) or []
    out = []
    seen_cat: set[str] = set()
    for it in items:
        title = it.get("title") or ""
        if not title:
            continue
        risk = classify(title)
        if risk:
            cat = risk["category"]
            if cat in seen_cat:
                continue  # 同类别只保留最新一条，避免刷屏
            seen_cat.add(cat)
            risk["code"] = code
            risk["date"] = (it.get("notice_date") or it.get("display_time") or "")[:10]
            out.append(risk)
    return out


def fetch_risk_map(codes: List[str], cfg: Config, max_workers: int = 6) -> Dict[str, List[Dict]]:
    """并发抓取多只股票的风险事件，返回 {code: [risk, ...]}。"""
    out: Dict[str, List[Dict]] = {}
    if not codes:
        return out
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(fetch_stock_announcements, c, cfg): c for c in codes}
        for fut in as_completed(futs):
            code = futs[fut]
            try:
                risks = fut.result()
                if risks:
                    out[code] = risks
            except Exception:
                continue  # 单只失败降级，不阻塞
    return out


def risk_verdict(risks: List[Dict]) -> tuple[bool, List[str]]:
    """判断一组风险事件是否一票否决。返回 (是否否决, 原因列表)。"""
    veto = [r["note"] for r in risks if r.get("veto")]
    return (bool(veto), veto)


def risk_warnings(risks: List[Dict]) -> List[str]:
    return [r["note"] for r in risks if not r.get("veto")]


# ---- 本地缓存（供静态站点复用，避免每次重建重复抓取） ----
def _cache_path() -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "daily_picker", "cache", "risks.json")


def load_risk_cache() -> Dict[str, List[Dict]]:
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_risk_cache(risk_map: Dict[str, List[Dict]]) -> None:
    try:
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump(risk_map, f, ensure_ascii=False)
    except Exception:
        pass
