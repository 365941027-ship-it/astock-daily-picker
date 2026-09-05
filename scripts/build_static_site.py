#!/usr/bin/env python3
"""把《A股每日选股》的本地动态网页导出为 GitHub Pages 可托管的静态站点。

输出目录：site/
  - index.html / style.css / app.js   （从 web/ 复制，并注入 STATIC_MODE）
  - data/config.json                  基础配置与更新时间
  - data/latest.json                  最近一个交易日的选股结果
  - data/replay.json + data/replay/*.json      历史回放（索引 + 单日）
  - data/verify.json + data/verify/*.json      预判核对（索引 + 单日）
  - data/market.json                  缠论大盘研判
  - data/watch_sectors.json           观察行业
  - data/news.json                    财经新闻
  - data/sectors.json                 板块行情
  - data/kline/<code>_101.json        候选个股日K（含指标与缠论标注）
  - data/quotes.json                  候选个股最新收盘快照
  - downloads/*                       最近一日报告（md/docx，若存在）

用法：
    python scripts/build_static_site.py [--proxy socks5h://127.0.0.1:7897]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import webapp  # noqa: E402  (复用同一套缓存读取/指标/K线逻辑)
from daily_picker.cli import next_trading_day  # noqa: E402
from daily_picker.config import Config, cn_now  # noqa: E402
from daily_picker.data_fetch import fetch_sectors  # noqa: E402
from daily_picker.market_signal import market_signal, watch_sectors  # noqa: E402
from daily_picker.news import fetch_news, news_summary  # noqa: E402
from daily_picker.risks import (  # noqa: E402
    fetch_risk_map,
    load_risk_cache,
    save_risk_cache,
)

ROOT = BASE
WEB_DIR = os.path.join(ROOT, "web")
SITE_DIR = os.path.join(ROOT, "site")
DATA_DIR = os.path.join(SITE_DIR, "data")
REPLAY_OUT = os.path.join(DATA_DIR, "replay")
VERIFY_OUT = os.path.join(DATA_DIR, "verify")
KLINE_OUT = os.path.join(DATA_DIR, "kline")
DOWNLOADS_OUT = os.path.join(SITE_DIR, "downloads")

VERSION = "0.3.0-static"


def _write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def _copy_static_assets() -> None:
    """复制前端三件套，并在 site/index.html 中注入 STATIC_MODE。"""
    os.makedirs(SITE_DIR, exist_ok=True)
    shutil.copy(os.path.join(WEB_DIR, "style.css"), os.path.join(SITE_DIR, "style.css"))
    shutil.copy(os.path.join(WEB_DIR, "app.js"), os.path.join(SITE_DIR, "app.js"))

    with open(os.path.join(WEB_DIR, "index.html"), "r", encoding="utf-8") as f:
        html = f.read()
    # 更新缓存版本号，避免 GitHub Pages 上的浏览器继续用旧 JS/CSS
    html = html.replace('style.css?v=13', 'style.css?v=15')
    html = html.replace('app.js?v=13', 'app.js?v=15')
    # 在 app.js 引入前注入静态模式标记
    if "window.STATIC_MODE" not in html:
        html = html.replace(
            '  <script src="app.js?v=15"></script>',
            '  <script>window.STATIC_MODE = true;</script>\n  <script src="app.js?v=15"></script>',
            1,
        )
    with open(os.path.join(SITE_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    # 防止 GitHub Pages 用 Jekyll 处理，确保 JSON 直接可访问
    open(os.path.join(SITE_DIR, ".nojekyll"), "w").close()


def _collect_symbols() -> list[str]:
    codes: set[str] = set()
    idx = webapp.load_replay_index() or {}
    for day in idx.get("days", []):
        payload = webapp.load_replay_day(day) or {}
        for c in payload.get("priority", []) + payload.get("strong", []) + payload.get("excluded", []):
            code = str(c.get("code") or "").zfill(6)
            if code:
                codes.add(code)
    vidx = webapp.load_verify_index() or {}
    for day in vidx.get("days", []):
        payload = webapp.load_verify_day(day) or {}
        for e in payload.get("entries", []):
            code = str(e.get("code") or "").zfill(6)
            if code:
                codes.add(code)
    return sorted(codes)


_CACHE_DIRS: list[str] | None = None


def _latest_cache_day_for(code: str) -> str | None:
    global _CACHE_DIRS
    if _CACHE_DIRS is None:
        if not os.path.isdir(webapp.CACHE.root):
            return None
        _CACHE_DIRS = sorted(d for d in os.listdir(webapp.CACHE.root) if d.isdigit())
    for d in reversed(_CACHE_DIRS):
        if d.isdigit() and os.path.exists(os.path.join(webapp.CACHE.root, d, f"kline_{code}.json")):
            return d
    return None


def _build_kline(codes: list[str]) -> None:
    os.makedirs(KLINE_OUT, exist_ok=True)
    written = 0

    def export_one(code: str) -> bool:
        day = _latest_cache_day_for(code)
        if not day:
            return False
        payload = webapp.kline_payload(code, day, klt=101)
        if payload:
            _write_json(os.path.join(KLINE_OUT, f"{code}_101.json"), payload)
            return True
        return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(export_one, code): code for code in codes}
        for i, fut in enumerate(as_completed(futures), 1):
            if fut.result():
                written += 1
            if i % 50 == 0:
                print(f"[K线] 进度 {i}/{len(codes)}")
    print(f"[K线] 导出 {written}/{len(codes)} 只个股日K（含指标+缠论标注）")


def _build_core(cfg: Config) -> None:
    # ---- 回放 ----
    rindex = webapp.load_replay_index() or {"days": []}
    _write_json(os.path.join(DATA_DIR, "replay.json"), rindex)
    for day in rindex.get("days", []):
        payload = webapp.load_replay_day(day)
        if payload:
            _write_json(os.path.join(REPLAY_OUT, f"{day}.json"), payload)
    print(f"[回放] 导出 {len(rindex.get('days', []))} 个交易日")

    # ---- 核对 ----
    vindex = webapp.load_verify_index() or {"days": []}
    _write_json(os.path.join(DATA_DIR, "verify.json"), vindex)
    for day in vindex.get("days", []):
        payload = webapp.load_verify_day(day)
        if payload:
            _write_json(os.path.join(VERIFY_OUT, f"{day}.json"), payload)
    print(f"[核对] 导出 {len(vindex.get('days', []))} 个核对日")

    # ---- 最新选股结果（优先取 last_result.json：含事件排雷与当日候选；否则退回放） ----
    latest_day = rindex["days"][-1] if rindex.get("days") else ""
    last_result = None
    try:
        with open(webapp.RESULT_FILE, encoding="utf-8") as f:
            last_result = json.load(f).get("result") or {}
    except Exception:
        pass
    if last_result and last_result.get("data_date") and last_result["data_date"] >= latest_day:
        payload = {
            "pool": len(last_result.get("priority", [])) + len(last_result.get("strong", [])),
            "priority": last_result.get("priority", []),
            "strong": last_result.get("strong", []),
            "excluded": last_result.get("excluded", []),
            "risk_rejected": last_result.get("risk_rejected", []),
        }
        latest_day = last_result["data_date"]
        # 当日候选已带 risks；无则回退全量 risks.json
        has_risk_field = any(c.get("risks") for c in payload["priority"] + payload["strong"])
        if not has_risk_field:
            risk_cache = load_risk_cache()
            for c in payload["priority"] + payload["strong"]:
                c["risks"] = [r.get("note") for r in risk_cache.get(str(c["code"]).zfill(6), [])]
    else:
        payload = webapp.load_replay_day(latest_day) or {}
    sig = market_signal(cfg)
    verdict = sig.get("verdict", "") if sig.get("ok") else ""
    indices = {}
    for i in sig.get("indices", []):
        if i.get("name"):
            indices[i["name"]] = {"close": i.get("close"), "pct_chg": i.get("pct_chg")}
    files = {}
    for ext, key in (("md", "md"), ("docx", "docx")):
        src = os.path.join(ROOT, f"A股短线候选_{latest_day}.{ext}")
        if os.path.exists(src):
            shutil.copy(src, os.path.join(DOWNLOADS_OUT, os.path.basename(src)))
            files[key] = f"downloads/{os.path.basename(src)}"
    latest = {
        "data_date": latest_day,
        "observe_date": next_trading_day(__import__("datetime").date.fromisoformat(latest_day)).isoformat()
        if latest_day else "",
        "channel": "GitHub 静态版（盘后自动更新）",
        "pool": payload.get("pool", 0),
        "priority": payload.get("priority", []),
        "strong": payload.get("strong", []),
        "excluded": payload.get("excluded", []),
        "risk_rejected": payload.get("risk_rejected", []),
        "market_verdict": verdict,
        "indices": indices,
        "files": files,
    }
    _write_json(os.path.join(DATA_DIR, "latest.json"), latest)
    print(f"[最新] 导出 {latest_day}，优先观察 {len(latest['priority'])} 只，大盘门控：{verdict or '无'}")

    # ---- 缠论研判 / 观察行业 / 新闻 / 板块 ----
    _write_json(os.path.join(DATA_DIR, "market.json"), sig if sig.get("ok") else {"error": sig.get("error", "指数数据不可用")})
    _write_json(os.path.join(DATA_DIR, "watch_sectors.json"), {"rows": watch_sectors(cfg, pick=5)})
    try:
        news = news_summary(fetch_news(cfg))
    except Exception as exc:  # noqa: BLE001
        news = {"items": [], "hot_tags": [], "error": str(exc)}
    _write_json(os.path.join(DATA_DIR, "news.json"), news)
    try:
        sectors = {"rows": fetch_sectors(cfg)}
    except Exception as exc:  # noqa: BLE001
        sectors = {"rows": [], "error": str(exc)}
    _write_json(os.path.join(DATA_DIR, "sectors.json"), sectors)

    # ---- 事件排雷：抓取全部候选风险公告并缓存 ----
    try:
        risk_codes = _collect_symbols()
        risk_map = fetch_risk_map(risk_codes, cfg)
        save_risk_cache(risk_map)
        _write_json(os.path.join(DATA_DIR, "risks.json"), risk_map)
        veto_codes = {c for c, rs in risk_map.items() if any(r.get("veto") for r in rs)}
        print(f"[排雷] 检查 {len(risk_codes)} 只 → 命中风险 {len(risk_map)} 只，其中一票否决 {len(veto_codes)} 只")
    except Exception as exc:  # noqa: BLE001
        print(f"[排雷] 抓取失败（使用缓存）：{exc}")
        risk_map = load_risk_cache()
        _write_json(os.path.join(DATA_DIR, "risks.json"), risk_map)


def _build_quotes(codes: list[str]) -> None:
    quotes = webapp.quick_quotes(codes)
    _write_json(os.path.join(DATA_DIR, "quotes.json"), quotes)
    print(f"[行情] 导出 {len(quotes)} 只个股收盘快照")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 GitHub Pages 静态站点")
    parser.add_argument("--proxy", default="", help="行情代理，如 socks5h://127.0.0.1:7897")
    args = parser.parse_args()

    cfg = Config()
    if args.proxy:
        cfg.proxy = args.proxy

    for d in (SITE_DIR, DATA_DIR, REPLAY_OUT, VERIFY_OUT, KLINE_OUT, DOWNLOADS_OUT):
        os.makedirs(d, exist_ok=True)

    print("== 生成静态站点 ==")
    _copy_static_assets()
    _build_core(cfg)
    codes = _collect_symbols()
    _build_kline(codes)
    _build_quotes(codes)

    _write_json(os.path.join(DATA_DIR, "config.json"), {
        "default_date": (webapp.load_replay_index() or {}).get("days", [None])[-1],
        "default_top": cfg.top_n,
        "has_docx": False,
        "version": VERSION,
        "static": True,
        "updated_at": cn_now().isoformat(timespec="seconds"),
        # 访问码（轻量防误入；公网源码可见，不是强安全）。用环境变量 ASTOCK_PIN 覆盖，空则关闭。
        "pin": os.environ.get("ASTOCK_PIN", "").strip() or "2580",
    })

    size = sum(
        os.path.getsize(os.path.join(r, f))
        for r, _, fs in os.walk(SITE_DIR)
        for f in fs
    )
    print(f"== 完成：{SITE_DIR}，共 {size / 1024 / 1024:.1f} MB ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
