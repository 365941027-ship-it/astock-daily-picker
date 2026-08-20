#!/usr/bin/env python3
"""A股每日选股 · 网页版（本地 Web 服务）。

用法：
    python webapp.py --port 8234

然后在浏览器打开 http://127.0.0.1:8234
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional
from urllib.parse import parse_qs, quote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from daily_picker.cli import _parse_date, next_trading_day, resolve_data_date  # noqa: E402
from daily_picker.config import Config, cn_now  # noqa: E402
from daily_picker.data_fetch import (  # noqa: E402
    DataCache,
    channel_name,
    fetch_kline,
    fetch_sectors,
    get_indices,
    get_klines,
    get_snapshot,
)
from daily_picker.indicators import analyze_bars  # noqa: E402
from daily_picker.chanlun import annotate as chanlun_annotate  # noqa: E402
from daily_picker.userdata import (  # noqa: E402
    add_watch,
    close_position,
    load as load_userdata,
    remove_watch,
    trade as user_trade,
)
from daily_picker.report import HAS_DOCX, build_content, render_docx, render_markdown  # noqa: E402
from daily_picker.replay import run_replay  # noqa: E402
from daily_picker.screening import market_stats, prefilter, screen  # noqa: E402
from daily_picker.verify import run_verify  # noqa: E402
from daily_picker.market_signal import market_signal, watch_sectors  # noqa: E402
from daily_picker.market_signal import INDEX_INFO, index_signal_series  # noqa: E402
from daily_picker.news import fetch_news, news_summary  # noqa: E402


BASE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE, "web")
CACHE = DataCache(os.path.join(BASE, "daily_picker", "cache"))
RESULT_FILE = os.path.join(BASE, "daily_picker", "cache", "last_result.json")
REPLAY_DIR = os.path.join(BASE, "daily_picker", "cache", "replay")
VERIFY_DIR = os.path.join(BASE, "daily_picker", "cache", "verify")
os.makedirs(REPLAY_DIR, exist_ok=True)
os.makedirs(VERIFY_DIR, exist_ok=True)


def _f(v, n: int = 2):
    """JSON 友好的数值：None/非数 -> None，浮点四舍五入。"""
    if v is None:
        return None
    try:
        return round(float(v), n)
    except (TypeError, ValueError):
        return None


def cand_json(c, full: bool = False) -> Dict:
    d = {
        "code": c.code,
        "name": c.name,
        "close": _f(c.close),
        "pct_chg": _f(c.pct_chg),
        "turnover": _f(c.turnover),
        "amount": _f(c.amount, 0),
        "structure": c.structure,
        "score": _f(c.score, 1),
        "category": c.category,
        "note": c.note,
    }
    if full:
        d["reasons"] = c.reasons
    return d


def ind_json(ind: Dict) -> Dict:
    keys = [
        "ma5", "ma10", "ma20", "gain_5d", "gain_10d",
        "dif", "dea", "hist", "k", "d", "j",
    ]
    return {k: _f(ind.get(k), 3) for k in keys}


class Job:
    """单任务运行器：记录日志与结果，供前端轮询。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = "idle"  # idle | running | done | error
        self.params: Dict = {}
        self.logs: List[Dict] = []
        self.result: Optional[Dict] = None
        self.error: Optional[str] = None
        self.started: Optional[str] = None
        self.finished: Optional[str] = None
        self.kind: str = "pick"
        self._restore_from_disk()

    def _restore_from_disk(self):
        """服务重启后从磁盘恢复上次选股结果，避免页面无数据。"""
        try:
            if os.path.exists(RESULT_FILE):
                with open(RESULT_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                if saved and saved.get("state") == "done" and saved.get("result"):
                    self.state = "done"
                    self.result = saved["result"]
                    self.kind = saved.get("kind", "pick")
                    self.finished = saved.get("finished") or "已恢复上次结果"
        except Exception:  # noqa: BLE001 - 恢复失败不阻塞启动
            self.state = "idle"

    def save_to_disk(self):
        """把当前结果落盘，供服务重启后恢复。"""
        try:
            payload = {
                "state": self.state,
                "result": self.result,
                "finished": self.finished,
                "kind": self.kind,
            }
            with open(RESULT_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass

    def log(self, msg: str):
        with self.lock:
            self.logs.append({"t": datetime.now().strftime("%H:%M:%S"), "m": msg})
            if len(self.logs) > 500:
                self.logs = self.logs[-300:]

    def begin(self, params: Dict, kind: str = "pick"):
        with self.lock:
            self.state = "running"
            self.params = params
            self.kind = kind
            self.logs = []
            self.result = None
            self.error = None
            self.started = datetime.now().strftime("%H:%M:%S")
            self.finished = None

    def finish(self, result: Dict):
        with self.lock:
            self.state = "done"
            self.result = result
            self.finished = datetime.now().strftime("%H:%M:%S")
        self.save_to_disk()

    def fail(self, error: str):
        with self.lock:
            self.state = "error"
            self.error = error
            self.finished = datetime.now().strftime("%H:%M:%S")

    def to_dict(self) -> Dict:
        with self.lock:
            return {
                "state": self.state,
                "params": self.params,
                "logs": self.logs[-300:],
                "result": self.result,
                "error": self.error,
                "started": self.started,
                "finished": self.finished,
                "kind": self.kind,
            }


JOB = Job()


def progress_cb(done: int, total: int, failed: int):
    if done == 1 or done == total or done % 25 == 0:
        JOB.log(f"日K获取进度：{done}/{total}（失败 {failed}）")


def run_pipeline(params: Dict):
    JOB.begin(params)
    try:
        JOB.log("开始运行：拉取行情 -> 指标计算 -> 筛选评分 -> 生成报告")
        cfg = Config()
        if params.get("top"):
            cfg.top_n = int(params["top"])
        if params.get("max_workers"):
            cfg.max_workers = int(params["max_workers"])
        if params.get("proxy"):
            cfg.proxy = str(params["proxy"]).strip()
        refresh = bool(params.get("refresh"))
        offline = bool(params.get("offline"))
        allow_intraday = bool(params.get("allow_intraday"))
        run_date = cn_now().date()
        data_date = resolve_data_date(params.get("date") or None)
        JOB.log(f"数据日期：{data_date.isoformat()}")

        snapshot: Optional[List[Dict]] = None
        kline_map: Dict[str, List[Dict]] = {}
        try:
            snapshot = get_snapshot(cfg, CACHE, data_date, offline=offline, refresh=refresh)
            if not snapshot:
                JOB.log("警告：无全市场快照（离线且无缓存？）")
            else:
                JOB.log(f"全市场快照：{len(snapshot)} 只")
            codes = [str(r.get("f12") or "").zfill(6) for r in prefilter(snapshot or [], cfg)]
            JOB.log(f"预筛候选：{len(codes)} 只，开始拉取日K…")
            kline_map, failed = get_klines(
                cfg, CACHE, data_date, codes,
                offline=offline, refresh=refresh, progress=progress_cb,
            )
            if failed:
                JOB.log(f"日K获取失败 {len(failed)} 只（如：{failed[:3]}）")
            if kline_map:
                actual = max(bars[-1]["date"] for bars in kline_map.values())
                if actual != data_date.isoformat():
                    data_date = _parse_date(actual)
                    JOB.log(f"提示：实际最新交易日为 {actual}，报告按该日期生成")
        except Exception as exc:  # noqa: BLE001 - 网络故障降级
            JOB.log(f"行情获取异常，按“空仓观察”处理：{exc}")

        index_data: Dict[str, Dict] = {}
        try:
            index_data = get_indices(cfg, CACHE, data_date, offline=offline)
        except Exception as exc:  # noqa: BLE001
            JOB.log(f"指数获取异常，报告不含市场环境：{exc}")

        target = data_date.isoformat()
        market_verdict = ""
        if snapshot:
            try:
                ms = market_signal(cfg)
                market_verdict = ms.get("verdict", "") if ms.get("ok") else ""
                JOB.log(f"大盘研判：{market_verdict or '不可用'}")
            except Exception:
                pass
            result = screen(snapshot, kline_map, cfg, target, market_verdict=market_verdict)
            if market_verdict == "不适合入场":
                # 大盘不宜入场：不推荐任何个股，仅空仓观察
                result["priority"] = []
                result["strong"] = []
        else:
            result = {
                "priority": [], "strong": [], "excluded": [],
                "stats": {"prefiltered": 0, "analyzed": 0, "no_kline": 0},
            }
        result["snapshot"] = snapshot or []

        intraday = (
            not offline
            and data_date == run_date
            and cn_now().strftime("%H:%M") < cfg.intraday_cutoff
            and not allow_intraday
        )
        observe_date = (
            _parse_date(params["observe_date"])
            if params.get("observe_date")
            else next_trading_day(data_date)
        )
        source_note = "东方财富公开行情接口（新浪日K兜底）+ 本地缓存"
        content = build_content(
            result, index_data, cfg, data_date, run_date, observe_date,
            data_ok=bool(snapshot), intraday=intraday, source_note=source_note,
            market_verdict=market_verdict,
        )

        md_name = f"A股短线候选_{target}.md"
        docx_name = f"A股短线候选_{target}.docx"
        md_path = os.path.join(BASE, md_name)
        docx_path = os.path.join(BASE, docx_name)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(render_markdown(content))
        files = {"md": md_name}
        if HAS_DOCX:
            render_docx(content, docx_path)
            files["docx"] = docx_name
        JOB.log(f"报告已生成：{md_name}" + (f"、{docx_name}" if HAS_DOCX else ""))

        stats = market_stats(snapshot, cfg) if snapshot else {}
        result_json = {
            "data_date": target,
            "observe_date": observe_date.isoformat(),
            "run_date": run_date.isoformat(),
            "intraday": intraday,
            "market_verdict": market_verdict,
            "channel": channel_name(),
            "stats": result["stats"],
            "market": stats,
            "indices": {
                n: {"close": _f(b.get("close")), "pct_chg": _f(b.get("pct_chg")), "amount": _f(b.get("amount"), 0)}
                for n, b in index_data.items()
            },
            "priority": [cand_json(c) for c in result["priority"]],
            "strong": [cand_json(c) for c in result["strong"]],
            "excluded": [cand_json(c, full=True) for c in result["excluded"]],
            "files": files,
            "title": content["title"],
            "intro": content["intro"],
        }
        JOB.log(f"完成：优先观察 {len(result['priority'])} 只，强势不宜追高 {len(result['strong'])} 只")
        JOB.finish(result_json)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        JOB.fail(f"{exc}")


def load_name_map() -> Dict[str, str]:
    """从快照缓存构建 code -> name 映射，供历史回放显示名称。"""
    name_map: Dict[str, str] = {}
    for day in sorted(os.listdir(CACHE.root)):
        if not day.isdigit():
            continue
        try:
            snap = CACHE.load_json(date(int(day[:4]), int(day[4:6]), int(day[6:])), "snapshot.json")
        except Exception:
            snap = None
        if snap:
            for row in snap:
                code = str(row.get("f12") or "").zfill(6)
                if code:
                    name_map[code] = str(row.get("f14") or code)
    return name_map


def build_env_signal(cfg: Config) -> Dict[str, str]:
    """拉取三大指数历史K线，计算每日大盘环境信号（供回放/核对门控）。"""
    bars_by_index: Dict[str, List[Dict]] = {}
    for name, info in INDEX_INFO.items():
        try:
            from daily_picker.data_fetch import _fetch_kline_sina_symbol

            bars = _fetch_kline_sina_symbol(info["symbol"], cfg)
            if bars:
                bars_by_index[name] = bars
        except Exception:
            continue
    if not bars_by_index:
        return {}
    all_dates = sorted({b["date"] for bars in bars_by_index.values() for b in bars})
    return index_signal_series(bars_by_index, all_dates)


def load_all_kline_map() -> Dict[str, List[Dict]]:
    """加载所有已缓存的日K（按最近一个缓存日）。"""
    out: Dict[str, List[Dict]] = {}
    dirs = sorted(d for d in os.listdir(CACHE.root) if d.isdigit())
    if not dirs:
        return out
    # 合并所有缓存日：新目录优先，旧目录补充缺失股票
    seen: Dict[str, List[Dict]] = {}
    for d in reversed(dirs):
        day = date(int(d[:4]), int(d[4:6]), int(d[6:]))
        for name in sorted(os.listdir(os.path.join(CACHE.root, d))):
            if not (name.startswith("kline_") and name.endswith(".json")):
                continue
            code = name[len("kline_") : -len(".json")]
            if code in seen:
                continue
            bars = CACHE.load_json(day, name)
            if bars:
                seen[code] = bars
    out = seen
    return out


def save_replay_day(date_str: str, result: Dict):
    path = os.path.join(REPLAY_DIR, f"{date_str}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)


def replay_pipeline(params: Dict):
    JOB.begin(params, kind="replay")
    try:
        start = params.get("start") or "2026-07-01"
        end = params.get("end") or "2026-08-14"
        JOB.log(f"历史回放开始：{start} ~ {end}")
        cfg = Config()
        if params.get("top"):
            cfg.top_n = int(params["top"])
        kline_map = load_all_kline_map()
        JOB.log(f"加载日K缓存：{len(kline_map)} 只")
        name_map = load_name_map()

        def cb(done: int, total: int):
            if done == total or done % 10 == 0 or done == 1:
                JOB.log(f"回放进度：{done}/{total}")

        env_signal = build_env_signal(cfg)
        dates, results = run_replay(
            kline_map, name_map, cfg, start, end,
            progress=cb, env_signal=env_signal,
        )
        JOB.log(f"回放完成：{len(dates)} 个交易日")
        day_list = []
        total_picks = 0
        for d in dates:
            r = results[d]
            pri = [cand_json(c) for c in r["priority"]]
            strong = [cand_json(c) for c in r["strong"]]
            excl = [cand_json(c, full=True) for c in r["excluded"]]
            payload = {
                "date": d,
                "pool": r["pool"],
                "priority": pri,
                "strong": strong,
                "excluded": excl,
            }
            save_replay_day(d, payload)
            total_picks += len(pri)
            day_list.append(d)
        index = {"start": start, "end": end, "days": day_list, "total_picks": total_picks}
        with open(os.path.join(REPLAY_DIR, "index.json"), "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False)
        JOB.finish({
            "kind": "replay",
            "start": start,
            "end": end,
            "days": day_list,
            "total_picks": total_picks,
        })
        JOB.log(f"完成：{len(dates)} 个交易日，共选出 {total_picks} 只优先观察")
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        JOB.fail(f"{exc}")


def load_replay_index() -> Optional[Dict]:
    path = os.path.join(REPLAY_DIR, "index.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            idx = json.load(f)
    except Exception:
        idx = None
    # 若单日文件比索引多（索引被覆盖/缺失），自动重建完整索引
    day_files = [f[:-5] for f in os.listdir(REPLAY_DIR) if f.endswith(".json") and f != "index.json"]
    if day_files and (not idx or len(day_files) > len(idx.get("days", []))):
        days = sorted(day_files)
        total_picks = 0
        for d in days:
            try:
                with open(os.path.join(REPLAY_DIR, f"{d}.json"), "r", encoding="utf-8") as f:
                    payload = json.load(f)
                total_picks += len(payload.get("priority", []))
            except Exception:
                pass
        idx = {
            "start": days[0],
            "end": days[-1],
            "days": days,
            "total_picks": total_picks,
            "rebuilt": True,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
    return idx


def load_replay_day(date_str: str) -> Optional[Dict]:
    path = os.path.join(REPLAY_DIR, f"{date_str}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def quick_quotes(codes: List[str]) -> Dict[str, Dict]:
    """从本地缓存快速取最新收盘价（不联网）。"""
    out: Dict[str, Dict] = {}
    dirs = sorted(d for d in os.listdir(CACHE.root) if d.isdigit())
    if not dirs:
        return out
    for code in codes:
        # 跨目录查找：最新目录优先
        for d in reversed(dirs):
            try:
                day = date(int(d[:4]), int(d[4:6]), int(d[6:]))
                bars = CACHE.load_json(day, f"kline_{code}.json")
                if bars:
                    last = bars[-1]
                    out[code] = {
                        "code": code,
                        "close": last["close"],
                        "pct_chg": last["pct_chg"],
                        "date": last["date"],
                    }
                    break
            except Exception:
                continue
    return out


def save_verify_day(date_str: str, payload: Dict):
    path = os.path.join(VERIFY_DIR, f"{date_str}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def verify_pipeline(params: Dict):
    JOB.begin(params, kind="verify")
    try:
        start = params.get("start") or "2026-07-01"
        end = params.get("end") or "2026-08-14"
        JOB.log(f"预判核对开始：{start} ~ {end}")
        cfg = Config()
        if params.get("top"):
            cfg.top_n = int(params["top"])
        kline_map = load_all_kline_map()
        name_map = load_name_map()
        JOB.log(f"加载日K缓存：{len(kline_map)} 只")

        def cb(done: int, total: int):
            if total == 0 or done == total or done % 10 == 0:
                JOB.log(f"核对进度：{done}/{total}")

        env_signal = build_env_signal(cfg)
        results = run_verify(
            kline_map, name_map, cfg, start, end,
            progress=cb, env_signal=env_signal,
        )
        days = sorted(results)
        total_valid = sum(results[d]["valid"] for d in days)
        total_failed = sum(results[d]["failed"] for d in days)
        for d in days:
            save_verify_day(d, results[d])
        index = {
            "start": start, "end": end, "days": days,
            "total_valid": total_valid, "total_failed": total_failed,
        }
        with open(os.path.join(VERIFY_DIR, "index.json"), "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False)
        JOB.finish({
            "kind": "verify",
            "days": days,
            "total_valid": total_valid,
            "total_failed": total_failed,
        })
        JOB.log(f"完成：核对 {len(days)} 个交易日，兑现 {total_valid} 条，失效 {total_failed} 条")
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        JOB.fail(f"{exc}")


def load_verify_index() -> Optional[Dict]:
    path = os.path.join(VERIFY_DIR, "index.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_verify_day(date_str: str) -> Optional[Dict]:
    path = os.path.join(VERIFY_DIR, f"{date_str}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def rolling_ma(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            out.append(round(sum(values[i + 1 - period : i + 1]) / period, 2))
    return out


def latest_cache_day() -> Optional[str]:
    if not os.path.isdir(CACHE.root):
        return None
    dirs = sorted(d for d in os.listdir(CACHE.root) if d.isdigit())
    return dirs[-1] if dirs else None


def kline_payload(code: str, day: Optional[str], klt: int = 101) -> Optional[Dict]:
    day = day or latest_cache_day()
    if not day:
        return None
    try:
        if klt != 101:
            cfg = Config()
            bars = fetch_kline(code, cfg, klt=klt)
        else:
            bars = CACHE.load_json(_parse_date(day), f"kline_{code}.json")
    except Exception:
        try:
            cfg = Config()
            bars = fetch_kline(code, cfg, klt=klt)
        except Exception:
            return None
    if not bars:
        return None
    cfg = Config()
    ind = analyze_bars(bars, cfg)
    chan = chanlun_annotate(bars)
    closes = [b["close"] for b in bars]
    ma5, ma10, ma20 = rolling_ma(closes, 5), rolling_ma(closes, 10), rolling_ma(closes, 20)
    start = max(0, len(bars) - 120)
    rows = []
    for i in range(start, len(bars)):
        b = bars[i]
        rows.append({
            "date": b["date"],
            "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"],
            "volume": b["volume"],
            "ma5": ma5[i], "ma10": ma10[i], "ma20": ma20[i],
        })
    return {
        "code": code,
        "date": day,
        "klt": klt,
        "bars": rows,
        "ind": ind_json(ind) if ind else {},
        "chanlun": chan,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ASharePicker/1.0"

    def log_message(self, fmt, *args):  # 静默访问日志
        return

    def _send_json(self, obj, status: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, name: str, ctype: str):
        path = os.path.join(WEB_DIR, name)
        if not os.path.exists(path):
            self._send_json({"error": "资源不存在"}, 404)
            return
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _send_download(self, fname: str):
        path = os.path.join(BASE, fname)
        if not os.path.exists(path):
            self._send_json({"error": f"文件不存在：{fname}"}, 404)
            return
        with open(path, "rb") as f:
            data = f.read()
        ctype = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if fname.endswith(".docx")
            else "text/markdown; charset=utf-8"
        )
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        fname = os.path.basename(path)
        if fname.isascii():
            disp = f'attachment; filename="{fname}"'
        else:
            disp = f"attachment; filename=\"report\"; filename*=UTF-8''{quote(fname)}"
        self.send_header("Content-Disposition", disp)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)
        if path in ("/", "/index.html"):
            self._send_static("index.html", "text/html; charset=utf-8")
        elif path == "/app.js":
            self._send_static("app.js", "text/javascript; charset=utf-8")
        elif path == "/style.css":
            self._send_static("style.css", "text/css; charset=utf-8")
        elif path == "/api/status":
            self._send_json(JOB.to_dict())
        elif path == "/api/config":
            cfg = Config()
            self._send_json({
                "default_date": resolve_data_date(None).isoformat(),
                "default_top": cfg.top_n,
                "has_docx": HAS_DOCX,
                "version": "0.2.0",
            })
        elif path == "/api/kline":
            code = (q.get("code") or [""])[0]
            day = (q.get("date") or [None])[0]
            klt = int((q.get("klt") or ["101"])[0])
            payload = kline_payload(code, day, klt=klt)
            if not payload:
                self._send_json({"error": "无该股票的缓存数据，请先运行选股"}, 404)
            else:
                self._send_json(payload)
        elif path == "/api/sectors":
            try:
                rows = fetch_sectors(Config())
                self._send_json({"rows": rows})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"板块数据获取失败：{exc}"}, 500)
        elif path == "/api/market-signal":
            try:
                self._send_json(market_signal(Config()))
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"缠论研判失败：{exc}"}, 500)
        elif path == "/api/watch-sectors":
            try:
                pick = int((q.get("pick") or ["5"])[0])
                self._send_json({"rows": watch_sectors(Config(), pick=pick)})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"行业筛选失败：{exc}"}, 500)
        elif path == "/api/news":
            try:
                items = fetch_news(Config())
                self._send_json(news_summary(items))
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"新闻获取失败：{exc}"}, 500)
        elif path == "/api/userdata":
            self._send_json(load_userdata())
        elif path == "/api/quotes":
            codes = [c for c in (q.get("codes") or [""])[0].split(",") if c]
            self._send_json(quick_quotes(codes))
        elif path == "/api/watchlist":
            action = (q.get("action") or [""])[0]
            code = (q.get("code") or [""])[0]
            if action == "remove" and code:
                self._send_json(remove_watch(code))
            else:
                self._send_json({"error": "参数错误"}, 400)
        elif path == "/api/replay":
            date_str = (q.get("date") or [""])[0]
            if date_str:
                payload = load_replay_day(date_str)
                if not payload:
                    self._send_json({"error": f"没有 {date_str} 的回放数据，请先启动历史回放"}, 404)
                else:
                    self._send_json(payload)
            else:
                index = load_replay_index()
                if not index:
                    self._send_json({"error": "还没有历史回放数据，请先运行“历史回放”"}, 404)
                else:
                    self._send_json(index)
        elif path == "/api/verify":
            date_str = (q.get("date") or [""])[0]
            if date_str:
                payload = load_verify_day(date_str)
                if not payload:
                    self._send_json({"error": f"没有 {date_str} 的预判核对数据，请先运行“预判核对”"}, 404)
                else:
                    self._send_json(payload)
            else:
                index = load_verify_index()
                if not index:
                    self._send_json({"error": "还没有预判核对数据，请先运行“预判核对”"}, 404)
                else:
                    self._send_json(index)
        elif path == "/api/download":
            ftype = (q.get("type") or ["md"])[0]
            day = (q.get("date") or [""])[0]
            fname = f"A股短线候选_{day}.{'docx' if ftype == 'docx' else 'md'}"
            self._send_download(fname)
        else:
            self._send_json({"error": "Not Found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            params = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            params = {}
        if JOB.state == "running":
            self._send_json({"error": "已有任务在运行，请稍候"}, 409)
            return
        if parsed.path == "/api/run":
            threading.Thread(target=run_pipeline, args=(params,), daemon=True).start()
        elif parsed.path == "/api/replay":
            threading.Thread(target=replay_pipeline, args=(params,), daemon=True).start()
        elif parsed.path == "/api/verify":
            threading.Thread(target=verify_pipeline, args=(params,), daemon=True).start()
        elif parsed.path == "/api/watchlist":
            code = str(params.get("code") or "").zfill(6)
            name = str(params.get("name") or "")
            alert_price = params.get("alert_price")
            alert_type = str(params.get("alert_type") or "below")
            if not code or not name:
                self._send_json({"error": "缺少代码或名称"}, 400)
                return
            self._send_json(add_watch(
                code, name,
                float(alert_price) if alert_price else None,
                alert_type,
            ))
        elif parsed.path == "/api/trade":
            code = str(params.get("code") or "").zfill(6)
            name = str(params.get("name") or "")
            action = str(params.get("action") or "")
            price = params.get("price")
            shares = params.get("shares")
            try:
                price_f = float(price)
                shares_i = int(shares)
            except (TypeError, ValueError):
                self._send_json({"error": "价格/数量格式错误"}, 400)
                return
            self._send_json(user_trade(code, name, action, price_f, shares_i))
        elif parsed.path == "/api/close":
            code = str(params.get("code") or "").zfill(6)
            self._send_json(close_position(code))
        elif parsed.path == "/api/subscribe":
            # 预留订阅消息登记：真实上线需小程序登录后传 openid + 模板ID
            self._send_json({"ok": True, "msg": "订阅登记成功（预留，正式发送需配置模板ID与access_token）"})
        else:
            self._send_json({"error": "Not Found"}, 404)
            return
        self._send_json({"ok": True})


def main() -> int:
    parser = argparse.ArgumentParser(description="A股每日选股网页版")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8234)
    args = parser.parse_args()
    print(f"A股每日选股 Web 版启动：http://{args.host}:{args.port}")
    print(f"默认数据日期：{resolve_data_date(None).isoformat()}（可关掉浏览器后 Ctrl+C 停止服务）")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
