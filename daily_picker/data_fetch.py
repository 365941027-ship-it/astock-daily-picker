"""行情数据抓取：东方财富公开接口（主）+ 新浪日K（备），带本地缓存。

数据源均为公开免登录接口，仅用于个人研究，请遵守数据源的使用条款。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Callable, Dict, List, Optional

from .config import Config


EM_CLIST = "https://push2.eastmoney.com/api/qt/clist/get"
EM_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EM_SECTOR = "https://push2.eastmoney.com/api/qt/clist/get"
EM_ULIST = "https://push2.eastmoney.com/api/qt/ulist.np/get"
SINA_KLINE = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/"
    "CN_MarketDataService.getKLineData"
)
SINA_SNAPSHOT_COUNT = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeStockCount?node=hs_a"
)
SINA_SNAPSHOT = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData?page={page}&num=100&sort=changepercent&asc=0"
    "&node=hs_a&symbol=&_s_r_a=init"
)
SINA_SECTOR = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
SINA_REFERER = "https://finance.sina.com.cn"

INDEX_SECIDS = {
    "上证指数": "1.000001",
    "深证成指": "0.399001",
    "创业板指": "0.399006",
}


def secid_of(code: str) -> str:
    """东方财富 secid：6/9 开头是沪市(1)，其余为深市(0)。"""
    code = str(code).zfill(6)
    return f"1.{code}" if code[0] in "69" else f"0.{code}"


def fetch_quotes_realtime(codes: List[str], cfg: Config) -> Dict[str, Dict]:
    """批量拉实时行情（东财 ulist），返回 {code: {...}}。

    字段：f2最新价 f3涨跌幅 f15最高 f16最低 f17今开 f18昨收 f6成交额。
    非交易时段返回最近收盘快照值，可作盯盘参考。
    """
    out: Dict[str, Dict] = {}
    codes = [str(c).zfill(6) for c in codes if str(c).zfill(6)]
    if not codes:
        return out
    # 单次最多约 60 只，超过分批
    for i in range(0, len(codes), 60):
        batch = codes[i : i + 60]
        params = {
            "fltt": 2,
            "secids": ",".join(secid_of(c) for c in batch),
            "fields": "f2,f3,f5,f6,f12,f14,f15,f16,f17,f18",
        }
        url = EM_ULIST + "?" + urllib.parse.urlencode(params)
        try:
            data = json.loads(_request(url, cfg))
        except Exception:
            continue
        rows = (data.get("data") or {}).get("diff") or []
        for r in rows:
            code = str(r.get("f12") or "").zfill(6)
            if not code:
                continue
            out[code] = {
                "code": code,
                "name": r.get("f14") or code,
                "price": _num(r.get("f2")),
                "pct_chg": _num(r.get("f3")),
                "high": _num(r.get("f15")),
                "low": _num(r.get("f16")),
                "open": _num(r.get("f17")),
                "prev_close": _num(r.get("f18")),
                "amount": _num(r.get("f6")),
            }
    return out


def _num(v) -> Optional[float]:
    try:
        if v is None or v == "-":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _urllib_text(
    url: str,
    cfg: Config,
    proxy: Optional[str] = None,
    referer: Optional[str] = None,
) -> str:
    headers = {"User-Agent": cfg.user_agent, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers=headers)
    last_err: Optional[Exception] = None
    for attempt in range(cfg.retries):
        try:
            with opener.open(req, timeout=cfg.timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - 统一重试
            last_err = exc
            if attempt < cfg.retries - 1:
                time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"urllib 请求失败: {last_err}")


def _curl_text(
    url: str,
    cfg: Config,
    proxy: Optional[str] = None,
    referer: Optional[str] = None,
) -> str:
    if not shutil.which("curl"):
        raise RuntimeError("未找到 curl 可执行文件")
    cmd = ["curl", "-s", "-m", str(cfg.timeout), "-A", cfg.user_agent]
    if proxy:
        cmd += ["-x", proxy]
    if referer:
        cmd += ["-H", f"Referer: {referer}"]
    cmd.append(url)
    last_err: Optional[Exception] = None
    for attempt in range(cfg.retries):
        try:
            out = subprocess.run(
                cmd, capture_output=True, timeout=cfg.timeout + 5, check=False
            )
            if out.returncode == 0 and out.stdout:
                return out.stdout.decode("utf-8", errors="replace")
            last_err = RuntimeError(
                f"curl 退出码 {out.returncode}: "
                f"{out.stderr.decode('utf-8', errors='replace')[:200]}"
            )
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        if attempt < cfg.retries - 1:
            time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"curl 请求失败: {last_err}")


def _to_socks5(url: Optional[str]) -> Optional[str]:
    """把 getproxies() 里的 socks 地址归一化为 curl 可用的 socks5h。"""
    if not url:
        return None
    if url.startswith(("socks5://", "socks5h://")):
        return url
    if url.startswith("socks://"):
        return "socks5h://" + url[len("socks://") :]
    if url.startswith(("http://", "https://")):
        return "socks5h://" + url.split("://", 1)[1]
    return "socks5h://" + url


_channel: Optional[Dict] = None
_channel_lock = threading.Lock()


def _em_kline_probe_url(cfg: Config) -> str:
    params = {
        "secid": "1.600000", "klt": 101, "fqt": cfg.fqt, "lmt": 2,
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    return EM_KLINE + "?" + urllib.parse.urlencode(params)


def _sina_kline_probe_url() -> str:
    return SINA_KLINE + "?symbol=sh600000&scale=240&ma=no&datalen=3"


def _probe_channel(cfg: Config) -> Dict:
    """按优先级探测可用网络通道，返回首个能取到行情的通道。"""
    proxy = cfg.proxy.strip() or None
    candidates: List[Dict] = []
    if proxy:
        if proxy.startswith(("socks", "socks5", "socks5h")):
            candidates.append({"name": "curl(SOCKS代理)", "engine": "curl", "proxy": proxy})
        else:
            candidates.append({"name": "urllib(指定代理)", "engine": "urllib", "proxy": proxy})
            candidates.append({"name": "curl(指定代理)", "engine": "curl", "proxy": proxy})
    else:
        candidates.append({"name": "urllib(系统代理)", "engine": "urllib", "proxy": None})
        if shutil.which("curl"):
            candidates.append({"name": "curl(直连)", "engine": "curl", "proxy": None})
            sys_proxies = urllib.request.getproxies()
            hp = sys_proxies.get("https") or sys_proxies.get("http")
            if hp:
                candidates.append({"name": "curl(系统HTTP代理)", "engine": "curl", "proxy": hp})
            sp = _to_socks5(sys_proxies.get("socks"))
            if sp:
                candidates.append({"name": "curl(系统SOCKS代理)", "engine": "curl", "proxy": sp})
    # 最后兜底：直连（代理临时抖动时仍有机会）
    if shutil.which("curl") and not any(c["engine"] == "curl" and c["proxy"] is None for c in candidates):
        candidates.append({"name": "curl(直连兜底)", "engine": "curl", "proxy": None})

    saved_timeout = cfg.timeout
    saved_retries = cfg.retries
    cfg.timeout = cfg.probe_timeout
    cfg.retries = 2
    errors = []
    try:
        for cand in candidates:
            ok_sina = ok_em = False
            for url, referer, key in [
                (_sina_kline_probe_url(), SINA_REFERER, "sina"),
                (_em_kline_probe_url(cfg), None, "eastmoney"),
            ]:
                try:
                    if cand["engine"] == "urllib":
                        text = _urllib_text(url, cfg, proxy=cand["proxy"], referer=referer)
                    else:
                        text = _curl_text(url, cfg, proxy=cand["proxy"], referer=referer)
                    if text.strip():
                        if key == "sina":
                            ok_sina = True
                        else:
                            ok_em = True
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{cand['name']}/{key}: {exc}")
            if ok_sina or ok_em:
                return {**cand, "eastmoney": ok_em, "sina": ok_sina}
            time.sleep(0.3)
    finally:
        cfg.timeout = saved_timeout
        cfg.retries = saved_retries
    raise RuntimeError(
        "所有网络通道均无法访问行情接口。若本机有代理，请用 "
        "--proxy 指定，例如 --proxy socks5h://127.0.0.1:7897。"
        + ("　| ".join(errors))
    )


def _get_channel(cfg: Config) -> Dict:
    global _channel
    if _channel is None:
        with _channel_lock:
            if _channel is None:
                _channel = _probe_channel(cfg)
    return _channel


def channel_name() -> str:
    return _channel["name"] if _channel else "未探测"


def _request(url: str, cfg: Config, referer: Optional[str] = None) -> str:
    channel = _get_channel(cfg)
    if channel["engine"] == "curl":
        return _curl_text(url, cfg, proxy=channel["proxy"], referer=referer)
    try:
        return _urllib_text(url, cfg, proxy=channel["proxy"], referer=referer)
    except Exception:
        # 部分东财接口对 urllib 的 TLS 指纹不友好，失败时自动用 curl 同代理重试
        if shutil.which("curl"):
            return _curl_text(url, cfg, proxy=channel["proxy"], referer=referer)
        raise


def _get_json(url: str, cfg: Config, referer: Optional[str] = None):
    return json.loads(_request(url, cfg, referer=referer))


def parse_kline_line(line: str) -> Dict:
    """解析东方财富日K行：date,open,close,high,low,volume,amount,amplitude,pct,change,turnover"""
    parts = line.split(",")
    if len(parts) < 11:
        raise ValueError(f"日K数据格式异常: {line}")
    return {
        "date": parts[0],
        "open": float(parts[1]),
        "close": float(parts[2]),
        "high": float(parts[3]),
        "low": float(parts[4]),
        "volume": float(parts[5]),
        "amount": float(parts[6]),
        "amplitude": float(parts[7]),
        "pct_chg": float(parts[8]),
        "change": float(parts[9]),
        "turnover": float(parts[10]),
        "source": "eastmoney",
    }


def fetch_snapshot(cfg: Config) -> List[Dict]:
    """拉取全市场股票快照：东财优先，失败时回退到新浪。"""
    channel = _get_channel(cfg)
    if channel.get("eastmoney"):
        try:
            rows = _fetch_snapshot_eastmoney(cfg)
            if rows:
                return rows
        except Exception:
            pass
    return _fetch_snapshot_sina(cfg)


def fetch_sectors(cfg: Config, top_n: int = 30) -> List[Dict]:
    """行业板块热度榜（东方财富，按涨跌幅排序）。"""
    try:
        params = {
            "pn": 1, "pz": top_n, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f3", "fs": "m:90+t:2+f:!50",
            "fields": "f2,f3,f4,f8,f12,f14,f104,f105,f128,f140",
        }
        url = EM_SECTOR + "?" + urllib.parse.urlencode(params)
        text = _request(url, cfg)
        data = json.loads(text).get("data") or {}
        diff = data.get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        rows = []
        for r in diff:
            rows.append({
                "code": r.get("f12"),
                "name": r.get("f14"),
                "pct_chg": _safe_float(r.get("f3")),
                "leader": r.get("f140") or "",
                "leader_pct": _safe_float(r.get("f128") or r.get("f104")),
            })
        if rows:
            return rows
    except Exception:
        pass
    return _fetch_sectors_sina(cfg, top_n)


def _fetch_sectors_sina(cfg: Config, top_n: int) -> List[Dict]:
    """新浪行业板块兜底（GBK 文本）。"""
    # 该接口为 GBK 编码，必须拿原始字节再解码，不能走 _request 的 UTF-8 解码
    channel = _get_channel(cfg)
    if channel["engine"] == "curl":
        cmd = ["curl", "-s", "-m", str(cfg.timeout), "-A", cfg.user_agent,
               "-H", "Referer: https://finance.sina.com.cn"]
        if channel["proxy"]:
            cmd += ["-x", channel["proxy"]]
        cmd.append(SINA_SECTOR)
        out = subprocess.run(cmd, capture_output=True, timeout=cfg.timeout + 5, check=False)
        raw_bytes = out.stdout
    else:
        try:
            req = urllib.request.Request(SINA_SECTOR, headers={
                "User-Agent": cfg.user_agent,
                "Referer": "https://finance.sina.com.cn",
            })
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": channel["proxy"], "https": channel["proxy"]})
            ) if channel["proxy"] else urllib.request.build_opener()
            with opener.open(req, timeout=cfg.timeout) as resp:
                raw_bytes = resp.read()
        except Exception as exc:
            raise RuntimeError(f"新浪板块请求失败: {exc}")
    text = raw_bytes.decode("gbk", errors="replace")
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return []
    body = text[start : end + 1]
    data = json.loads(body)
    rows = []
    for code, line in data.items():
        parts = line.split(",")
        if len(parts) < 12:
            continue
        try:
            pct = float(parts[4])
        except (ValueError, IndexError):
            pct = 0.0
        rows.append({
            "code": code,
            "name": parts[1],
            "pct_chg": pct,
            "leader": parts[12] if len(parts) > 12 else "",
            "leader_code": parts[8] if len(parts) > 8 else "",
            "leader_pct": _safe_float(parts[9]) if len(parts) > 9 else None,
        })
    rows.sort(key=lambda r: r["pct_chg"] or 0, reverse=True)
    return rows[:top_n]


def _safe_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fetch_snapshot_eastmoney(cfg: Config) -> List[Dict]:
    rows: List[Dict] = []
    total = None
    pn = 1
    while True:
        params = {
            "pn": pn, "pz": 200, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f3", "fs": cfg.fs_filter,
            "fields": "f2,f3,f5,f6,f8,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23",
        }
        url = EM_CLIST + "?" + urllib.parse.urlencode(params)
        data = (_get_json(url, cfg).get("data") or {})
        total = data.get("total") if data.get("total") else total
        diff = data.get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        rows.extend(diff)
        if not diff or (total is not None and len(rows) >= total):
            break
        pn += 1
        time.sleep(0.05)
    return rows


def _fetch_snapshot_sina(cfg: Config) -> List[Dict]:
    """新浪全市场快照（node=hs_a，含北交所，需自行过滤）。"""
    count_text = _request(SINA_SNAPSHOT_COUNT, cfg, referer=SINA_REFERER)
    total = int(json.loads(count_text))
    rows: List[Dict] = []
    page = 1
    while len(rows) < total:
        url = SINA_SNAPSHOT.format(page=page)
        text = _request(url, cfg, referer=SINA_REFERER)
        arr = json.loads(text)
        if not arr:
            break
        for r in arr:
            symbol = r.get("symbol") or ""
            if symbol.startswith("bj"):
                continue
            rows.append({
                "f2": float(r.get("trade") or 0),
                "f3": float(r.get("changepercent") or 0),
                "f5": r.get("volume"),
                "f6": float(r.get("amount") or 0),
                "f8": float(r.get("turnoverratio") or 0),
                "f10": None,
                "f12": r.get("code"),
                "f14": r.get("name"),
                "f15": float(r.get("high") or 0),
                "f16": float(r.get("low") or 0),
                "f17": float(r.get("open") or 0),
                "f18": float(r.get("settlement") or 0),
                "f20": r.get("mktcap"),
                "f21": r.get("nmc"),
                "f23": r.get("pb"),
            })
        page += 1
        time.sleep(0.05)
    return rows


def fetch_kline(code: str, cfg: Config, use_sina_fallback: bool = True, klt: int = 101) -> List[Dict]:
    """拉取单只股票K线（klt=101 日K / 60 60分钟 / 30 30分钟），东财失败回退新浪。"""
    channel = _get_channel(cfg)
    if channel.get("eastmoney"):
        try:
            return _fetch_kline_eastmoney(code, cfg, klt=klt)
        except Exception:
            if not use_sina_fallback:
                raise
            if klt != 101:
                raise RuntimeError(f"东财 {klt}分钟K线不可用且新浪不支持分钟K线")
    return _fetch_kline_sina(code, cfg)


def _fetch_kline_eastmoney(code: str, cfg: Config, klt: int = 101) -> List[Dict]:
    params = {
        "secid": secid_of(code), "klt": klt, "fqt": cfg.fqt,
        "lmt": 200 if klt != 101 else cfg.kline_bars, "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    url = EM_KLINE + "?" + urllib.parse.urlencode(params)
    data = (_get_json(url, cfg).get("data") or {})
    return [parse_kline_line(k) for k in (data.get("klines") or [])]


def _fetch_kline_sina(code: str, cfg: Config) -> List[Dict]:
    return _fetch_kline_sina_symbol(
        ("sh" if str(code).zfill(6)[0] in "69" else "sz") + str(code).zfill(6), cfg
    )


def _fetch_kline_sina_symbol(sym: str, cfg: Config) -> List[Dict]:
    url = SINA_KLINE + f"?symbol={sym}&scale=240&ma=no&datalen={cfg.kline_bars}"
    text = _request(url, cfg, referer=SINA_REFERER)
    # 返回形如 /*...*/ var _data=([{...}]); 的 JSONP，直接取最外层括号内的内容
    left = text.find("(")
    right = text.rfind(")")
    if left != -1 and right > left:
        raw = text[left + 1 : right]
    else:
        raise RuntimeError(f"新浪日K返回格式异常: {text[:120]!r}")
    data = json.loads(raw)
    bars = []
    for r in data:
        bars.append({
            "date": r["day"],
            "open": float(r["open"]), "close": float(r["close"]),
            "high": float(r["high"]), "low": float(r["low"]),
            "volume": float(r.get("volume") or 0), "amount": 0.0,
            "amplitude": 0.0, "pct_chg": 0.0, "change": 0.0, "turnover": 0.0,
            "source": "sina",
        })
    return bars


def fetch_indices(cfg: Config) -> Dict[str, Dict]:
    """拉取主要指数的最近一根日K（东财优先，失败回退新浪）。"""
    out: Dict[str, Dict] = {}
    sina_symbols = {"上证指数": "sh000001", "深证成指": "sz399001", "创业板指": "sz399006"}
    channel = _get_channel(cfg)
    if channel.get("eastmoney"):
        for name, secid in INDEX_SECIDS.items():
            params = {
                "secid": secid, "klt": 101, "fqt": 0,
                "lmt": 5, "end": "20500101",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            }
            url = EM_KLINE + "?" + urllib.parse.urlencode(params)
            try:
                data = (_get_json(url, cfg).get("data") or {})
                klines = data.get("klines") or []
                if klines:
                    out[name] = parse_kline_line(klines[-1])
            except Exception:
                continue
        if len(out) == len(INDEX_SECIDS):
            return out
    # 新浪兜底
    for name, sym in sina_symbols.items():
        if name in out:
            continue
        try:
            bars = _fetch_kline_sina_symbol(sym, cfg)
            if len(bars) >= 2:
                last = bars[-1]
                prev_close = bars[-2]["close"]
                pct = (last["close"] / prev_close - 1.0) * 100.0 if prev_close else 0.0
                out[name] = {
                    "date": last["date"],
                    "close": last["close"],
                    "pct_chg": pct,
                    "amount": last["amount"],
                }
        except Exception:
            continue
    return out


class DataCache:
    """按日期分目录的 JSON 缓存。"""

    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, day: date, name: str) -> str:
        return os.path.join(self.root, day.isoformat().replace("-", ""), name)

    def save_json(self, day: date, name: str, obj) -> None:
        path = self._path(day, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def load_json(self, day: date, name: str):
        path = self._path(day, name)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


def get_snapshot(
    cfg: Config,
    cache: DataCache,
    day: date,
    offline: bool = False,
    refresh: bool = False,
) -> Optional[List[Dict]]:
    if not refresh:
        cached = cache.load_json(day, "snapshot.json")
        if cached is not None:
            return cached
    if offline:
        return None
    snap = fetch_snapshot(cfg)
    if snap:
        cache.save_json(day, "snapshot.json", snap)
    return snap


def _fetch_and_cache_kline(code: str, cfg: Config, cache: DataCache, day: date) -> List[Dict]:
    bars = fetch_kline(code, cfg)
    if bars:
        cache.save_json(day, f"kline_{code}.json", bars)
    return bars


def get_klines(
    cfg: Config,
    cache: DataCache,
    day: date,
    codes: List[str],
    offline: bool = False,
    refresh: bool = False,
    progress: Optional[Callable[[int, int, int], None]] = None,
) -> tuple[Dict[str, List[Dict]], List[str]]:
    """并发拉取日K，返回 (成功字典, 失败代码列表)。"""
    result: Dict[str, List[Dict]] = {}
    failed: List[str] = []
    todo: Dict[str, str] = {}
    for code in codes:
        if not refresh:
            cached = cache.load_json(day, f"kline_{code}.json")
            if cached is not None:
                result[code] = cached
                continue
        if offline:
            failed.append(code)
        else:
            todo[code] = code

    if not todo:
        return result, failed

    done = 0
    with ThreadPoolExecutor(max_workers=cfg.max_workers) as ex:
        futs = {ex.submit(_fetch_and_cache_kline, c, cfg, cache, day): c for c in todo}
        for fut in as_completed(futs):
            code = futs[fut]
            try:
                bars = fut.result()
                if bars:
                    result[code] = bars
                else:
                    failed.append(code)
            except Exception:
                failed.append(code)
            done += 1
            if progress:
                progress(done, len(futs), len(failed))
    return result, failed


def get_indices(cfg: Config, cache: DataCache, day: date, offline: bool = False) -> Dict[str, Dict]:
    cached = cache.load_json(day, "indices.json")
    if cached is not None:
        return cached
    if offline:
        return {}
    indices = fetch_indices(cfg)
    if indices:
        cache.save_json(day, "indices.json", indices)
    return indices
