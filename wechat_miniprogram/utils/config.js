function baseUrl() {
  try {
    const app = getApp();
    if (app && app.globalData && app.globalData.baseUrl) {
      return app.globalData.baseUrl;
    }
  } catch (e) {}
  return "http://127.0.0.1:8235";
}

function request(path, method = "GET", data = {}) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: baseUrl() + path,
      method,
      data,
      timeout: 15000,
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else {
          reject(new Error((res.data && res.data.error) || `请求失败(${res.statusCode})`));
        }
      },
      fail: (err) => reject(new Error(err.errMsg || "网络请求失败")),
    });
  });
}

module.exports = {
  baseUrl,
  request,
  startPick: (params) => request("/api/run", "POST", params),
  status: () => request("/api/status"),
  kline: (code, date, klt = 101) => request(`/api/kline?code=${code}&date=${date}&klt=${klt}`),
  verify: (date) => request(`/api/verify?date=${date}`),
  verifyIndex: () => request("/api/verify"),
  runVerify: (params) => request("/api/verify", "POST", params),
  userdata: () => request("/api/userdata"),
  quotes: (codes) => request(`/api/quotes?codes=${codes}`),
  addWatch: (code, name, alert_price, alert_type) =>
    request("/api/watchlist", "POST", { code, name, alert_price, alert_type }),
  removeWatch: (code) => request(`/api/watchlist?action=remove&code=${code}`),
  trade: (code, name, action, price, shares) =>
    request("/api/trade", "POST", { code, name, action, price, shares }),
  closePosition: (code) => request("/api/close", "POST", { code }),
  sectors: () => request("/api/sectors"),
  subscribe: (tmplId) => request("/api/subscribe", "POST", { tmplId }),
};
