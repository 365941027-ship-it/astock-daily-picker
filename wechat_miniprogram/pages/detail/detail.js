const api = require("../../utils/config");
const { fmtNum } = require("../../utils/format");

Page({
  data: {
    code: "",
    date: "",
    name: "",
    indList: [],
    note: "",
    klt: 101,
    chanlun: {},
  },

  onLoad(options) {
    this.setData({
      code: options.code || "",
      date: options.date || "",
      name: decodeURIComponent(options.name || ""),
    });
    this.loadData();
  },

  async loadData() {
    const { code, date, klt } = this.data;
    wx.showLoading({ title: "加载中…" });
    try {
      const data = await api.kline(code, date, klt);
      this.renderInd(data);
      this.drawKline(data.bars || []);
      this.setData({ chanlun: data.chanlun || {} });
      this.chanData = data;
    } catch (e) {
      wx.showToast({ title: e.message || "加载失败", icon: "none" });
    } finally {
      wx.hideLoading();
    }
  },

  setKlt(e) {
    const klt = Number(e.currentTarget.dataset.klt);
    if (klt === this.data.klt) return;
    this.setData({ klt, chanlun: {} });
    this.loadData();
  },

  renderInd(data) {
    const ind = data.ind || {};
    const cells = {
      "收盘": ind.close, "涨幅": ind.pct_chg,
      "MA5": ind.ma5, "MA10": ind.ma10, "MA20": ind.ma20,
      "DIF": ind.dif, "DEA": ind.dea, "MACD柱": ind.hist,
      "K": ind.k, "D": ind.d, "J": ind.j,
      "5日涨幅": ind.gain_5d, "10日涨幅": ind.gain_10d,
    };
    const indList = Object.entries(cells)
      .filter(([, v]) => v !== null && v !== undefined)
      .map(([k, v]) => ({ k, v: fmtNum(v, 2) }));
    this.setData({ indList });
  },

  drawKline(bars) {
    if (!bars || !bars.length) return;
    const ctx = wx.createCanvasContext("kline", this);
    this.paint(ctx, bars, () => {
      ctx.draw();
    });
  },

  paint(ctx, bars, done) {
    // 经典 canvas API：先用相对单位画，画布尺寸交给 wxss 控制
    const w = 375;
    const h = 260;
    const padL = 10, padR = 10, padT = 12;
    const priceH = h * 0.72, gap = 10;
    const priceBottom = padT + priceH;
    const volTop = priceBottom + gap;
    const volBottom = h - 6;

    const lows = bars.map((b) => b.low);
    const highs = bars.map((b) => b.high);
    const maAll = ["ma5", "ma10", "ma20"]
      .map((k) => bars.map((b) => b[k]).filter((v) => v !== null));
    const all = lows.concat(highs).concat(...maAll).filter((v) => v !== null && v > 0);
    let minP = Math.min(...all), maxP = Math.max(...all);
    const pad = (maxP - minP) * 0.06 || 1;
    minP -= pad; maxP += pad;

    const n = bars.length;
    const step = (w - padL - padR) / n;
    const bw = Math.max(1.2, step * 0.6);
    const px = (i) => padL + step * i + step / 2;
    const py = (v) => padT + (maxP - v) / (maxP - minP) * priceH;
    const maxVol = Math.max(...bars.map((b) => b.volume), 1);

    ctx.clearRect(0, 0, w, h);
    ctx.setStrokeStyle("#eef1f5");
    ctx.setLineWidth(1);
    for (let g = 0; g <= 4; g++) {
      const y = padT + (priceH / 4) * g;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
    }

    bars.forEach((b, i) => {
      const up = b.close >= b.open;
      const color = up ? "#e03131" : "#2f9e44";
      ctx.setStrokeStyle(color);
      ctx.setFillStyle(color);
      const x = px(i);
      ctx.beginPath();
      ctx.moveTo(x, py(b.high));
      ctx.lineTo(x, py(b.low));
      ctx.stroke();
      const yo = py(b.open), yc = py(b.close);
      const top = Math.min(yo, yc);
      const bh = Math.max(Math.abs(yo - yc), 1);
      ctx.fillRect(x - bw / 2, top, bw, bh);

      const vh = (b.volume / maxVol) * (volBottom - volTop);
      ctx.setFillStyle(up ? "rgba(224,49,49,.45)" : "rgba(47,158,68,.45)");
      ctx.fillRect(x - bw / 2, volBottom - vh, bw, Math.max(vh, 1));
    });

    const maColors = { ma5: "#f59f00", ma10: "#1971c2", ma20: "#9c36b5" };
    Object.entries(maColors).forEach(([key, color]) => {
      ctx.setStrokeStyle(color);
      ctx.setLineWidth(1.2);
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

    const last = bars[n - 1];
    ctx.setFillStyle(last.close >= last.open ? "#e03131" : "#2f9e44");
    ctx.setFontSize(11);
    ctx.fillText(last.close.toFixed(2), w - padR - 50, Math.max(2, py(last.close) - 12));

    // 缠论中枢上下沿（虚线）
    const chan = this.data.chanlun || {};
    if (chan.levels && chan.levels.length) {
      ctx.setFontSize(10);
      chan.levels.forEach((lv) => {
        const y = py(lv.price);
        ctx.setStrokeStyle(lv.type === "zg" ? "#9c36b5" : "#f59f00");
        ctx.setLineWidth(1.2);
        // 经典 canvas 不支持 setLineDash，用短线段模拟虚线
        const dash = 6;
        for (let x = padL; x < w - padR; x += dash * 2) {
          ctx.beginPath();
          ctx.moveTo(x, y);
          ctx.lineTo(Math.min(x + dash, w - padR), y);
          ctx.stroke();
        }
        ctx.setFillStyle(lv.type === "zg" ? "#9c36b5" : "#f59f00");
        ctx.fillText(`${lv.type.toUpperCase()} ${lv.price.toFixed(2)}`, padL + 2, y - 2);
      });
    }
    if (done) done();
  },

  shareCard() {
    // 生成分享卡片（简化版：直接把K线截图保存/转发）
    wx.canvasToTempFilePath({
      canvasId: "kline",
      success: (res) => {
        wx.showActionSheet({
          itemList: ["保存到相册", "转发给朋友"],
          success: (r) => {
            if (r.tapIndex === 0) {
              wx.saveImageToPhotosAlbum({
                filePath: res.tempFilePath,
                success: () => wx.showToast({ title: "已保存", icon: "success" }),
                fail: () => wx.showToast({ title: "保存失败，请检查相册权限", icon: "none" }),
              });
            } else if (r.tapIndex === 1) {
              wx.showShareImageMenu({ path: res.tempFilePath });
            }
          },
        });
      },
      fail: () => wx.showToast({ title: "生成图片失败", icon: "none" }),
    });
  },
});
