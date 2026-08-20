"""每日选股小程序的默认参数。

所有阈值均来自工作区历史日报（A股短线候选）的选股口径：
非 ST、今日收红、成交额>=5 亿、换手率>=3%、站上 20 日线、
MACD 在 0 轴上方或金叉/柱体改善、KDJ 金叉或低中位拐头但不过热、
近 5/10 日涨幅不过度透支。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


CN_TZ = timezone(timedelta(hours=8))


def cn_now() -> datetime:
    """返回北京时间当前时间。"""
    return datetime.now(CN_TZ)


@dataclass
class Config:
    # ---- 行情范围：深主板 / 创业板 / 沪主板 / 科创板 ----
    fs_filter: str = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
    kline_bars: int = 160        # 拉取日K根数，足够算 MA20/MACD/KDJ 和 10 日涨幅
    fqt: int = 1                 # 前复权

    # ---- 基础流动性门槛 ----
    min_amount: float = 5e8      # 成交额 >= 5 亿元
    min_turnover: float = 3.0    # 换手率 >= 3%
    max_turnover: float = 25.0   # 换手率超过 25% 降级观察

    # ---- 上市时间 ----
    min_list_days: int = 60      # 上市不足 60 个交易日视为次新，剔除

    # ---- 结构过滤 ----
    require_red: bool = True                    # 今日收红
    require_close_above_ma20: bool = True       # 收盘站上 20 日线
    prefer_ma5_gt_ma10: bool = True             # 优先 5 日线 > 10 日线

    # ---- MACD：DIF 在 0 轴上方，或金叉/柱体改善 ----
    macd_above_zero: bool = True
    macd_improving: bool = True

    # ---- KDJ：金叉或低中位 K>D 拐头，但 J 不过热 ----
    kdj_max_j: float = 95.0
    kdj_allow_golden: bool = True
    kdj_allow_turning: bool = True

    # ---- 涨幅透支 ----
    max_5d_gain: float = 25.0
    max_10d_gain: float = 40.0

    # ---- 追高判定（主板 10cm / 创业科创板 20cm） ----
    main_limit_pct: float = 9.8
    chi_limit_pct: float = 19.8

    # ---- 输出 ----
    top_n: int = 8
    out_dir: str = "."

    # ---- 网络 ----
    max_workers: int = 10
    timeout: int = 15
    retries: int = 3
    probe_timeout: int = 10      # 网络通道探测超时（秒）
    proxy: str = ""              # 手动指定代理，如 http://127.0.0.1:7897 或 socks5h://127.0.0.1:7897
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )

    # ---- 盘中判定：当日 15:05 之前运行视为盘中数据 ----
    intraday_cutoff: str = "15:05"
