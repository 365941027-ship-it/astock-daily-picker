#!/usr/bin/env python3
"""生成《策略回测 + 策略卡》报告页 site/strategy.html。

用法：
    python scripts/build_strategy_report.py [--proxy socks5h://127.0.0.1:7897]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from daily_picker.config import Config, cn_now  # noqa: E402
from daily_picker.strategy import build_cards, run_backtest  # noqa: E402


PRESETS = [
    {
        "name": "A 现行规则",
        "desc": "止损7% · 止盈10% · 持有5天 · 弱市照买",
        "params": {},
    },
    {
        "name": "B 弱市禁买",
        "desc": "止损7% · 止盈10% · 持有5天 · 大盘弱势不交易",
        "params": {"skip_weak": True},
    },
    {
        "name": "C 更严止损",
        "desc": "止损5% · 止盈10% · 持有5天 · 弱市照买",
        "params": {"stop_pct": 0.05},
    },
    {
        "name": "D 更快止盈",
        "desc": "止损7% · 止盈8% · 持有5天 · 弱市照买",
        "params": {"target_pct": 0.08},
    },
    {
        "name": "E 短持有",
        "desc": "止损7% · 止盈10% · 持有3天 · 弱市照买",
        "params": {"hold_days": 3},
    },
    {
        "name": "F 保守组合",
        "desc": "止损5% · 止盈8% · 持有3天 · 弱市禁买",
        "params": {"stop_pct": 0.05, "target_pct": 0.08, "hold_days": 3, "skip_weak": True},
    },
]


def load_verify_entries() -> list[dict]:
    entries: list[dict] = []
    files = sorted(
        f for f in glob.glob(os.path.join(BASE, "daily_picker", "cache", "verify", "*.json"))
        if not f.endswith("index.json")
    )
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                day = json.load(fh)
            for e in day.get("entries", []):
                e.setdefault("date", day.get("date", ""))
                e.setdefault("checked_on", day.get("checked_on", ""))
                entries.append(e)
        except Exception:
            continue
    return entries


def load_latest_replay() -> dict:
    path = os.path.join(BASE, "daily_picker", "cache", "replay", "index.json")
    with open(path, encoding="utf-8") as f:
        idx = json.load(f)
    days = idx.get("days") or []
    if not days:
        return {}
    with open(os.path.join(BASE, "daily_picker", "cache", "replay", f"{days[-1]}.json"), encoding="utf-8") as f:
        return json.load(f)


def fetch_benchmark(cfg: Config) -> list[dict] | None:
    """拉上证指数日K作为基准；失败返回 None。"""
    try:
        from daily_picker.data_fetch import _fetch_kline_sina_symbol

        bars = _fetch_kline_sina_symbol("sh000001", cfg)
        return bars if bars else None
    except Exception:
        return None


def bench_curve(bench_bars: list[dict] | None, trades: list[dict]) -> list[float] | None:
    if not bench_bars:
        return None
    closes = {b["date"]: b["close"] for b in bench_bars}
    out = [1.0]
    eq = 1.0
    for t in trades:
        a = closes.get(t["entry_date"])
        b = closes.get(t["exit_date"])
        if a and b:
            eq *= b / a
        out.append(round(eq, 4))
    return out


def align_series(points: list[dict], bench_closes: dict[str, float], all_dates: list[str]) -> tuple[list[float], list[float | None]]:
    """把某方案的净值点对齐到统一日期轴（向后填充），并给出基准同期净值。"""
    by_date = {p["date"]: p["equity"] for p in points}
    cur = 1.0
    out: list[float] = []
    for d in all_dates:
        if d in by_date:
            cur = by_date[d]
        out.append(cur)
    if not all_dates:
        return [], []
    base = bench_closes.get(all_dates[0])
    bench_out: list[float | None] = []
    for d in all_dates:
        c = bench_closes.get(d)
        bench_out.append(round(c / base, 4) if base and c else None)
    return out, bench_out


PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>策略回测 · A股短线观察系统</title>
<style>
:root{--red:#e03131;--green:#2f9e44;--ink:#1f2733;--sub:#667085;--line:#e8edf4;--bg:#f6f8fb;--card:#fff}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);line-height:1.6}
.wrap{max-width:1100px;margin:0 auto;padding:28px 20px 60px}
.hero{display:flex;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:22px}
.hero h1{font-size:24px;font-weight:700;letter-spacing:.3px}
.hero p{color:var(--sub);font-size:13px;margin-top:4px}
.badge{font-size:12px;padding:4px 12px;border-radius:999px;background:#eef4ff;color:#1d4ed8;font-weight:600}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:18px}
.metric{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.metric .k{font-size:12px;color:var(--sub)}
.metric .v{font-size:26px;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}
.metric .s{font-size:11px;color:var(--sub);margin-top:2px}
.up{color:var(--red)} .down{color:var(--green)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px 22px;margin-bottom:18px}
.panel h2{font-size:16px;margin-bottom:14px;display:flex;align-items:center;gap:8px}
.panel h2::before{content:"";width:4px;height:16px;border-radius:2px;background:#2563eb}
.rules{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}
.rule{background:#fafbfd;border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.rule b{display:block;font-size:12px;color:var(--sub);margin-bottom:4px}
.rule span{font-size:14px;font-weight:600}
.chart-box{position:relative;height:280px}
canvas{width:100%;height:100%;display:block}
.legend{display:flex;gap:16px;font-size:12px;color:var(--sub);margin-top:8px}
.legend i{display:inline-block;width:18px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
.table-wrap{overflow-x:auto;max-height:440px;overflow-y:auto}
table{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}
th,td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:right}
th{position:sticky;top:0;background:#f4f6fa;color:var(--sub);font-weight:600;z-index:1}
td:first-child,th:first-child{text-align:left}
.t-red{color:var(--red);font-weight:700}
.t-green{color:var(--green);font-weight:700}
.tag{font-size:11px;padding:2px 8px;border-radius:6px;background:#f1f3f5;color:#495057}
.tag.red{background:#fff0f0;color:#c92a2a}.tag.yellow{background:#fff9e6;color:#b08900}.tag.green{background:#ebfbee;color:#2b8a3e}.tag.observe{background:#e7f5ff;color:#1971c2}
.card-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.scard{border:1px solid var(--line);border-radius:14px;padding:16px 18px;background:#fff}
.scard .top{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px}
.scard .name{font-size:16px;font-weight:700}
.scard .code{font-size:12px;color:var(--sub)}
.scard .pos{font-size:11px;color:#1d4ed8;background:#eef4ff;border-radius:6px;padding:3px 8px}
.kv{display:grid;grid-template-columns:1fr 1fr;gap:8px 14px;margin:10px 0;font-size:13px}
.kv div{display:flex;justify-content:space-between;border-bottom:1px dashed var(--line);padding-bottom:5px}
.kv b{font-weight:600}
.note{font-size:12px;color:var(--sub);background:#fafbfd;border-radius:8px;padding:8px 10px}
.foot{margin-top:26px;font-size:12px;color:var(--sub);border-top:1px solid var(--line);padding-top:14px;line-height:1.8}
.empty{color:var(--sub);text-align:center;padding:30px;font-size:14px}
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <div>
      <h1>策略回测 · 短线观察系统</h1>
      <p>回踩支撑买入 · 7% 止损 · 10% 止盈 · 最长 5 个交易日 · 数据截至 __DATE__</p>
    </div>
    <span class="badge">模拟盘原型 · 仅供研究</span>
  </div>

  <div class="cards" id="metrics"></div>

  <div class="panel">
    <h2>策略净值 vs 上证指数</h2>
    <div class="chart-box"><canvas id="chart"></canvas></div>
    <div class="legend"><span><i style="background:#2563eb"></i>策略净值</span><span><i style="background:#adb5bd"></i>上证指数</span><span id="chartHint"></span></div>
  </div>

  <div class="panel">
    <h2>参数对比：哪套规矩更稳？</h2>
    <p style="font-size:13px;color:var(--sub);margin:-6px 0 14px">同一批历史信号，换不同止损/止盈/持有天数/是否弱市禁买，结果并排对比。回撤越浅、收益越稳，说明这套规矩越抗揍。</p>
    <div class="table-wrap"><table id="cmpTable"></table></div>
    <div class="chart-box" style="height:320px"><canvas id="cmpChart"></canvas></div>
    <div class="legend" id="cmpLegend"></div>
  </div>

  <div class="panel">
    <h2>交易规则（可执行策略卡）</h2>
    <div class="rules">
      <div class="rule"><b>触发</b><span>次日回踩支撑位不破</span></div>
      <div class="rule"><b>执行</b><span>次次日开盘买入，高开超5%放弃</span></div>
      <div class="rule"><b>止损</b><span>买入价 -7%</span></div>
      <div class="rule"><b>止盈</b><span>买入价 +10%</span></div>
      <div class="rule"><b>期限</b><span>最长持有 5 个交易日</span></div>
      <div class="rule"><b>仓位</b><span>按大盘研判：空仓 / ≤5% / ≤10%</span></div>
    </div>
  </div>

  <div class="panel">
    <h2>最新交易明细（按入场日倒序）</h2>
    <div class="table-wrap"><table id="trades"></table></div>
  </div>

  <div class="panel">
    <h2>最新候选策略卡（__CARD_DATE__）</h2>
    <div class="card-grid" id="cards"></div>
  </div>

  <div class="foot">
    说明：本回测基于历史缓存逐日重算，未计交易佣金与滑点；样本期为可验证区间，最新交易日尚未完成的交易已自动排除。<br>
    局限：回测股票池来自当前缓存中的幸存股票，胜率存在高估可能；“跌破支撑=观察成功”等口径偏乐观；模拟盘不构成投资建议，据此操作风险自负。
  </div>
</div>

<script>
const DATA = __DATA__;

function fmt(v, d=2){ return (v===null||v===undefined) ? "—" : Number(v).toFixed(d); }
function cls(v){ return v>0 ? "t-red" : v<0 ? "t-green" : ""; }
function tag(g){ return `<span class="tag ${g}">${{red:"成功上涨",yellow:"成功下跌",green:"失败下跌",observe:"仅观察"}[g]||g}</span>`; }

const m = DATA.summary;
document.getElementById("metrics").innerHTML = [
  ["入场次数", m.n_trades, `${m.no_trigger} 次信号未触发`],
  ["胜率", fmt(m.win_rate,1)+"%", `${m.n_win} 盈 / ${m.n_loss} 亏`],
  ["盈亏比", fmt(m.profit_factor), `平均盈 ${fmt(m.avg_win)}% / 亏 ${fmt(m.avg_loss)}%`],
  ["累计收益", fmt(m.cum_return)+"%", m.cum_return>=0?"跑赢基准":"落后基准"],
  ["最大回撤", fmt(m.max_drawdown)+"%", "按复利净值计算"],
  ["vs 上证指数", fmt(m.excess_return)+"%", `基准 ${fmt(m.bench_return)}%`],
].map(([k,v,s]) => `<div class="metric"><div class="k">${k}</div><div class="v ${k==="最大回撤"?"down":(k==="累计收益"||k==="vs 上证指数")?(m.cum_return>=0||m.excess_return>=0?"up":"down"):""}">${v}</div><div class="s">${s}</div></div>`).join("");

const cv = document.getElementById("chart");
const dpr = window.devicePixelRatio||1;
const w = cv.clientWidth||900, h = cv.clientHeight||280;
cv.width = w*dpr; cv.height = h*dpr;
const ctx = cv.getContext("2d");
ctx.setTransform(dpr,0,0,dpr,0,0);
const eq = DATA.equity, be = DATA.bench;
const all = eq.concat(be||[]);
let lo = Math.min(...all), hi = Math.max(...all);
const pad = (hi-lo)*0.08 || 0.1; lo-=pad; hi+=pad;
const padL=44, padR=12, padT=14, padB=22;
const nx = eq.length-1;
const px = i => padL + (nx? (i*(w-padL-padR)/nx):0);
const py = v => padT + (hi-v)/(hi-lo)*(h-padT-padB);
ctx.clearRect(0,0,w,h);
for(let g=0;g<=4;g++){ const y=padT+(h-padT-padB)*g/4; ctx.strokeStyle="#eef1f5"; ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(w-padR,y); ctx.stroke();
  ctx.fillStyle="#9aa4b2"; ctx.font="11px sans-serif"; ctx.textAlign="right"; ctx.fillText(fmt(hi-(hi-lo)*g/4,0)+"%", padL-6, y+4); }
function line(series,color){ ctx.strokeStyle=color; ctx.lineWidth=2; ctx.beginPath(); series.forEach((v,i)=>{ const x=px(i), y=py(v); i?ctx.lineTo(x,y):ctx.moveTo(x,y); }); ctx.stroke(); }
if(be) line(be,"#adb5bd");
line(eq,"#2563eb");
const lx=eq.length-1; ctx.fillStyle="#2563eb"; ctx.fillText("策略 "+fmt((eq[lx]-1)*100,1)+"%", px(lx)-80, py(eq[lx])-8);
if(be) { ctx.fillStyle="#868e96"; ctx.fillText("上证 "+fmt((be[lx]-1)*100,1)+"%", px(lx)-80, py(be[lx])+12); }

/* ---------- 参数对比 ---------- */
const CMP = DATA.comparison;
const COLORS = ["#2563eb","#e03131","#2f9e44","#f59f00","#9c36b5","#0ca678","#f76707","#12b886"];
const cmpTb = document.getElementById("cmpTable");
cmpTb.innerHTML = `<thead><tr><th>方案</th><th>规则说明</th><th class="num">交易次数</th><th class="num">胜率</th><th class="num">累计收益</th><th class="num">最大回撤</th><th class="num">盈亏比</th><th class="num">超额收益</th></tr></thead><tbody>` +
CMP.map((c,i)=>`<tr>
  <td><b>${c.name}</b></td><td class="desc" style="text-align:left;color:var(--sub);font-size:12px">${c.desc}</td>
  <td class="num">${c.n_trades}</td>
  <td class="num">${fmt(c.win_rate,1)}%</td>
  <td class="num ${c.cum_return>=0?"t-red":"t-green"}">${c.cum_return>=0?"+":""}${fmt(c.cum_return)}%</td>
  <td class="num t-green">${fmt(c.max_drawdown)}%</td>
  <td class="num">${fmt(c.profit_factor)}</td>
  <td class="num ${c.excess_return>=0?"t-red":"t-green"}">${c.excess_return>=0?"+":""}${fmt(c.excess_return)}%</td>
</tr>`).join("") + `</tbody>`;

const cc = document.getElementById("cmpChart");
const cdpr = window.devicePixelRatio||1;
const cw = cc.clientWidth||900, ch = cc.clientHeight||320;
cc.width = cw*cdpr; cc.height = ch*cdpr;
const cctx = cc.getContext("2d");
cctx.setTransform(cdpr,0,0,cdpr,0,0);
const cmpDates = DATA.cmp_dates;
const allVals = CMP.flatMap(c=>c.curve).concat(DATA.cmp_bench||[]).filter(v=>v!==null);
let clo = Math.min(...allVals), chi = Math.max(...allVals);
const cpad = (chi-clo)*0.08 || 0.1; clo-=cpad; chi+=cpad;
const cpadL=44, cpadR=12, cpadT=14, cpadB=22;
const cnx = cmpDates.length-1;
const cpx = i => cpadL + (cnx? (i*(cw-cpadL-cpadR)/cnx):0);
const cpy = v => cpadT + (chi-v)/(chi-clo)*(ch-cpadT-cpadB);
cctx.clearRect(0,0,cw,ch);
for(let g=0;g<=4;g++){ const y=cpadT+(ch-cpadT-cpadB)*g/4; cctx.strokeStyle="#eef1f5"; cctx.beginPath(); cctx.moveTo(cpadL,y); cctx.lineTo(cw-cpadR,y); cctx.stroke();
  cctx.fillStyle="#9aa4b2"; cctx.font="11px sans-serif"; cctx.textAlign="right"; cctx.fillText(fmt(chi-(chi-clo)*g/4,0)+"%", cpadL-6, y+4); }
function cmpLine(series,color,width){ cctx.strokeStyle=color; cctx.lineWidth=width||2; cctx.beginPath(); series.forEach((v,i)=>{ const x=cpx(i), y=cpy(v); if(v===null)return; i?cctx.lineTo(x,y):cctx.moveTo(x,y); }); cctx.stroke(); }
if(DATA.cmp_bench) cmpLine(DATA.cmp_bench,"#adb5bd",1.5);
CMP.forEach((c,i)=>cmpLine(c.curve, COLORS[i], 2));
const clx = cmpDates.length-1;
document.getElementById("cmpLegend").innerHTML = CMP.map((c,i)=>
  `<span><i style="background:${COLORS[i]}"></i>${c.name} ${fmt((c.curve[clx]-1)*100,1)}%</span>`).join("") +
  `<span><i style="background:#adb5bd"></i>上证 ${DATA.cmp_bench?fmt((DATA.cmp_bench[clx]-1)*100,1)+"%":""}</span>`;

const tb = document.getElementById("trades");
if(DATA.trades.length){
  tb.innerHTML = `<thead><tr><th>信号日</th><th>代码</th><th>名称</th><th>分组</th><th>入场日</th><th>入场价</th><th>止损</th><th>止盈</th><th>出场日</th><th>出场价</th><th>盈亏</th><th>出场原因</th></tr></thead><tbody>` +
  DATA.trades.slice().reverse().map(t=>`<tr>
    <td>${t.signal_date}</td><td>${t.code}</td><td>${t.name}</td><td>${tag(t.group)}</td>
    <td>${t.entry_date}</td><td>${fmt(t.entry)}</td><td>${fmt(t.stop)}</td><td>${fmt(t.target)}</td>
    <td>${t.exit_date}</td><td>${fmt(t.exit)}</td>
    <td class="${cls(t.pnl_pct)}">${t.pnl_pct>=0?"+":""}${fmt(t.pnl_pct)}%</td><td>${t.reason}</td>
  </tr>`).join("") + `</tbody>`;
} else { tb.innerHTML = `<tr><td class="empty">暂无已完成交易</td></tr>`; }

const cb = document.getElementById("cards");
if(DATA.cards.length){
  cb.innerHTML = DATA.cards.map(c=>`<div class="scard">
    <div class="top"><div><div class="name">${c.name} <span class="code">${c.code}</span></div>
      <div class="tag" style="margin-top:5px">${c.category} · ${c.structure}</div></div>
      <span class="pos">${c.position}</span></div>
    <div class="kv">
      <div><span>观察日收盘</span><b>${fmt(c.close)}</b></div>
      <div><span>支撑位</span><b>${fmt(c.support)}</b></div>
      <div><span>触发条件</span><b>${c.trigger}</b></div>
      <div><span>止损线</span><b class="down">${fmt(c.stop)}</b></div>
      <div><span>目标价</span><b class="up">${fmt(c.target)}</b></div>
      <div><span>观察期限</span><b>${c.hold_days} 个交易日</b></div>
    </div>
    <div class="note">${c.note}</div>
  </div>`).join("");
} else { cb.innerHTML = `<div class="empty">暂无候选</div>`; }
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="生成策略回测报告")
    parser.add_argument("--proxy", default="", help="行情代理")
    args = parser.parse_args()

    cfg = Config()
    if args.proxy:
        cfg.proxy = args.proxy

    print("== 生成策略回测报告 ==")
    entries = load_verify_entries()
    result = run_backtest(entries, params=PRESETS[0]["params"])
    trades = result["trades"]
    print(f"核对记录 {len(entries)} 条 → 交易 {result['n_trades']} 笔（未触发 {result['no_trigger']}，跳过 {result['skip']}）")
    print(f"胜率 {result['win_rate']}% | 累计收益 {result['cum_return']}% | 最大回撤 {result['max_drawdown']}% | 盈亏比 {result['profit_factor']}")

    bench_bars = fetch_benchmark(cfg)
    be = bench_curve(bench_bars, trades)
    bench_return = round((be[-1] - 1) * 100, 2) if be else None
    bench_closes = {b["date"]: b["close"] for b in bench_bars} if bench_bars else {}
    equity_curve = result.pop("equity_curve")
    result.pop("curve_points", None)
    summary = {
        **result,
        "bench_return": bench_return,
        "excess_return": round(result["cum_return"] - (bench_return or 0), 2) if bench_return is not None else None,
        "equity": equity_curve,
        "bench": be,
    }

    # ---- 参数对比：跑全部预设，统一日期轴对齐 ----
    all_points: list[dict] = []
    for pr in PRESETS:
        r = run_backtest(entries, params=pr["params"])
        all_points.extend(r["curve_points"])
    all_dates = sorted({p["date"] for p in all_points})
    comparison = []
    for i, pr in enumerate(PRESETS):
        r = run_backtest(entries, params=pr["params"])
        curve, bcurve = align_series(r["curve_points"], bench_closes, all_dates)
        bench_end = (bcurve[-1] - 1) * 100 if bcurve and bcurve[-1] else 0.0
        excess = round(r["cum_return"] - bench_end, 2)
        comparison.append({
            "name": pr["name"],
            "desc": pr["desc"],
            "n_trades": r["n_trades"],
            "win_rate": r["win_rate"],
            "cum_return": r["cum_return"],
            "max_drawdown": r["max_drawdown"],
            "profit_factor": r["profit_factor"],
            "excess_return": excess,
            "curve": curve,
        })
        print(f"  对比 {pr['name']}: {r['n_trades']}笔 | 胜率{r['win_rate']}% | 收益{r['cum_return']}% | 回撤{r['max_drawdown']}% | 超额{excess}%")
    _, bench_aligned = align_series(all_points, bench_closes, all_dates)

    replay = load_latest_replay()
    card_date = (replay or {}).get("date", "")
    verdict = ""
    try:
        with open(os.path.join(BASE, "site", "data", "latest.json"), encoding="utf-8") as f:
            verdict = json.load(f).get("market_verdict", "")
    except Exception:
        pass
    cards = build_cards(replay or {}, verdict)
    print(f"策略卡：{len(cards)} 张（{card_date}）")

    data = {
        "summary": {k: v for k, v in summary.items() if k not in ("trades",)},
        "trades": trades,
        "equity": equity_curve,
        "bench": be,
        "cards": cards,
        "comparison": comparison,
        "cmp_dates": all_dates,
        "cmp_bench": bench_aligned,
    }
    html = (
        PAGE.replace("__DATE__", cn_now().strftime("%Y-%m-%d"))
        .replace("__CARD_DATE__", card_date)
        .replace("__DATA__", json.dumps(data, ensure_ascii=False))
    )
    out = os.path.join(BASE, "site", "strategy.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"报告已生成：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
