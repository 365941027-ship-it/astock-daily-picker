"use strict";

/* ---------- 全局状态 ---------- */
const STATIC = window.STATIC_MODE === true;
const LS_USER = "astock-static-userdata-v1";

const $ = (sel) => document.querySelector(sel);
const state = {
  running: false,
  result: null,
  pollTimer: null,
  replayDays: [],
  replayActive: null,
  risks: {},
  modal: { code: "", date: "", name: "", klt: 101, chan: null },
};

let pollFailures = 0;
const LS_KEY = "astock-picker-params-v1";

/* ---------- 工具函数 ---------- */
function defaultUserdata() {
  return { watchlist: [], portfolio: { cash: 100000, positions: [], trades: [] } };
}

function localUserdata() {
  try {
    const d = JSON.parse(localStorage.getItem(LS_USER) || "null");
    if (d && Array.isArray(d.watchlist) && d.portfolio) return d;
  } catch (e) {}
  return defaultUserdata();
}

function saveUserdata(d) {
  try { localStorage.setItem(LS_USER, JSON.stringify(d)); } catch (e) {}
}

function localAddWatch(body) {
  const d = localUserdata();
  if (!d.watchlist.some((w) => w.code === String(body.code).padStart(6, "0"))) {
    d.watchlist.push({
      code: String(body.code).padStart(6, "0"),
      name: body.name || body.code,
      alert_price: body.alert_price || null,
      alert_type: body.alert_type || "below",
    });
    saveUserdata(d);
    return { ok: true, msg: "已加入自选（保存在本机浏览器）" };
  }
  return { ok: true, msg: "该股票已在自选中" };
}

function localRemoveWatch(code) {
  const d = localUserdata();
  d.watchlist = d.watchlist.filter((w) => w.code !== code);
  saveUserdata(d);
  return { ok: true, msg: "已删除" };
}

function localTrade(body) {
  const d = localUserdata();
  const p = d.portfolio;
  const price = Number(body.price);
  const shares = Number(body.shares);
  const amt = price * shares;
  const code = String(body.code).padStart(6, "0");
  if (body.action === "buy") {
    if (p.cash < amt) return { ok: false, msg: "可用资金不足" };
    p.cash -= amt;
    const pos = p.positions.find((x) => x.code === code);
    if (pos) {
      const totalCost = pos.cost * pos.shares + amt;
      pos.shares += shares;
      pos.cost = totalCost / pos.shares;
    } else {
      p.positions.push({ code, name: body.name || body.code, cost: price, shares });
    }
  } else {
    const pos = p.positions.find((x) => x.code === code);
    if (!pos || pos.shares < shares) return { ok: false, msg: "持仓不足" };
    p.cash += amt;
    pos.shares -= shares;
    if (pos.shares === 0) p.positions = p.positions.filter((x) => x !== pos);
  }
  p.trades.push({
    date: new Date().toISOString().slice(0, 10),
    code,
    name: body.name || body.code,
    action: body.action,
    price,
    shares,
  });
  saveUserdata(d);
  return { ok: true, msg: body.action === "buy" ? "买入成功（保存在本机）" : "卖出成功（保存在本机）" };
}

function localClose(code) {
  const d = localUserdata();
  const pos = d.portfolio.positions.find((x) => x.code === code);
  if (pos) {
    d.portfolio.cash += pos.cost * pos.shares;
    d.portfolio.positions = d.portfolio.positions.filter((x) => x !== pos);
  }
  saveUserdata(d);
  return { ok: true, msg: "已平仓" };
}

/* 静态模式：把 /api/* 路径映射到 data/ 下的 JSON 文件 */
function staticPath(url) {
  const u = new URL(url, location.href);
  const path = u.pathname;
  if (path === "/api/config" || path === "/api/status") return "data/config.json";
  if (path === "/api/replay") {
    const d = u.searchParams.get("date");
    return d ? `data/replay/${d}.json` : "data/replay.json";
  }
  if (path === "/api/verify") {
    const d = u.searchParams.get("date");
    return d ? `data/verify/${d}.json` : "data/verify.json";
  }
  if (path === "/api/market-signal") return "data/market.json";
  if (path === "/api/watch-sectors") return "data/watch_sectors.json";
  if (path === "/api/news") return "data/news.json";
  if (path === "/api/sectors") return "data/sectors.json";
  if (path === "/api/kline") {
    const code = u.searchParams.get("code") || "";
    const klt = u.searchParams.get("klt") || "101";
    return `data/kline/${code}_${klt}.json`;
  }
  if (path === "/api/quotes") return "data/quotes.json";
  return null;
}

async function fetchWithTimeout(url, ms = 8000, options = undefined) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  try {
    if (STATIC) {
      const u = new URL(url, location.href);
      const path = u.pathname;
      const finish = (data) => ({ ok: true, json: async () => data });
      if (options && options.method === "POST") {
        let body = {};
        try { body = options.body ? JSON.parse(options.body) : {}; } catch (e) {}
        if (path === "/api/watchlist") return finish(localAddWatch(body));
        if (path === "/api/trade") return finish(localTrade(body));
        if (path === "/api/close") return finish(localClose(body.code));
        return finish({ ok: true }); // run/replay/verify/subscribe：静态版无后端任务
      }
      if (path === "/api/userdata") return finish(localUserdata());
      if (path === "/api/status") return finish({ state: "idle", logs: [], kind: null, result: null });
      if (path === "/api/watchlist") return finish(localRemoveWatch(u.searchParams.get("code") || ""));
      if (!path.startsWith("/api/")) return fetch(url, { signal: ctrl.signal });
      const target = staticPath(url);
      if (!target) return { ok: false, json: async () => ({ error: "静态预览版不支持该接口" }) };
      return fetch(target, { signal: ctrl.signal });
    }
    return await fetch(url, Object.assign({}, options, { signal: ctrl.signal }));
  } finally {
    clearTimeout(timer);
  }
}

function fmtPct(v) {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%`;
}

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined) return "—";
  return Number(v).toFixed(digits);
}

function pctClass(v) {
  if (v === null || v === undefined) return "";
  return v > 0 ? "pct-up" : v < 0 ? "pct-down" : "";
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* 风险徽标：红色=一票否决，黄色=风险提示；warnOnly 时只显示黄色提示（历史回放/核对避免误导） */
function riskBadges(risks, warnOnly) {
  if (!risks || !risks.length) return "";
  return risks.map((r) => {
    const veto = String(r || "").includes("否决") || String(r || "").includes("拟减持") ||
      String(r || "").includes("立案") || String(r || "").includes("预亏") ||
      String(r || "").includes("退市") || String(r || "").includes("重组终止") ||
      String(r || "").includes("商誉") || String(r || "").includes("处罚");
    if (warnOnly && veto) return "";
    return `<span class="risk-badge ${veto ? "risk-veto" : ""}" title="${esc(r)}">${veto ? "排雷" : "提示"} ${esc(r)}</span>`;
  }).join("");
}

function nameCell(name, code, date, risks, warnOnly) {
  return `<span class="stock-link" data-code="${esc(code)}" data-date="${esc(date || "")}" data-name="${esc(name)}">${esc(name)}</span>${riskBadges(risks, warnOnly)}`;
}

function klineBtn(code, date, name) {
  return `<span class="stock-link" data-code="${esc(code)}" data-date="${esc(date || "")}" data-name="${esc(name)}">K线</span>`;
}

function bindStockLinks(scope) {
  (scope || document).querySelectorAll(".stock-link").forEach((el) => {
    el.onclick = () => {
      const { code, date, name } = el.dataset;
      if (code) openModal(code, date, name || code);
    };
  });
}

/* ---------- 参数读写 ---------- */
function saveParams() {
  const p = {
    date: $("#inputDate").value,
    top: $("#inputTop").value,
    proxy: $("#inputProxy").value.trim(),
    refresh: $("#inputRefresh").checked,
    intraday: $("#inputIntraday").checked,
  };
  try { localStorage.setItem(LS_KEY, JSON.stringify(p)); } catch (e) {}
}

function loadParams() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || "{}");
  } catch {
    return {};
  }
}

/* ---------- 导航 ---------- */
function switchSection(name) {
  document.querySelectorAll(".section").forEach((s) => s.classList.remove("active"));
  const sec = document.getElementById("sec-" + name);
  if (sec) sec.classList.add("active");
  document.querySelectorAll(".nav-item").forEach((a) => {
    a.classList.toggle("active", a.dataset.section === name);
  });
}

document.querySelectorAll(".nav-item").forEach((a) => {
  a.onclick = () => switchSection(a.dataset.section);
});

/* ---------- 初始化 ---------- */
async function init() {
  if (STATIC) {
    document.body.classList.add("static-mode");
    // 隐藏“运行类”操作（选股/回放/核对/刷新按钮），页面保持只读预览
    ["btnRun", "btnReset", "btnReplay", "btnVerify", "btnVerifyAll", "btnSignal", "btnNews", "btnSectors"]
      .forEach((id) => { const el = document.getElementById(id); if (el) el.hidden = true; });
    ["#sec-pick", "#sec-replay", "#sec-verify"].forEach((sel) => {
      const card = document.querySelector(`${sel} .card`);
      if (card) {
        const grid = card.querySelector(".form-grid"); if (grid) grid.hidden = true;
        const actions = card.querySelector(".actions"); if (actions) actions.hidden = true;
      }
    });
    // K线弹窗只保留日K（静态版不带分钟线）
    document.querySelectorAll(".klt-tab").forEach((el) => {
      if (Number(el.dataset.klt) !== 101) el.hidden = true;
    });
    setStatus("static", "静态预览");
  }
  if (location.protocol === "file:") {
    alert("请通过 http://127.0.0.1:8235 访问网页版，不要直接用文件打开。");
    return;
  }
  const cfg = await fetchWithTimeout("/api/config").then((r) => r.json()).catch(() => ({}));
  const saved = loadParams();
  $("#inputDate").value = saved.date || cfg.default_date || "";
  $("#inputTop").value = saved.top || cfg.default_top || 8;
  $("#inputProxy").value = saved.proxy || "";
  $("#inputRefresh").checked = !!saved.refresh;
  $("#inputIntraday").checked = !!saved.intraday;
  $("#replayStart").value = "2026-07-01";
  $("#replayEnd").value = cfg.default_date || "";
  $("#verifyDate").value = cfg.default_date || "";
  fetchWithTimeout("/api/replay").then((r) => {
    if (!r.ok) throw new Error();
    return r.json();
  }).then((idx) => {
    if (idx && idx.days) renderReplayDays(idx.days);
  }).catch(() => {});
  // 预判核对默认选“有核对数据的最后一天”（而不是最新交易日，最新交易日还没有次日可核对）
  fetchWithTimeout("/api/verify").then((r) => {
    if (!r.ok) throw new Error();
    return r.json();
  }).then((idx) => {
    const days = idx && idx.days ? idx.days : [];
    if (days.length) {
      $("#verifyDate").value = days[days.length - 1];
      loadVerifyDay(days[days.length - 1]);
    }
  }).catch(() => {});
  loadWatchlist();
  loadHoldings();
  loadSignal();
  loadNews();
  loadSectors();
  if (STATIC) {
    fetchWithTimeout("data/risks.json").then((r) => r.json()).then((m) => { state.risks = m || {}; }).catch(() => {});
    fetchWithTimeout("data/latest.json").then((r) => r.json()).then((r) => {
      if (r && r.priority) renderResult(r);
    }).catch(() => {});
  }
  pollStatus();
  setInterval(pollStatus, 2500);
}

/* ---------- 运行 ---------- */
async function startRun() {
  if (state.running) return;
  try { sessionStorage.removeItem("loading-dismissed"); } catch (e) {}
  saveParams();
  const params = {
    date: $("#inputDate").value || null,
    top: Number($("#inputTop").value) || 8,
    proxy: $("#inputProxy").value.trim() || null,
    refresh: $("#inputRefresh").checked,
    allow_intraday: $("#inputIntraday").checked,
  };
  const res = await fetchWithTimeout("/api/run", 10000, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  }).catch(() => null);
  if (!res) {
    alert("无法连接服务，请确认 webapp.py 正在运行");
    return;
  }
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    alert(j.error || "启动失败");
    return;
  }
  state.running = true;
  setStatus("running", "运行中");
  if (document.getElementById("loadingMask")) $("#loadingMask").hidden = false;
  $("#logBox").hidden = false;
  $("#logList").innerHTML = "";
  pollStatus();
}

async function pollStatus() {
  if (STATIC) return;
  try {
    const st = await fetchWithTimeout("/api/status").then((r) => r.json());
    pollFailures = 0;
    renderLogs(st.logs);
    if (st.state === "running") {
      setStatus("running", "运行中");
      state.running = true;
      let dismissed = false;
      try { dismissed = !!sessionStorage.getItem("loading-dismissed"); } catch (e) {}
      if (!dismissed && document.getElementById("loadingMask")) $("#loadingMask").hidden = false;
      return;
    }
    state.running = false;
    if (document.getElementById("loadingMask")) $("#loadingMask").hidden = true;
    if (st.state === "done") {
      setStatus("done", "完成");
      if (st.kind === "replay") renderReplayResult(st.result);
      else if (st.kind === "verify") appendLog(`预判核对完成：${(st.result?.days || []).length} 个交易日`);
      else renderResult(st.result);
    } else if (st.state === "error") {
      setStatus("error", "失败");
      renderLogs([{ t: "", m: "错误：" + (st.error || "未知错误") }]);
    } else {
      setStatus("idle", "空闲");
    }
  } catch {
    pollFailures += 1;
    if (pollFailures >= 3) {
      state.running = false;
      if (document.getElementById("loadingMask")) $("#loadingMask").hidden = true;
      setStatus("error", "连接失败");
    }
  }
}

function renderLogs(logs) {
  if (!logs || !logs.length) return;
  const box = $("#logList");
  const last = logs[logs.length - 1];
  const lastKey = `${last.t}|${last.m}`;
  if (box.dataset.last === lastKey) return;
  box.dataset.last = lastKey;
  box.innerHTML = logs.map((l) => `<div>${esc(l.t)}　${esc(l.m)}</div>`).join("");
  $("#logBox").scrollTop = $("#logBox").scrollHeight;
}

function appendLog(msg) {
  const box = $("#logList");
  const div = document.createElement("div");
  div.textContent = msg;
  box.appendChild(div);
  $("#logBox").scrollTop = $("#logBox").scrollHeight;
}

function setStatus(kind, text) {
  const b = $("#statusBadge");
  b.className = "status-badge " + kind;
  b.textContent = text;
}

/* ---------- 结果渲染 ---------- */
function renderResult(r) {
  if (!r) return;
  state.result = r;
  const noMarket = r.market_verdict === "不适合入场";
  // 大盘不宜入场：只显示空仓观察提示，不展示个股推荐
  const marketBanner = document.getElementById("marketBanner");
  if (marketBanner) {
    marketBanner.hidden = !noMarket;
    if (noMarket) {
      marketBanner.innerHTML =
        `<div class="card" style="background:#fff5f5;border-color:#ffc9c9">
          <div class="card-title" style="color:#c92a2a">大盘研判：不适合入场 · 仅做空仓观察</div>
          <p style="font-size:14px;color:#3d4754">缠论大盘研判判定次日不宜入场，按纪律本轮不推荐任何个股，只做空仓观察，等待指数站稳中枢或出现明确转强信号。</p>
        </div>`;
    }
  }
  document.getElementById("sec-pick").querySelectorAll(".section-block").forEach((el) => {
    el.hidden = noMarket;
  });
  document.getElementById("downloadActions") && (document.getElementById("downloadActions").hidden = noMarket);
  $("#resultMeta").textContent = r.replay
    ? `历史回放 · ${r.data_date} · 回放池 ${r.pool || 0} 只`
    : `数据日 ${r.data_date} · 观察日 ${r.observe_date} · 通道 ${r.channel || "缓存"}`;

  if (r.market && r.market.total) {
    const m = r.market;
    $("#marketStrip").hidden = false;
    $("#marketStrip").innerHTML = [
      chip("上涨", m.up, m.up >= m.down ? "up" : "down"),
      chip("下跌", m.down, m.down > m.up ? "down" : "up"),
      chip("平盘", m.flat, ""),
      chip("涨停", m.limit_up, "up"),
      chip("总数", m.total, ""),
    ].join("");
  }
  if (r.indices && Object.keys(r.indices).length) {
    $("#indexRow").hidden = false;
    $("#indexRow").innerHTML = Object.entries(r.indices)
      .map(([n, v]) => `<div class="index-item"><b>${esc(n)}</b> ${fmtNum(v.close)} ` +
        `<span class="${pctClass(v.pct_chg)}">${fmtPct(v.pct_chg)}</span></div>`)
      .join("");
  }

  $("#priCount").textContent = r.priority.length;
  $("#priTable tbody").innerHTML = r.priority.map((c) => `<tr>
    <td>${esc(c.code)}</td>
    <td>${nameCell(c.name, c.code, r.data_date, c.risks)}</td>
    <td class="num">${fmtNum(c.close)}</td>
    <td class="num ${pctClass(c.pct_chg)}">${fmtPct(c.pct_chg)}</td>
    <td class="num">${fmtNum(c.turnover)}%</td>
    <td>${esc(c.structure)}</td>
    <td>${esc(c.note)}</td></tr>`).join("") ||
    `<tr><td colspan="7" class="empty">本轮没有个股进入优先观察。</td></tr>`;

  $("#strongCount").textContent = r.strong.length;
  $("#strongTable tbody").innerHTML = r.strong.map((c) => `<tr>
    <td>${esc(c.code)}</td>
    <td>${nameCell(c.name, c.code, r.data_date, c.risks)}</td>
    <td class="num">${fmtNum(c.close)}</td>
    <td class="num ${pctClass(c.pct_chg)}">${fmtPct(c.pct_chg)}</td>
    <td>${esc(c.note)}</td></tr>`).join("") ||
    `<tr><td colspan="5" class="empty">本轮没有“强势但不宜追高”的标的。</td></tr>`;

  $("#excCount").textContent = r.excluded.length;
  $("#excludedList").innerHTML = r.excluded
    .map((c) => `<li><b>${esc(c.code)} ${esc(c.name)}</b>：${esc((c.reasons || []).slice(0, 3).join("；"))}</li>`)
    .join("") || `<li class="empty">本轮没有需要单独列出的剔除样本。</li>`;

  // 排雷剔除
  const rrBox = document.getElementById("riskRejected");
  if (rrBox) {
    const items = r.risk_rejected || [];
    rrBox.hidden = !items.length;
    rrBox.innerHTML = (items || []).map((c) => `<li><b>${esc(c.code)} ${esc(c.name)}</b>：${esc((c.reasons || []).join("；"))}</li>`).join("");
    const cnt = document.getElementById("riskCount");
    if (cnt) cnt.textContent = items.length;
    const hint = document.getElementById("riskHint");
    if (hint) hint.hidden = !!items.length;
  }

  $("#btnMd").dataset.date = r.data_date;
  $("#btnMd").hidden = r.replay || !r.files || !r.files.md;
  $("#btnDocx").hidden = r.replay || !r.files || !r.files.docx;
  bindStockLinks($("#priTable"));
  bindStockLinks($("#strongTable"));
}

function chip(k, v, tone) {
  return `<div class="market-chip"><div class="k">${k}</div><div class="v ${tone}">${v}</div></div>`;
}

/* ---------- 历史回放 ---------- */
async function startReplay() {
  if (state.running) return;
  const start = $("#replayStart").value;
  const end = $("#replayEnd").value;
  if (!start || !end || start > end) { alert("请选择有效的日期范围"); return; }
  const res = await fetchWithTimeout("/api/replay", 10000, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ start, end }),
  }).catch(() => null);
  if (!res) { alert("无法连接服务"); return; }
  if (!res.ok) { const j = await res.json().catch(() => ({})); alert(j.error || "启动回放失败"); return; }
  state.running = true;
  setStatus("running", "回放中");
  if (document.getElementById("loadingMask")) $("#loadingMask").hidden = false;
  $("#logBox").hidden = false;
  $("#logList").innerHTML = "";
  pollStatus();
}

function renderReplayResult(r) {
  if (!r || !r.days) return;
  renderReplayDays(r.days);
  appendLog(`历史回放完成：${r.days.length} 个交易日，共 ${r.total_picks} 只优先观察。点击日期查看当天候选。`);
}

function renderReplayDays(days) {
  state.replayDays = days;
  const box = $("#replayDays");
  box.hidden = false;
  box.innerHTML =
    `<div class="label">共 ${days.length} 个交易日，点击日期查看当天候选：</div>` +
    `<div class="day-chips">` +
    days.map((d) => `<span class="day-chip" data-day="${d}">${d}</span>`).join("") +
    `</div>` +
    `<div class="replay-note">基于当前股票池缓存逐日重算，仅供回顾。点击“对比”可看次日核对。</div>`;
  box.querySelectorAll(".day-chip").forEach((el) => {
    el.onclick = () => loadReplayDay(el.dataset.day, el);
  });
}

async function loadReplayDay(day, chipEl) {
  try {
    const payload = await fetchWithTimeout(`/api/replay?date=${encodeURIComponent(day)}`).then((r) => r.json());
    if (payload.error) throw new Error(payload.error);
    state.replayActive = day;
    document.querySelectorAll(".day-chip").forEach((el) => el.classList.toggle("active", el.dataset.day === day));
    renderReplayDetail(day, payload);
  } catch (e) {
    alert(e.message || "该日期没有回放数据");
  }
}

function renderReplayDetail(day, payload) {
  const box = $("#replayResult");
  const rows = (payload.priority || []).map((c) => `<tr>
    <td>${esc(c.code)}</td>
    <td>${nameCell(c.name, c.code, day, state.risks[c.code], true)}</td>
    <td class="num">${fmtNum(c.close)}</td>
    <td class="num ${pctClass(c.pct_chg)}">${fmtPct(c.pct_chg)}</td>
    <td class="num">${fmtNum(c.turnover)}%</td>
    <td>${esc(c.structure)}</td>
    <td>${esc(c.note)}</td>
    <td>${klineBtn(c.code, day, c.name)}</td>
  </tr>`).join("");
  const strongRows = (payload.strong || []).map((c) => `<tr>
    <td>${esc(c.code)}</td>
    <td>${nameCell(c.name, c.code, day, state.risks[c.code], true)}</td>
    <td class="num">${fmtNum(c.close)}</td>
    <td class="num ${pctClass(c.pct_chg)}">${fmtPct(c.pct_chg)}</td>
    <td>${esc(c.note)}</td>
    <td>${klineBtn(c.code, day, c.name)}</td>
  </tr>`).join("");
  box.innerHTML = `
    <div class="section-head"><h2>${day} 优先观察</h2><span class="count-badge">${(payload.priority || []).length}</span>
      <button class="mini-btn blue" id="btnCompare">与次日核对对比</button></div>
    <div class="table-wrap"><table>
      <thead><tr><th>代码</th><th>名称</th><th class="num">收盘</th><th class="num">涨幅</th><th class="num">换手</th><th>结构</th><th>观察要点</th><th>K线</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="8" class="empty">无候选</td></tr>`}</tbody>
    </table></div>
    <div class="section-head"><h2>${day} 强势不宜追高</h2><span class="count-badge warn">${(payload.strong || []).length}</span></div>
    <div class="table-wrap"><table>
      <thead><tr><th>代码</th><th>名称</th><th class="num">收盘</th><th class="num">涨幅</th><th>风险/处理</th><th>K线</th></tr></thead>
      <tbody>${strongRows || `<tr><td colspan="6" class="empty">无</td></tr>`}</tbody>
    </table></div>`;
  bindStockLinks(box);
  const btn = box.querySelector("#btnCompare");
  if (btn) btn.onclick = () => loadCompare(day, payload);
}

async function loadCompare(day, payload) {
  // 找 day 的下一核对日：verify 索引中比 day 大的最近一天
  try {
    const idx = await fetchWithTimeout("/api/verify").then((r) => r.json());
    const next = (idx.days || []).filter((d) => d > day)[0];
    if (!next) {
      alert(`${day} 是最后一个有数据的交易日，还没有“次日”走势可核对。核对需要等下一个交易日数据更新后才有。`);
      return;
    }
    const v = await fetchWithTimeout(`/api/verify?date=${encodeURIComponent(next)}`).then((r) => r.json());
    if (v.error) { alert(v.error); return; }
    renderCompare(day, payload, next, v);
  } catch (e) {
    alert("对比数据加载失败：" + (e.message || ""));
  }
}

function renderCompare(day, payload, next, verify) {
  const box = $("#replayResult");
  // 以核对条目为准（后端已按 红->黄->绿 排序），策略列从观察日候选里匹配
  const candByCode = {};
  [...(payload.priority || []), ...(payload.strong || [])].forEach((c) => { candByCode[c.code] = c; });
  const rowCls = {
    red: "row-red", yellow: "row-yellow", green: "row-green", gray: "", observe: "row-observe",
  };
  const rows = (verify.entries || []).map((v) => {
    const c = candByCode[v.code] || {};
    return `<tr class="${rowCls[v.group] || ""}">
      <td>${esc(v.code)}</td>
      <td>${nameCell(v.name, v.code, day, state.risks[v.code], true)}</td>
      <td class="num">${fmtNum(v.prev_close)}</td>
      <td class="num">${esc((c.note || "").split("等回踩")[0])}</td>
      <td class="num">${v.support != null ? v.support.toFixed(2) : "—"}</td>
      <td class="num ${pctClass(v.next_pct)}">${fmtPct(v.next_pct)}</td>
      <td class="num">${fmtNum(v.next_low)}</td>
      <td class="verdict-${v.group || ""}">${esc(v.detail || "—")}</td>
      <td>${klineBtn(v.code, day, v.name)}</td>
    </tr>`;
  }).join("");
  box.innerHTML = `
    <div class="card" style="background:#f8fbff">
      <div class="card-title">观察日 ${day} vs 核对日 ${next}</div>
      <div class="verify-summary">
        共 ${verify.total} 条 · <span class="rate-item rate-red">成功上涨率 ${verify.rates ? verify.rates.up_success : 0}%</span>
        <span class="rate-item rate-yellow">成功下跌率 ${verify.rates ? verify.rates.down_success : 0}%</span>
        <span class="rate-item rate-green">失败下跌率 ${verify.rates ? verify.rates.down_failed : 0}%</span>
      </div>
      <div class="table-wrap"><table>
        <thead><tr><th>代码</th><th>名称</th><th class="num">观察收盘</th><th>操作策略</th>
          <th class="num">支撑位</th><th class="num">当日涨跌</th><th class="num">当日最低</th><th>判定</th><th>K线</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="9" class="empty">无候选</td></tr>`}</tbody>
      </table></div>
      <div class="replay-note">判定口径（按您定义）：跌破支撑位=观察成功；未跌破且较观察日上涨=观察成功；未跌破且较观察日下跌=观察失败。红色=成功且上涨，黄色=成功但下跌，绿色=失败且下跌。点击日期重新选择观察日。</div>
    </div>`;
  bindStockLinks(box);
  // 重新渲染日期选择器
  renderReplayDays(state.replayDays);
}

/* ---------- 预判核对 ---------- */
async function startVerify() {
  const date = $("#verifyDate").value;
  if (!date) { alert("请选择核对日"); return; }
  await loadVerifyDay(date);
}

async function startVerifyAll() {
  if (state.running) return;
  const res = await fetchWithTimeout("/api/verify", 10000, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ start: "2026-07-01", end: "2026-08-17" }),
  }).catch(() => null);
  if (!res) { alert("无法连接服务"); return; }
  if (!res.ok) { const j = await res.json().catch(() => ({})); alert(j.error || "启动核对失败"); return; }
  state.running = true;
  setStatus("running", "核对中");
  if (document.getElementById("loadingMask")) $("#loadingMask").hidden = false;
  $("#logBox").hidden = false;
  $("#logList").innerHTML = "";
  pollStatus();
}

async function loadVerifyDay(date) {
  try {
    const payload = await fetchWithTimeout(`/api/verify?date=${encodeURIComponent(date)}`).then((r) => r.json());
    if (payload.error) throw new Error(payload.error);
    renderVerify(payload);
  } catch (e) {
    alert(e.message || "无核对数据");
  }
}

function renderVerify(p) {
  const summary = $("#verifySummary");
  summary.hidden = false;
  summary.innerHTML =
    `观察日 <b>${p.date}</b> 的候选，在 <b>${p.checked_on}</b>（下一交易日，自动跳过周末/节假日）的核对：共 ${p.total} 条。<br>` +
    `<span class="rate-item rate-red">成功上涨率 ${p.rates ? p.rates.up_success : 0}%</span>　` +
    `<span class="rate-item rate-yellow">成功下跌率 ${p.rates ? p.rates.down_success : 0}%</span>　` +
    `<span class="rate-item rate-green">失败下跌率 ${p.rates ? p.rates.down_failed : 0}%</span>`;
  const wrap = $("#verifyTableWrap");
  wrap.hidden = false;
  const tbody = $("#verifyTable tbody");
  if (!p.entries || !p.entries.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty">当天没有候选可核对。</td></tr>`;
    return;
  }
  const cls = {
    red: "verdict-red", yellow: "verdict-yellow", green: "verdict-green",
    gray: "verdict-gray", observe: "verdict-gray",
  };
  const rowCls = {
    red: "row-red", yellow: "row-yellow", green: "row-green", gray: "", observe: "row-observe",
  };
  tbody.innerHTML = p.entries.map((e) => `<tr class="${rowCls[e.group] || ""}">
    <td>${esc(e.code)}</td>
    <td>${nameCell(e.name, e.code, p.checked_on, state.risks[e.code], true)}</td>
    <td class="num">${fmtNum(e.prev_close)}</td>
    <td class="num">${e.support != null ? e.support.toFixed(2) : "—"}</td>
    <td class="num">${fmtNum(e.next_close)}</td>
    <td class="num ${pctClass(e.next_pct)}">${fmtPct(e.next_pct)}</td>
    <td class="num">${fmtNum(e.next_low)}</td>
    <td class="${cls[e.group] || ""}">${esc(e.detail || "—")}</td>
    <td>${esc(e.suggestion)}</td>
    <td>${klineBtn(e.code, p.checked_on, e.name)}</td>
  </tr>`).join("");
  bindStockLinks($("#verifyTable"));
}

/* ---------- 缠论研判 ---------- */
async function loadSignal() {
  const box = $("#signalResult");
  box.innerHTML = `<div class="empty">加载缠论研判中…</div>`;
  try {
    const d = await fetchWithTimeout("/api/market-signal").then((r) => r.json());
    if (d.error) throw new Error(d.error);
    const tone = d.verdict === "适合入场" ? "up" : d.verdict === "不适合入场" ? "down" : "";
    box.innerHTML = `
      <div class="signal-hero">
        <div class="sh-title">${d.date} · 缠论大盘研判</div>
        <div class="sh-verdict ${tone}">${esc(d.verdict)}</div>
        <div class="sh-advice">${esc(d.advice)}</div>
      </div>
      <div class="signal-grid">
        ${(d.indices || []).map((i) => {
          const zs = i.zhongshu;
          return `<div class="signal-card">
            <div class="sc-name">${esc(i.name)} <span class="sc-trend">${esc(i.trend)}</span></div>
            <div class="sc-price ${pctClass(i.pct_chg)}">${fmtNum(i.close)} <small>${fmtPct(i.pct_chg)}</small></div>
            <div class="sc-meta">MA5 ${fmtNum(i.ma5)} · MA10 ${fmtNum(i.ma10)} · MA20 ${fmtNum(i.ma20)}</div>
            <div class="sc-meta">位置：${esc(i.position)}</div>
            ${zs ? `<div class="sc-zs">缠论中枢：ZG ${zs.zg} / ZD ${zs.zd}（${zs.pens}笔）</div>` : ""}
          </div>`;
        }).join("")}
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="empty">${esc(e.message || "研判失败")}</div>`;
  }
  loadWatchSectors();
}

async function loadWatchSectors() {
  const box = $("#watchSectors");
  try {
    const d = await fetchWithTimeout("/api/watch-sectors?pick=5").then((r) => r.json());
    if (d.error) throw new Error(d.error);
    box.innerHTML = (d.rows || []).map((r) => `<div class="sector-row">
      <div class="sr-rank">${r.rank}</div>
      <div class="sr-main">
        <div class="sr-name">${esc(r.name)} <span class="sub">龙头：${esc(r.leader || "—")}</span></div>
        <div class="sr-reason">${esc(r.reason)}</div>
      </div>
      <div class="sr-pct ${pctClass(r.pct_chg)}">${fmtPct(r.pct_chg)}</div>
    </div>`).join("") || `<div class="empty">暂无观察行业</div>`;
  } catch (e) {
    box.innerHTML = `<div class="empty">${esc(e.message || "行业数据失败")}</div>`;
  }
}

/* ---------- 财经新闻 ---------- */
async function loadNews() {
  const box = $("#newsList");
  box.innerHTML = `<div class="empty">加载财经快讯中…</div>`;
  try {
    const d = await fetchWithTimeout("/api/news").then((r) => r.json());
    if (d.error) throw new Error(d.error);
    const tags = $("#newsTags");
    if ((d.hot_tags || []).length) {
      tags.hidden = false;
      tags.innerHTML = (d.hot_tags || []).map((t) =>
        `<span class="news-tag">${esc(t.tag)} × ${t.count}</span>`).join("");
    }
    box.innerHTML = (d.items || []).map((it) => `<div class="news-item">
      <div class="n-title">${esc(it.title)}</div>
      <div class="n-time">${esc(it.time || "")}</div>
      ${it.summary && it.summary !== it.title ? `<div class="n-summary">${esc(it.summary)}</div>` : ""}
      <div class="n-tags">${(it.tags || []).map((t) => `<span>${esc(t)}</span>`).join("")}</div>
    </div>`).join("") || `<div class="empty">暂无新闻</div>`;
  } catch (e) {
    box.innerHTML = `<div class="empty">${esc(e.message || "新闻获取失败")}</div>`;
  }
}

/* ---------- 自选股 ---------- */
async function loadWatchlist() {
  try {
    const data = await fetchWithTimeout("/api/userdata").then((r) => r.json());
    const list = data.watchlist || [];
    const box = $("#watchList");
    if (!list.length) { box.innerHTML = `<div class="empty">还没有自选股，添加一只试试吧。</div>`; return; }
    box.innerHTML = list.map((w) => `
      <div class="watch-item">
        <div class="watch-main"><b>${esc(w.name)}</b>${riskBadges(state.risks[w.code])}<span class="sub">${esc(w.code)}</span>
          <span class="watch-alert">${w.alert_price ? (w.alert_type === "above" ? "突破 " : "跌破 ") + w.alert_price : "未设提醒"}</span></div>
        <div class="watch-actions">
          <a class="watch-link" data-code="${esc(w.code)}" data-name="${esc(w.name)}">K线</a>
          <a class="watch-del" data-code="${esc(w.code)}">删除</a>
        </div>
      </div>`).join("");
    box.querySelectorAll(".watch-link").forEach((el) => {
      el.onclick = (e) => { e.preventDefault(); openModal(el.dataset.code, state.result?.data_date || "", el.dataset.name); };
    });
    box.querySelectorAll(".watch-del").forEach((el) => {
      el.onclick = async (e) => {
        e.preventDefault();
        await fetchWithTimeout(`/api/watchlist?action=remove&code=${encodeURIComponent(el.dataset.code)}`).then((r) => r.json());
        loadWatchlist();
      };
    });
  } catch (e) { /* 静默 */ }
}

/* ---------- 持仓 ---------- */
async function loadHoldings() {
  try {
    const data = await fetchWithTimeout("/api/userdata").then((r) => r.json());
    const positions = data.portfolio.positions || [];
    const trades = (data.portfolio.trades || []).slice().reverse();
    let quotes = {};
    if (positions.length) {
      quotes = await fetchWithTimeout(`/api/quotes?codes=${encodeURIComponent(positions.map((p) => p.code).join(","))}`)
        .then((r) => r.json());
    }
    let mv = 0;
    const rows = positions.map((p) => {
      const close = quotes[p.code] ? quotes[p.code].close : p.cost;
      const marketV = close * p.shares;
      const pnl = (close - p.cost) * p.shares;
      const rate = p.cost ? (close - p.cost) / p.cost * 100 : 0;
      mv += marketV;
      return `<tr>
        <td>${esc(p.code)}</td>
        <td>${nameCell(p.name, p.code, "", state.risks[p.code])}</td>
        <td class="num">${close.toFixed(2)}</td><td class="num">${p.cost.toFixed(2)}</td>
        <td class="num">${p.shares}</td><td class="num">${marketV.toFixed(0)}</td>
        <td class="num ${pnl >= 0 ? "pct-up" : "pct-down"}">${pnl >= 0 ? "+" : ""}${pnl.toFixed(0)}</td>
        <td class="num ${rate >= 0 ? "pct-up" : "pct-down"}">${rate >= 0 ? "+" : ""}${rate.toFixed(2)}%</td>
        <td>${klineBtn(p.code, "", p.name)}</td>
        <td><button class="mini-btn blue" data-code="${esc(p.code)}" data-name="${esc(p.name)}" data-price="${close}">卖出</button>
            <button class="mini-btn danger" data-code="${esc(p.code)}">平仓</button></td>
      </tr>`;
    });
    $("#positionTable tbody").innerHTML = rows.join("") || `<tr><td colspan="10" class="empty">暂无持仓</td></tr>`;
    $("#tradeTable tbody").innerHTML = trades.map((t) => `<tr>
      <td>${esc(t.date)}</td><td>${esc(t.code)}</td><td>${esc(t.name)}</td>
      <td class="${t.action === "buy" ? "pct-up" : "pct-down"}">${t.action === "buy" ? "买入" : "卖出"}</td>
      <td class="num">${t.price.toFixed(2)}</td><td class="num">${t.shares}</td>
      <td class="num">${(t.price * t.shares).toFixed(0)}</td></tr>`).join("") ||
      `<tr><td colspan="7" class="empty">暂无交易记录</td></tr>`;
    const cash = data.portfolio.cash || 0;
    const total = mv + cash;
    const pnl = total - 100000;
    $("#holdingSummary").hidden = false;
    $("#holdingSummary").innerHTML = `
      <div class="index-item"><b>总资产</b> ${total.toFixed(0)}</div>
      <div class="index-item"><b>总盈亏</b> <span class="${pnl >= 0 ? "pct-up" : "pct-down"}">${pnl >= 0 ? "+" : ""}${pnl.toFixed(0)}</span></div>
      <div class="index-item"><b>可用资金</b> ${cash.toFixed(0)}</div>`;
    bindStockLinks($("#positionTable"));
    $("#positionTable").querySelectorAll(".blue").forEach((el) => {
      el.onclick = () => {
        $("#tradeCode").value = el.dataset.code;
        $("#tradeName").value = el.dataset.name;
        $("#tradePrice").value = el.dataset.price;
        $("#tradeAction").value = "sell";
      };
    });
    $("#positionTable").querySelectorAll(".danger").forEach((el) => {
      el.onclick = async () => {
        if (!confirm("确认平仓 " + el.dataset.code + "？")) return;
        await fetchWithTimeout("/api/close", 8000, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: el.dataset.code }),
        }).then((r) => r.json());
        loadHoldings();
      };
    });
  } catch (e) { /* 静默 */ }
}

/* ---------- 板块 ---------- */
async function loadSectors() {
  try {
    const data = await fetchWithTimeout("/api/sectors").then((r) => r.json());
    const rows = data.rows || [];
    $("#sectorTable tbody").innerHTML = rows.map((r, i) => `<tr>
      <td class="num">${i + 1}</td>
      <td><b>${esc(r.name)}</b></td>
      <td class="num ${pctClass(r.pct_chg)}">${fmtPct(r.pct_chg)}</td>
      <td>${esc(r.leader || "—")}</td></tr>`).join("") || `<tr><td colspan="4" class="empty">暂无板块数据</td></tr>`;
  } catch (e) {
    $("#sectorTable tbody").innerHTML = `<tr><td colspan="4" class="empty">板块数据获取失败</td></tr>`;
  }
}

/* ---------- 个股详情 + K线 ---------- */
async function openModal(code, date, name) {
  state.modal = { code, date, name, klt: 101, chan: null };
  $("#modalMask").hidden = false;
  $("#modalTitle").textContent = `${name} ${code}`;
  $("#modalSub").textContent = `数据日期 ${date || "最近"} · 日K 前复权`;
  $("#modalInd").innerHTML = "加载中…";
  $("#chanBadge").hidden = true;
  document.querySelectorAll(".klt-tab").forEach((el) => el.classList.toggle("active", Number(el.dataset.klt) === 101));
  const canvas = $("#klineCanvas");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  try {
    const data = await fetchWithTimeout(`/api/kline?code=${encodeURIComponent(code)}&date=${encodeURIComponent(date || "")}&klt=101`)
      .then((r) => r.json());
    if (data.error) throw new Error(data.error);
    state.modal.chan = data.chanlun || null;
    renderModal(data);
  } catch (e) {
    $("#modalInd").innerHTML = `<div style="color:#e03131;font-size:13px">${esc(e.message)}</div>`;
  }
}

document.querySelectorAll(".klt-tab").forEach((el) => {
  el.onclick = async () => {
    const klt = Number(el.dataset.klt);
    const m = state.modal;
    if (!m.code || klt === m.klt) return;
    m.klt = klt;
    document.querySelectorAll(".klt-tab").forEach((x) => x.classList.toggle("active", Number(x.dataset.klt) === klt));
    $("#modalSub").textContent = `数据日期 ${m.date || "最近"} · ${klt === 101 ? "日K" : klt + "分钟"} 前复权`;
    $("#modalInd").innerHTML = "加载中…";
    try {
      const data = await fetchWithTimeout(`/api/kline?code=${encodeURIComponent(m.code)}&date=${encodeURIComponent(m.date || "")}&klt=${klt}`)
        .then((r) => r.json());
      if (data.error) throw new Error(data.error);
      m.chan = data.chanlun || null;
      renderModal(data);
    } catch (e) {
      $("#modalInd").innerHTML = `<div style="color:#e03131;font-size:13px">${esc(e.message)}</div>`;
    }
  };
});

function renderModal(data) {
  const canvas = $("#klineCanvas");
  renderInd(data);
  drawKline(canvas, data.bars, data.chanlun || null);
}

function renderInd(data) {
  const last = (data.bars || [])[data.bars.length - 1];
  const ind = data.ind || {};
  const cells = {
    "收盘": last ? last.close : null,
    "涨幅": ind.pct_chg, "MA5": ind.ma5, "MA10": ind.ma10, "MA20": ind.ma20,
    "DIF": ind.dif, "DEA": ind.dea, "MACD柱": ind.hist,
    "K": ind.k, "D": ind.d, "J": ind.j,
    "5日涨幅": ind.gain_5d, "10日涨幅": ind.gain_10d,
  };
  $("#modalInd").innerHTML = Object.entries(cells)
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => `<div class="ind-cell"><div class="k">${k}</div><div class="v">${fmtNum(v, 2)}</div></div>`)
    .join("");
  const chan = data.chanlun;
  const badge = $("#chanBadge");
  if (chan && chan.zhongshu) {
    badge.hidden = false;
    badge.textContent = `缠论中枢 ZG ${chan.zhongshu.zg} / ZD ${chan.zhongshu.zd}`;
  } else {
    badge.hidden = true;
  }
  $("#modalNote").textContent = "等回踩 MA5/MA10 附近不破并转强，可短线关注；跌破 MA10 且无法收回则放弃。";
}

function drawKline(canvas, bars, chan) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 720;
  const h = canvas.clientHeight || 340;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (!bars || !bars.length) return;

  const padL = 12, padR = 12, padT = 10;
  const priceH = h * 0.68, volH = h * 0.18, gap = 8;
  const priceBottom = padT + priceH;
  const volTop = priceBottom + gap;
  const volBottom = h - 8;

  const lows = bars.map((b) => b.low);
  const highs = bars.map((b) => b.high);
  const maVals = ["ma5", "ma10", "ma20"].map((k) => bars.map((b) => b[k]).filter((v) => v !== null));
  const all = lows.concat(highs).concat(...maVals).filter((v) => v !== null && v > 0);
  let minP = Math.min(...all), maxP = Math.max(...all);
  const pad = (maxP - minP) * 0.06 || 1;
  minP -= pad; maxP += pad;

  const n = bars.length;
  const step = (w - padL - padR) / n;
  const bw = Math.max(1.5, step * 0.62);
  const px = (i) => padL + step * i + step / 2;
  const py = (v) => padT + (maxP - v) / (maxP - minP) * priceH;
  const maxVol = Math.max(...bars.map((b) => b.volume), 1);

  ctx.strokeStyle = "#eef1f5";
  ctx.lineWidth = 1;
  for (let g = 0; g <= 4; g++) {
    const y = padT + (priceH / 4) * g;
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
  }

  bars.forEach((b, i) => {
    const up = b.close >= b.open;
    const color = up ? "#e03131" : "#2f9e44";
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 1;
    const x = px(i);
    ctx.beginPath(); ctx.moveTo(x, py(b.high)); ctx.lineTo(x, py(b.low)); ctx.stroke();
    const yo = py(b.open), yc = py(b.close);
    const top = Math.min(yo, yc);
    const bh = Math.max(Math.abs(yo - yc), 1);
    ctx.fillRect(x - bw / 2, top, bw, bh);
    const vh = (b.volume / maxVol) * (volBottom - volTop);
    ctx.fillStyle = up ? "rgba(224,49,49,.45)" : "rgba(47,158,68,.45)";
    ctx.fillRect(x - bw / 2, volBottom - vh, bw, vh);
  });

  const maColors = { ma5: "#f59f00", ma10: "#1971c2", ma20: "#9c36b5" };
  Object.entries(maColors).forEach(([key, color]) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    let started = false;
    bars.forEach((b, i) => {
      const v = b[key];
      if (v === null) return;
      const x = px(i), y = py(v);
      if (!started) { ctx.moveTo(x, y); started = true; }
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });

  ctx.font = "11px sans-serif";
  ctx.textBaseline = "top";
  let lx = padL;
  Object.entries(maColors).forEach(([key, color]) => {
    const label = key.toUpperCase();
    ctx.fillStyle = color;
    ctx.fillText(label, lx, 1);
    lx += ctx.measureText(label).width + 12;
  });
  ctx.fillStyle = "#6b7684";
  ctx.fillText("红涨 绿跌", w - padR - 60, 1);

  const last = bars[n - 1];
  const lastY = py(last.close);
  ctx.fillStyle = last.close >= last.open ? "#e03131" : "#2f9e44";
  ctx.fillText(last.close.toFixed(2), w - padR - 58, Math.max(2, lastY - 14));

  if (chan && chan.levels && chan.levels.length) {
    ctx.font = "10px sans-serif";
    chan.levels.forEach((lv) => {
      const y = py(lv.price);
      ctx.strokeStyle = lv.type === "zg" ? "#9c36b5" : "#f59f00";
      ctx.lineWidth = 1.2;
      ctx.setLineDash([6, 4]);
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = lv.type === "zg" ? "#9c36b5" : "#f59f00";
      ctx.fillText(`${lv.type.toUpperCase()} ${lv.price.toFixed(2)}`, padL + 2, y - 3);
    });
  }
}

/* ---------- 下载 ---------- */
function download(type) {
  const r = state.result;
  if (!r) return;
  if (STATIC) {
    const f = r.files && (type === "md" ? r.files.md : r.files.docx);
    if (f) {
      const a = document.createElement("a");
      a.href = f;
      a.download = "";
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
    return;
  }
  const a = document.createElement("a");
  a.href = `/api/download?type=${type}&date=${encodeURIComponent(r.data_date)}`;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/* ---------- 事件绑定 ---------- */
$("#btnRun").onclick = startRun;
$("#btnReplay").onclick = startReplay;
$("#btnVerify").onclick = startVerify;
$("#btnVerifyAll").onclick = startVerifyAll;
$("#btnSignal").onclick = loadSignal;
$("#btnNews").onclick = loadNews;
$("#btnSectors").onclick = loadSectors;
$("#btnReset").onclick = () => { try { localStorage.removeItem(LS_KEY); } catch (e) {} location.reload(); };
$("#btnMd").onclick = () => download("md");
$("#btnDocx").onclick = () => download("docx");
$("#modalClose").onclick = () => { $("#modalMask").hidden = true; };
$("#modalMask").onclick = (e) => { if (e.target === $("#modalMask")) $("#modalMask").hidden = true; };

$("#btnAddWatch").onclick = async () => {
  const code = $("#watchCode").value.trim();
  const name = $("#watchName").value.trim();
  const price = $("#watchPrice").value;
  const type = $("#watchType").value;
  if (!/^\d{6}$/.test(code)) { alert("请输入 6 位股票代码"); return; }
  const res = await fetchWithTimeout("/api/watchlist", 8000, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, name: name || code, alert_price: price ? Number(price) : null, alert_type: type }),
  }).then((r) => r.json());
  alert(res.msg || "已加入");
  $("#watchCode").value = ""; $("#watchName").value = ""; $("#watchPrice").value = "";
  loadWatchlist();
};

$("#btnTrade").onclick = async () => {
  const code = $("#tradeCode").value.trim();
  const name = $("#tradeName").value.trim();
  const price = Number($("#tradePrice").value);
  const shares = Number($("#tradeShares").value);
  const action = $("#tradeAction").value;
  if (!/^\d{6}$/.test(code)) { alert("请输入 6 位代码"); return; }
  if (!price || !shares || shares % 100 !== 0) { alert("价格需有效，数量为 100 的整数倍"); return; }
  const res = await fetchWithTimeout("/api/trade", 8000, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, name: name || code, action, price, shares }),
  }).then((r) => r.json());
  alert(res.msg || (res.ok ? "成功" : "失败"));
  $("#tradeCode").value = ""; $("#tradeName").value = ""; $("#tradePrice").value = ""; $("#tradeShares").value = "";
  loadHoldings();
};

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (window.dismissLoading) window.dismissLoading();
    $("#modalMask").hidden = true;
  }
});
window.addEventListener("focus", () => { pollStatus(); });

["inputDate", "inputTop", "inputProxy", "inputRefresh", "inputIntraday"].forEach((id) => {
  $(`#${id}`).addEventListener("change", saveParams);
});

init();
