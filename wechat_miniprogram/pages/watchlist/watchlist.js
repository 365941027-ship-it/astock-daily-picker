const api = require("../../utils/config");
const { fmtPct, fmtNum } = require("../../utils/format");

Page({
  data: {
    code: "",
    name: "",
    alertPrice: "",
    alertType: "below",
    list: [],
    defaultDate: "",
  },

  onLoad() {
    const now = new Date();
    this.setData({
      defaultDate: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`,
    });
    this.refresh();
  },

  onPullDownRefresh() {
    this.refresh().finally(() => wx.stopPullDownRefresh());
  },

  async refresh() {
    try {
      const data = await api.userdata();
      const list = (data.watchlist || []).map((w) => ({ ...w }));
      this.setData({ list });
      this.refreshQuotes();
    } catch (e) {
      wx.showToast({ title: "加载失败", icon: "none" });
    }
  },

  async refreshQuotes() {
    // 用选股结果缓存补全自选股最新行情
    try {
      const st = await api.status();
      const r = st.result;
      if (!r) return;
      const quotes = {};
      [...r.priority, ...r.strong].forEach((c) => {
        quotes[c.code] = c;
      });
      const list = this.data.list.map((w) => {
        const q = quotes[w.code];
        if (!q) return w;
        const triggered = q.pct_chg !== null && q.pct_chg !== undefined &&
          ((w.alert_type === "below" && q.pct_chg <= (w.alert_price || -999)) ||
           (w.alert_type === "above" && q.pct_chg >= (w.alert_price || 999)));
        return {
          ...w,
          close: fmtNum(q.close),
          pct_chg: q.pct_chg,
          pct_text: fmtPct(q.pct_chg),
          triggered,
        };
      });
      this.setData({ list });
      const hit = list.filter((w) => w.triggered);
      if (hit.length) {
        wx.showToast({ title: `提醒：${hit.map((h) => h.name).join("、")} 触发条件`, icon: "none" });
      }
    } catch (e) {
      // 行情刷新失败不影响列表
    }
  },

  onCodeInput(e) { this.setData({ code: e.detail.value }); },
  onNameInput(e) { this.setData({ name: e.detail.value }); },
  onAlertInput(e) { this.setData({ alertPrice: e.detail.value }); },
  setAlertType(e) { this.setData({ alertType: e.currentTarget.dataset.type }); },

  async addWatch() {
    const code = this.data.code.trim();
    const name = this.data.name.trim();
    if (!/^\d{6}$/.test(code)) {
      wx.showToast({ title: "请输入6位股票代码", icon: "none" });
      return;
    }
    const alertPrice = this.data.alertPrice ? Number(this.data.alertPrice) : null;
    if (alertPrice !== null && (isNaN(alertPrice) || alertPrice <= 0)) {
      wx.showToast({ title: "提醒价格式错误", icon: "none" });
      return;
    }
    try {
      const res = await api.addWatch(code, name || code, alertPrice, this.data.alertType);
      wx.showToast({ title: res.msg || "已加入", icon: "success" });
      this.setData({ code: "", name: "", alertPrice: "" });
      this.refresh();
    } catch (e) {
      wx.showToast({ title: e.message || "添加失败", icon: "none" });
    }
  },

  removeWatch(e) {
    const code = e.currentTarget.dataset.code;
    wx.showModal({
      title: "删除自选",
      content: `确认删除 ${code}？`,
      success: async (res) => {
        if (!res.confirm) return;
        try {
          await api.removeWatch(code);
          this.refresh();
        } catch (err) {
          wx.showToast({ title: "删除失败", icon: "none" });
        }
      },
    });
  },

  openDetail(e) {
    const { code, date, name } = e.currentTarget.dataset;
    wx.navigateTo({ url: `/pages/detail/detail?code=${code}&date=${date}&name=${encodeURIComponent(name)}` });
  },
});
