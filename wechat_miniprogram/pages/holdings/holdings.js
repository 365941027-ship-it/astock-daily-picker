const api = require("../../utils/config");
const { fmtNum } = require("../../utils/format");

Page({
  data: {
    positions: [],
    trades: [],
    summary: null,
    defaultDate: "",
    trade: { code: "", name: "", price: "", shares: "", action: "buy" },
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
      const positions = data.portfolio.positions || [];
      const trades = (data.portfolio.trades || []).slice().reverse();
      const codes = positions.map((p) => p.code);
      let quotes = {};
      if (codes.length) {
        quotes = await api.quotes(codes.join(","));
      }
      let marketValue = 0;
      const enriched = positions.map((p) => {
        const close = quotes[p.code] ? quotes[p.code].close : p.cost;
        const mv = close * p.shares;
        const pnl = (close - p.cost) * p.shares;
        const rate = p.cost ? (close - p.cost) / p.cost * 100 : 0;
        marketValue += mv;
        return {
          ...p,
          close,
          close_text: fmtNum(close),
          market_value_text: mv.toFixed(0),
          pnl,
          pnl_text: (pnl >= 0 ? "+" : "") + pnl.toFixed(0),
          pnl_rate_text: (rate >= 0 ? "+" : "") + rate.toFixed(2) + "%",
        };
      });
      const cash = data.portfolio.cash || 0;
      const totalValue = marketValue + cash;
      const pnl = totalValue - 100000;
      const tradesEnriched = trades.map((t) => ({
        ...t,
        uid: `${t.date}_${t.code}_${t.action}_${t.shares}_${t.price}`,
        amount_text: (t.price * t.shares).toFixed(0),
      }));
      this.setData({
        positions: enriched,
        trades: tradesEnriched,
        summary: {
          total_value_text: totalValue.toFixed(0),
          pnl,
          pnl_text: (pnl >= 0 ? "+" : "") + pnl.toFixed(0),
          cash_text: cash.toFixed(0),
        },
      });
    } catch (e) {
      wx.showToast({ title: e.message || "加载失败", icon: "none" });
    }
  },

  onTradeCode(e) { this.setData({ "trade.code": e.detail.value }); },
  onTradeName(e) { this.setData({ "trade.name": e.detail.value }); },
  onTradePrice(e) { this.setData({ "trade.price": e.detail.value }); },
  onTradeShares(e) { this.setData({ "trade.shares": e.detail.value }); },
  setAction(e) { this.setData({ "trade.action": e.currentTarget.dataset.action }); },

  async submitTrade() {
    const t = this.data.trade;
    const code = t.code.trim();
    if (!/^\d{6}$/.test(code)) {
      wx.showToast({ title: "请输入6位代码", icon: "none" });
      return;
    }
    const price = Number(t.price);
    const shares = Number(t.shares);
    if (!price || !shares || shares % 100 !== 0) {
      wx.showToast({ title: "价格有效且数量为100的整数倍", icon: "none" });
      return;
    }
    try {
      const res = await api.trade(code, t.name.trim() || code, t.action, price, shares);
      wx.showToast({ title: res.msg || "成功", icon: "none" });
      this.setData({ trade: { code: "", name: "", price: "", shares: "", action: "buy" } });
      this.refresh();
    } catch (e) {
      wx.showToast({ title: e.message || "交易失败", icon: "none" });
    }
  },

  openSell(e) {
    const { code, name, shares, price } = e.currentTarget.dataset;
    this.setData({
      trade: { code, name, price: String(price), shares: "", action: "sell" },
    });
    wx.pageScrollTo({ scrollTop: 0, duration: 300 });
    wx.showToast({ title: `可卖 ${shares} 股`, icon: "none" });
  },

  closePos(e) {
    const code = e.currentTarget.dataset.code;
    wx.showModal({
      title: "确认平仓",
      content: `平掉 ${code} 全部持仓？`,
      success: async (res) => {
        if (!res.confirm) return;
        try {
          await api.closePosition(code);
          this.refresh();
        } catch (err) {
          wx.showToast({ title: "平仓失败", icon: "none" });
        }
      },
    });
  },

  openDetail(e) {
    const { code, date, name } = e.currentTarget.dataset;
    wx.navigateTo({ url: `/pages/detail/detail?code=${code}&date=${date}&name=${encodeURIComponent(name)}` });
  },
});
