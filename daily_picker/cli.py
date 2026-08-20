"""每日选股小程序命令行入口。"""

from __future__ import annotations

import argparse
import os
from datetime import date, datetime, timedelta
from typing import Optional

from . import __version__
from .config import Config, cn_now
from .data_fetch import DataCache, channel_name, get_indices, get_klines, get_snapshot
from .report import HAS_DOCX, build_content, render_docx, render_markdown
from .screening import prefilter, screen


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def resolve_data_date(args_date: Optional[str]) -> date:
    if args_date:
        return _parse_date(args_date)
    d = cn_now().date()
    while d.weekday() >= 5:  # 周末回退到周五
        d -= timedelta(days=1)
    return d


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="pick_stocks.py",
        description="A股每日短线选股小程序：拉取行情 -> 按历史口径过滤评分 -> 输出 Markdown/Word 报告",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--date", help="行情数据日期 YYYY-MM-DD，默认最近交易日")
    p.add_argument("--observe-date", help="观察日 YYYY-MM-DD，默认数据日后的下一个交易日")
    p.add_argument("--top", type=int, default=None, help="优先观察个股数量")
    p.add_argument("--format", choices=("md", "docx", "both"), default="both")
    p.add_argument("--out-dir", default=None, help="报告输出目录，默认当前目录")
    p.add_argument("--offline", action="store_true", help="仅使用本地缓存，不联网")
    p.add_argument("--refresh", action="store_true", help="忽略本地缓存，重新拉取")
    p.add_argument("--allow-intraday", action="store_true", help="允许盘中数据输出正式候选（默认盘中仅出预览）")
    p.add_argument("--max-workers", type=int, default=None, help="日K并发数")
    p.add_argument("--proxy", default=None, help="手动指定代理，如 http://127.0.0.1:7897 或 socks5h://127.0.0.1:7897")
    p.add_argument("--verbose", action="store_true", help="打印详细日志")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def _progress_cb(verbose: bool):
    def cb(done: int, total: int, failed: int):
        if verbose or done == total or (total > 0 and done % 25 == 0):
            print(f"  日K获取进度：{done}/{total}（失败 {failed}）", flush=True)

    return cb


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = Config()
    if args.top:
        cfg.top_n = args.top
    if args.max_workers:
        cfg.max_workers = args.max_workers
    if args.proxy:
        cfg.proxy = args.proxy
    if args.out_dir:
        cfg.out_dir = args.out_dir

    run_date = cn_now().date()
    data_date = resolve_data_date(args.date)
    print(f"== A股每日选股 v{__version__} ==")
    print(f"数据日期：{data_date.isoformat()}　运行时间：{cn_now().strftime('%Y-%m-%d %H:%M:%S')}")

    cache = DataCache(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache"))

    # 1)-3) 数据获取与选股（失败时降级为空仓观察，不中断）
    snapshot = None
    kline_map = {}
    try:
        snapshot = get_snapshot(cfg, cache, data_date, offline=args.offline, refresh=args.refresh)
        if not snapshot:
            print("警告：无法获取全市场快照（离线且无缓存？）")
        else:
            print(f"全市场快照：{len(snapshot)} 只")

        codes = [str(r.get("f12") or "").zfill(6) for r in prefilter(snapshot or [], cfg)]
        print(f"预筛候选：{len(codes)} 只，开始拉取日K…")
        kline_map, failed = get_klines(
            cfg, cache, data_date, codes,
            offline=args.offline, refresh=args.refresh,
            progress=_progress_cb(args.verbose),
        )
        if failed:
            print(f"日K获取失败 {len(failed)} 只（如：{failed[:3]}）")
        ch = channel_name()
        print(f"日K数据：{len(kline_map)}/{len(codes)}（网络通道：{ch if ch != '未探测' else '本地缓存'}）")

        if kline_map:
            actual = max(bars[-1]["date"] for bars in kline_map.values())
            if actual != data_date.isoformat():
                print(f"提示：{data_date.isoformat()} 无对应日K，实际最新交易日为 {actual}，报告按该日期生成。")
                data_date = _parse_date(actual)
    except Exception as exc:  # noqa: BLE001 - 网络故障降级
        print(f"警告：行情获取失败，将按“空仓观察”出报告：{exc}")

    # 4) 指数（独立容错，失败只影响市场环境小节）
    index_data = {}
    try:
        index_data = get_indices(cfg, cache, data_date, offline=args.offline)
    except Exception as exc:  # noqa: BLE001
        print(f"警告：指数获取失败，报告将不含市场环境：{exc}")

    target = data_date.isoformat()
    result = screen(snapshot or [], kline_map, cfg, target) if snapshot else {
        "priority": [], "strong": [], "excluded": [],
        "stats": {"prefiltered": 0, "analyzed": 0, "no_kline": 0},
    }
    result["snapshot"] = snapshot or []

    # 6) 盘中判定
    intraday = (
        not args.offline
        and data_date == run_date
        and cn_now().strftime("%H:%M") < cfg.intraday_cutoff
        and not args.allow_intraday
    )
    source_note = "东方财富公开行情接口（新浪日K兜底）+ 本地缓存"

    # 7) 报告
    observe_date = _parse_date(args.observe_date) if args.observe_date else next_trading_day(data_date)
    content = build_content(
        result, index_data, cfg, data_date, run_date, observe_date,
        data_ok=bool(snapshot), intraday=intraday, source_note=source_note,
    )

    os.makedirs(cfg.out_dir, exist_ok=True)
    md_path = os.path.join(cfg.out_dir, f"A股短线候选_{data_date.isoformat()}.md")
    docx_path = os.path.join(cfg.out_dir, f"A股短线候选_{data_date.isoformat()}.docx")
    written = []
    if args.format in ("md", "both"):
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(render_markdown(content))
        written.append(md_path)
    if args.format in ("docx", "both"):
        if HAS_DOCX:
            render_docx(content, docx_path)
            written.append(docx_path)
        else:
            print("警告：未安装 python-docx，跳过 .docx 输出（可 pip install python-docx）")

    # 8) 控制台摘要
    print()
    print("========== 选股摘要 ==========")
    if index_data:
        env = "　".join(
            f"{n} {bar.get('close', 0):.2f} ({bar.get('pct_chg', 0):+.2f}%)"
            for n, bar in index_data.items()
        )
        print("市场：" + env)
    st = result["stats"]
    print(
        f"全市场 {len(snapshot or [])} -> 预筛 {st['prefiltered']} -> "
        f"日K分析 {st['analyzed']}（缺失 {st['no_kline']}）"
    )
    if intraday:
        print("状态：盘中数据（预览模式），未列正式候选")
    elif not snapshot:
        print("状态：行情数据不可用，已按空仓观察出报告")
    else:
        for c in result["priority"]:
            print(
                f"  [{c.category}] {c.code} {c.name}　收盘 {c.close}　"
                f"涨幅 {c.pct_chg:+.2f}%　评分 {c.score:.1f}"
            )
        for c in result["strong"]:
            print(f"  [强势不宜追高] {c.code} {c.name}　涨幅 {c.pct_chg:+.2f}%")
    print()
    for w in written:
        print("报告已生成：" + os.path.abspath(w))
    return 0
