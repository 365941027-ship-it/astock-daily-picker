const api = require("../../utils/config");
const { fmtPct } = require("../../utils/format");

Page({
  data: {
    date: "",
    top: "8",
    proxy: "",
    running: false,
    logs: [],
    result: null,
    market: {},
    indices: [],
    pollTimer: null,
  },

  onLoad() {
    const now = new Date();
    const d = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    this.setData({ date: d });
  },

  onUnload() {
    this.clearPoll();
  },

  onDateChange(e) { this.setData({ date: e.detail.value }); },
  onTopInput(e) { this.setData({ top: e.detail.value }); },
  onProxyInput(e) { this.setData({ proxy: e.detail.value }); },

  clearPoll() {
    if (this.data.pollTimer) {
      clearInterval(this.data.pollTimer);
      this.setData({ pollTimer: null });
    }
  },

  appendLog(msg) {
    const now = new Date();
    const t = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}`;
    const logs = this.data.logs.slice(-200);
    logs.push({ t, m: msg });
    this.setData({ logs });
  },

  async startRun() {
    if (this.data.running) return;
    const date = this.data.date;
    if (!date) {
      wx.showToast({ title: "请选择日期", icon: "none" });
      return;
    }
    try {
      await api.startPick({
        date,
        top: Number(this.data.top) || 8,
        proxy: this.data.proxy.trim() || null,
      });
    } catch (e) {
      wx.showToast({ title: e.message || "启动失败", icon: "none" });
      return;
    }
    this.setData({ running: true, logs: [], result: null, market: {}, indices: [] });
    this.clearPoll();
    let polls = 0;
    this.setData({
      pollTimer: setInterval(() => {
        polls += 1;
        // 安全上限：最多轮询 5 分钟，防止异常情况下无限轮询拖垮模拟器
        if (polls > 200) {
          this.clearPoll();
          this.setData({ running: false });
          this.appendLog("轮询超时，已停止。请检查后端服务是否在运行。");
          return;
        }
        this.syncStatus();
      }, 1500),
    });
    this.syncStatus();
  },

  async syncStatus() {
    try {
      const st = await api.status();
      if (st.logs && st.logs.length) {
        const last = st.logs[st.logs.length - 1];
        const has = this.data.logs.some((l) => l.t === last.t && l.m === last.m);
        if (!has) {
          const logs = this.data.logs.slice(-200);
          logs.push(last);
          this.setData({ logs });
        }
      }
      if (st.state === "running") {
        this.setData({ running: true });
        return;
      }
      this.clearPoll();
      if (st.state === "done") {
        this.setData({ running: false });
        if (st.kind !== "replay") this.renderResult(st.result);
      } else if (st.state === "error") {
        this.setData({ running: false });
        this.appendLog("错误：" + (st.error || "未知错误"));
      } else {
        this.setData({ running: false });
      }
    } catch (e) {
      // 轮询失败静默，等待下一轮
    }
  },

  renderResult(r) {
    if (!r) return;
    const enrich = (list) => (list || []).map((c) => ({
      ...c,
      pct_text: fmtPct(c.pct_chg),
      reasons_text: (c.reasons || []).slice(0, 3).join("；"),
    }));
    const indices = Object.entries(r.indices || {}).map(([name, v]) => ({
      name,
      close: v.close,
      pct_chg: v.pct_chg,
      pct_text: fmtPct(v.pct_chg),
    }));
    this.setData({
      heroTitle: `A股市场 · ${r.data_date}`,
      heroSub: r.channel ? `数据通道：${r.channel}` : "数据通道：本地缓存",
      result: {
        ...r,
        priority: enrich(r.priority),
        strong: enrich(r.strong),
        excluded: enrich(r.excluded),
      },
      market: r.market || {},
      indices,
    });
    if (r.intraday) {
      this.appendLog("提示：当前为盘中数据（预览模式），盘后重跑出正式候选。");
    }
  },

  openDetail(e) {
    const { code, date, name } = e.currentTarget.dataset;
    wx.navigateTo({ url: `/pages/detail/detail?code=${code}&date=${date}&name=${encodeURIComponent(name)}` });
  },

  goSectors() {
    wx.navigateTo({ url: "/pages/sectors/sectors" });
  },

  subscribeDaily() {
    // 正式上线时需要真实订阅消息模板 ID；测试号仅演示交互
    wx.requestSubscribeMessage({
      tmplIds: [],
      fail: (err) => {
        if (err && err.errMsg && err.errMsg.includes("invalid template id")) {
          wx.showModal({
            title: "订阅推送",
            content: "测试号无法使用订阅消息。正式发布时，配置模板ID后，每天盘后即可收到候选推送。",
            showCancel: false,
          });
        }
      },
      complete: () => {
        wx.showModal({
          title: "订阅推送",
          content: "功能已就绪：正式发布时配置微信订阅消息模板ID，每天18:05盘后自动推送候选。",
          showCancel: false,
        });
      },
    });
  },
});
