const api = require("../../utils/config");
const { fmtPct } = require("../../utils/format");

Page({
  data: { list: [], loaded: false },

  onLoad() {
    this.loadSectors();
  },

  onPullDownRefresh() {
    this.loadSectors().finally(() => wx.stopPullDownRefresh());
  },

  async loadSectors() {
    wx.showLoading({ title: "加载中…" });
    try {
      const data = await api.sectors();
      const list = (data.rows || []).map((r) => ({
        ...r,
        pct_text: fmtPct(r.pct_chg),
      }));
      this.setData({ list, loaded: true });
    } catch (e) {
      this.setData({ list: [], loaded: true });
      wx.showToast({ title: e.message || "加载失败", icon: "none" });
    } finally {
      wx.hideLoading();
    }
  },
});
