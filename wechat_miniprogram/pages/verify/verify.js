const api = require("../../utils/config");
const { fmtPct, fmtNum } = require("../../utils/format");

Page({
  data: {
    date: "",
    overview: null,
    summary: null,
    entries: [],
    loaded: false,
  },

  onLoad() {
    const now = new Date();
    const d = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    this.setData({ date: d });
    this.loadOverview();
  },

  onDateChange(e) { this.setData({ date: e.detail.value }); },

  async loadOverview() {
    try {
      const idx = await api.verifyIndex();
      const totalChecked = (idx.total_valid || 0) + (idx.total_failed || 0);
      const rate = totalChecked ? Math.round(((idx.total_valid || 0) / totalChecked) * 100) : 0;
      this.setData({
        overview: {
          total_checked: totalChecked,
          total_valid: idx.total_valid || 0,
          total_failed: idx.total_failed || 0,
          rate,
        },
      });
    } catch (e) {
      // 无核对数据时静默，不显示总览
    }
  },

  async loadVerify() {
    const date = this.data.date;
    if (!date) {
      wx.showToast({ title: "请选择核对日", icon: "none" });
      return;
    }
    wx.showLoading({ title: "核对中…" });
    try {
      const p = await api.verify(date);
      const cls = {
        valid: "verdict-valid", failed: "verdict-failed",
        strong: "verdict-strong", weak: "verdict-weak", neutral: "verdict-neutral",
      };
      const entries = (p.entries || []).map((e) => ({
        ...e,
        next_pct_text: fmtPct(e.next_pct),
        support: e.support != null ? e.support.toFixed(2) : "—",
        prev_close: fmtNum(e.prev_close),
        next_close: fmtNum(e.next_close),
        next_low: fmtNum(e.next_low),
        verdict_class: cls[e.verdict] || "verdict-neutral",
      }));
      this.setData({
        summary: { date: p.date, checked_on: p.checked_on, total: p.total, valid: p.valid, failed: p.failed },
        entries,
        loaded: true,
      });
    } catch (e) {
      this.setData({ summary: null, entries: [], loaded: true });
      wx.showToast({ title: e.message || "无核对数据", icon: "none" });
    } finally {
      wx.hideLoading();
    }
  },
});
