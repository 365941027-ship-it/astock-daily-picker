"""财经快讯抓取与基本面关键词标注（东财主源 + 新浪兜底）。"""

from __future__ import annotations

import json
import urllib.parse
from datetime import date
from typing import Dict, List, Optional

from .config import Config
from .data_fetch import _request


EM_NEWS = "https://np-listapi.eastmoney.com/comm/web/getFastNewsList"
SINA_NEWS = "https://zhibo.sina.com.cn/api/zhibo/feed"

# 行业关键词词库：命中即给新闻打标签
KEYWORDS = {
    "半导体": ["半导体", "芯片", "集成电路", "晶圆", "存储芯片"],
    "新能源": ["新能源", "光伏", "锂电", "电池", "储能", "风电"],
    "人工智能": ["人工智能", "AI", "算力", "大模型", "数据中心", "服务器"],
    "医药": ["医药", "创新药", "疫苗", "生物医药", "CXO"],
    "军工": ["军工", "国防", "航天", "航空", "导弹"],
    "有色": ["有色", "铜", "铝", "锂", "稀土", "黄金", "金属"],
    "地产": ["地产", "房地产", "楼市", "住房"],
    "金融": ["银行", "券商", "保险", "金融", "降息", "降准"],
    "消费": ["消费", "白酒", "食品", "零售", "家电"],
    "汽车": ["汽车", "整车", "智能驾驶", "汽车零部件", "新能源车"],
}


def _parse_em(data: Dict) -> List[Dict]:
    items = data.get("data", {}).get("fastNewsList", []) or []
    out = []
    for it in items:
        title = it.get("title") or ""
        summary = it.get("summary") or ""
        text = title + " " + summary
        tags = [k for k, words in KEYWORDS.items() if any(w.lower() in text.lower() for w in words)]
        out.append({
            "title": title,
            "summary": summary,
            "time": it.get("showTime") or "",
            "tags": tags[:3],
        })
    return out


def _parse_sina(data: Dict) -> List[Dict]:
    feed = data.get("result", {}).get("data", {}).get("feed", {}).get("list", []) or []
    out = []
    for it in feed:
        text = it.get("rich_text") or ""
        title = text[:60] + ("…" if len(text) > 60 else "")
        tags = [k for k, words in KEYWORDS.items() if any(w in text for w in words)]
        out.append({
            "title": title,
            "summary": text,
            "time": it.get("create_time") or "",
            "tags": tags[:3],
        })
    return out


def fetch_news(cfg: Config, limit: int = 20) -> List[Dict]:
    """东财快讯优先，失败回退新浪。"""
    try:
        params = {
            "client": "web", "biz": "web_724", "fastColumn": "102",
            "sortEnd": "", "pageSize": limit, "req_trace": "1",
        }
        url = EM_NEWS + "?" + urllib.parse.urlencode(params)
        data = json.loads(_request(url, cfg))
        items = _parse_em(data)
        if items:
            return items
    except Exception:
        pass
    try:
        params = {"page": 1, "page_size": limit, "zhibo_id": 152, "tag_id": 0, "dire": "f", "dpc": 1}
        url = SINA_NEWS + "?" + urllib.parse.urlencode(params)
        data = json.loads(_request(url, cfg, referer="https://finance.sina.com.cn"))
        return _parse_sina(data)
    except Exception:
        return []


def news_summary(items: List[Dict], limit: int = 20) -> Dict:
    """按标签聚合，输出基本面要点。"""
    tag_count: Dict[str, int] = {}
    for it in items:
        for t in it.get("tags", []):
            tag_count[t] = tag_count.get(t, 0) + 1
    hot_tags = sorted(tag_count.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "date": date.today().isoformat(),
        "total": len(items),
        "hot_tags": [{"tag": t, "count": c} for t, c in hot_tags],
        "items": items[:limit],
    }
