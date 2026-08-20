function fmtPct(v) {
  if (v === null || v === undefined) return "—";
  return (v > 0 ? "+" : "") + Number(v).toFixed(2) + "%";
}

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined) return "—";
  return Number(v).toFixed(digits);
}

function pctClass(v) {
  if (v === null || v === undefined) return "";
  return v > 0 ? "up" : v < 0 ? "down" : "";
}

module.exports = { fmtPct, fmtNum, pctClass };
