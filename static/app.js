const state = { data: null, rangeHours: 24, chartEndMs: null, chartDragging: false, chartPointerDown: false, dragMoved: false, dragPointerType: "", dragStartX: 0, dragStartEndMs: 0 };
const $ = (s) => document.querySelector(s);
const pct = (v, digits = 1) => v == null || Number.isNaN(Number(v)) ? "—" : `${Number(v).toFixed(digits)}%`;
const dateText = (v) => v ? new Date(v).toLocaleString("zh-CN", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit"}) : "—";
const hoursText = (v) => {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const totalMinutes = Math.max(0, Math.round(Number(v) * 60));
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) return `${days} 天${hours ? ` ${hours} 小时` : ""}`;
  if (hours > 0) return `${hours} 小时${minutes ? ` ${minutes} 分钟` : ""}`;
  return `${minutes} 分钟`;
};

async function load(collect = false) {
  try {
    const response = await fetch(collect ? "/api/collect" : "/api/status", {cache:"no-store"});
    const payload = await response.json(); state.data = payload.dashboard || payload; render();
  } catch (_) { notice("无法连接本地采集器，请确认 app.py 正在运行。", true); }
}

function notice(message, warning) {
  const el = $("#notice"); el.textContent = message; el.classList.remove("hidden"); el.style.borderColor = warning ? "rgba(244,184,96,.3)" : "rgba(86,224,230,.3)";
}

function render() {
  const d = state.data || {}, latest = d.latest, windows = d.windows || [];
  if (!latest) notice("还没有额度采样。点击右上角 ↻ 立即读取 Codex OAuth 额度。", false);
  else if (!latest.ok) notice(latest.error || "最近一次采样失败。", true);
  else $("#notice").classList.add("hidden");
  $("#last-sync").textContent = latest?.captured_at ? `SYNC ${dateText(latest.captured_at)}` : "WAITING FOR SIGNAL";
  $("#data-location").textContent = d.data_dir || "";
  renderCards(windows); renderChart(d.history || []); renderForecast(d.forecast, windows);
}

function renderCards(windows) {
  const el = $("#cards");
  if (!windows.length) { el.innerHTML = `<div class="card"><div class="empty">暂无可用额度窗口</div></div>`; return; }
  el.innerHTML = windows.map((w, i) => {
    const color = i === 0 ? "cyan" : "violet", remain = Math.max(0, Math.min(100, Number(w.remaining_percent || 0)));
    const stateText = w.pace_gap > 8 ? "PACE · ROOM TO USE" : w.pace_gap < -8 ? "PACE · SLOW DOWN" : "PACE · BALANCED";
    return `<article class="card ${color}-card"><div class="card-title">${w.name || "额度窗口"}<span style="float:right">${stateText}</span></div><div class="card-body"><div><div class="big-number">${Math.round(remain)}<small>%</small></div><div class="card-recommend">推荐剩余 ${pct(w.recommended_remaining)} · ${w.recommendation || "—"}</div></div><div class="ring" style="--ring:${remain * 3.6}deg;--ring-color:var(--${color})"><span>${Math.round(remain)}%</span></div></div><div class="card-meta"><span>重置 ${dateText(w.reset_at)}</span><span>倒计时 ${hoursText(w.hours_to_reset)}</span><span>速度 ${pct(w.velocity_per_hour, 2)}/h</span></div></article>`;
  }).join("");
}

function renderChart(history) {
  const svg = $("#chart"), chartEnd = state.chartEndMs || Date.now(), rangeMs = state.rangeHours * 3600 * 1000, cutoff = chartEnd - rangeMs;
  const points = history.filter((item) => { const time = new Date(item.captured_at).getTime(); return time >= cutoff && time <= chartEnd; }), width = 1000, height = 390, left = 48, right = 18, top = 22, bottom = 32, plotW = width - left - right, plotH = height - top - bottom;
  const x = (t) => left + ((t - cutoff) / rangeMs) * plotW, y = (v) => top + (100 - Math.max(0, Math.min(100, v))) / 100 * plotH;
  const path = (values) => values.map((p, i) => `${i ? "L" : "M"}${x(p.time).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  let markup = `<defs><linearGradient id="cyanFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#56e0e6"/><stop offset="1" stop-color="#56e0e6" stop-opacity="0"/></linearGradient><linearGradient id="violetFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9d8cff"/><stop offset="1" stop-color="#9d8cff" stop-opacity="0"/></linearGradient></defs>`;
  [0, 25, 50, 75, 100].forEach((v) => { markup += `<line class="grid-line" x1="${left}" x2="${width - right}" y1="${y(v)}" y2="${y(v)}"/><text class="axis-label" x="8" y="${y(v) + 4}">${v}%</text>`; });
  for (let i = 0; i <= 4; i += 1) {
    const tick = cutoff + (rangeMs * i / 4);
    const label = new Date(tick).toLocaleString("zh-CN", state.rangeHours <= 24 ? {hour:"2-digit", minute:"2-digit"} : {month:"numeric", day:"numeric"});
    const tickX = left + plotW * i / 4;
    markup += `<line class="grid-line" x1="${tickX}" x2="${tickX}" y1="${top}" y2="${height - bottom}"/><text class="axis-label x-axis-label" text-anchor="middle" x="${tickX}" y="${height - 8}">${label}</text>`;
  }
  markup += `<text class="axis-label" text-anchor="end" x="${width - right}" y="${height - 24}">时间</text>`;
  let sparse = false;
  ["primary_window", "secondary_window"].forEach((id, i) => {
    const values = [];
    points.forEach((item) => { const w = (item.windows || []).find((candidate) => candidate.id === id); if (w) values.push({time:new Date(item.captured_at).getTime(), value:Number(w.remaining_percent)}); });
    if (!values.length) return;
    const color = i === 0 ? "cyan" : "violet";
    if (values.length < 2) { sparse = true; markup += `<circle class="point-${color}" cx="${x(values[0].time)}" cy="${y(values[0].value)}" r="4"/>`; return; }
    const line = path(values), area = `${line} L${x(values.at(-1).time)},${y(0)} L${x(values[0].time)},${y(0)} Z`;
    markup += `<path class="area area-${color}" d="${area}"/><path class="series series-${color}" d="${line}"/>`;
  });
  if (!points.length) markup += `<text class="axis-label" x="400" y="190">等待第一次额度采样…</text>`;
  else if (sparse) markup += `<text class="chart-note" x="${left + 12}" y="${top + 20}">继续采样后显示变化曲线</text>`;
  markup += `<line id="chart-hover-guide" class="chart-hover-guide" x1="${left}" x2="${left}" y1="${top}" y2="${height - bottom}" visibility="hidden"/><circle id="chart-hover-primary" class="chart-hover-point point-cyan" r="5" visibility="hidden"/><circle id="chart-hover-secondary" class="chart-hover-point point-violet" r="5" visibility="hidden"/>`;
  svg.innerHTML = markup;
  bindChartInteraction({svg, history, cutoff, chartEnd, rangeMs, width, left, plotW, top, height, bottom, x, y});
}

function bindChartInteraction(chart) {
  const {svg, history, cutoff, chartEnd, rangeMs, width, left, plotW, top, height, bottom, x, y} = chart;
  const tooltip = $("#chart-tooltip");
  const valuesFor = (id) => history.flatMap((item) => (item.windows || []).filter((w) => w.id === id).map((w) => ({time:new Date(item.captured_at).getTime(), value:Number(w.remaining_percent)}))).filter((p) => p.time >= cutoff && p.time <= chartEnd);
  const series = [{id:"primary_window", label:"5 小时", marker:"#chart-hover-primary", dot:"cyan", values:valuesFor("primary_window")}, {id:"secondary_window", label:"周期额度", marker:"#chart-hover-secondary", dot:"violet", values:valuesFor("secondary_window")}].filter((item) => item.values.length);
  const localX = (event) => { const rect = svg.getBoundingClientRect(); return Math.max(left, Math.min(width - 18, (event.clientX - rect.left) / rect.width * width)); };
  const nearest = (values, time) => values.reduce((best, item) => Math.abs(item.time - time) < Math.abs(best.time - time) ? item : best, values[0]);
  const hideTooltip = () => { tooltip.classList.remove("visible"); ["#chart-hover-guide", "#chart-hover-primary", "#chart-hover-secondary"].forEach((selector) => { const node = svg.querySelector(selector); if (node) node.setAttribute("visibility", "hidden"); }); };
  const showTooltip = (event, allowDuringPointerDown = false) => {
    if ((state.chartDragging || (state.chartPointerDown && !allowDuringPointerDown)) || !series.length) return;
    const cursorX = localX(event), time = cutoff + ((cursorX - left) / plotW) * rangeMs;
    const selected = series.map((item) => ({...item, point:nearest(item.values, time)}));
    const guide = svg.querySelector("#chart-hover-guide"); guide.setAttribute("x1", cursorX); guide.setAttribute("x2", cursorX); guide.setAttribute("visibility", "visible");
    selected.forEach((item) => { const marker = svg.querySelector(item.marker); marker.setAttribute("cx", x(item.point.time)); marker.setAttribute("cy", y(item.point.value)); marker.setAttribute("visibility", "visible"); });
    tooltip.innerHTML = `<strong>${new Date(time).toLocaleString("zh-CN", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit"})}</strong>${selected.map((item) => `<span><i class="legend-dot ${item.dot}"></i>${item.label} <b>${Math.round(item.point.value)}%</b></span>`).join("")}`;
    const wrap = $(".chart-wrap"), rect = wrap.getBoundingClientRect(); tooltip.style.left = `${Math.min(Math.max(event.clientX - rect.left + 14, 8), rect.width - tooltip.offsetWidth - 8)}px`; tooltip.style.top = `${Math.max(event.clientY - rect.top - tooltip.offsetHeight - 12, 8)}px`; tooltip.classList.add("visible");
  };
  svg.onpointermove = (event) => {
    const currentX = localX(event);
    if (state.chartPointerDown && state.dragPointerType === "touch" && !state.chartDragging && Math.abs(currentX - state.dragStartX) > 10) { state.chartDragging = true; state.dragMoved = true; hideTooltip(); }
    if (state.chartDragging) { const deltaMs = ((state.dragStartX - currentX) / plotW) * rangeMs; state.chartEndMs = state.dragStartEndMs + deltaMs; svg.classList.add("dragging"); return; }
    showTooltip(event, state.chartPointerDown && state.dragPointerType === "touch");
  };
  svg.onpointerdown = (event) => { state.chartPointerDown = true; state.dragPointerType = event.pointerType; state.dragMoved = false; state.chartDragging = event.pointerType !== "touch"; state.dragStartX = localX(event); state.dragStartEndMs = chartEnd; svg.setPointerCapture(event.pointerId); if (event.pointerType === "touch") showTooltip(event, true); else hideTooltip(); };
  svg.onpointerup = (event) => { const wasTouchTap = state.dragPointerType === "touch" && !state.dragMoved && !state.chartDragging; state.chartPointerDown = false; state.chartDragging = false; svg.releasePointerCapture(event.pointerId); svg.classList.remove("dragging"); if (!wasTouchTap) renderChart(state.data?.history || []); };
  svg.onpointercancel = (event) => { state.chartPointerDown = false; state.chartDragging = false; svg.classList.remove("dragging"); if (event.pointerId != null && svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId); renderChart(state.data?.history || []); };
  svg.onpointerleave = () => { if (!state.chartDragging && state.dragPointerType !== "touch") hideTooltip(); };
}

function renderForecast(signal, windows) {
  const badge = $("#forecast-badge"), content = $("#forecast-content");
  const resetInput = $("#forecast-reset-at"), probabilityInput = $("#forecast-probability");
  if (signal) {
    if (resetInput && signal.forecast_reset_at && document.activeElement !== resetInput) resetInput.value = inputDate(signal.forecast_reset_at);
    if (probabilityInput && signal.probability_24h != null && document.activeElement !== probabilityInput) probabilityInput.value = Math.round(Number(signal.probability_24h) * 100);
  }
  if (!signal) { badge.textContent = "NO SIGNAL"; badge.className = "badge muted"; content.innerHTML = `<div class="metric-line"><span>重置状态</span><strong>按自然周期计算</strong></div><div class="empty">等待重置预测信号；没有信号时按自然重置时间使用。</div>`; return; }
  const probability = Number(signal.probability_24h || 0) * 100;
  badge.textContent = `${Math.round(probability)}% / 24H`; badge.className = `badge ${probability >= 60 ? "amber-badge" : "cyan-badge"}`;
  content.innerHTML = `<div class="metric-line"><span>重置类型</span><strong>${signal.reset_type === "global_hard_reset" ? "全局 Hard Reset" : signal.reset_type === "banked_reset" ? "Banked Reset" : "未明确"}</strong></div><div class="metric-line"><span>未来 24 小时概率</span><strong>${Math.round(probability)}%</strong></div><div class="metric-line"><span>预计重置时间</span><strong>${signal.forecast_reset_at ? dateText(signal.forecast_reset_at) : "—"}</strong></div><div class="callout ${probability >= 60 ? "warn" : ""}">${probability >= 60 ? "预测信号较强，建议适度提前消耗，但保留安全底线。" : "当前没有强重置信号，按自然周期使用更稳妥。"}</div>`;
}

function inputDate(value) {
  const date = new Date(value), pad = (n) => String(n).padStart(2, "0");
  return Number.isNaN(date.getTime()) ? "" : `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

async function saveForecast() {
  const status = $("#forecast-form-status"), resetAt = $("#forecast-reset-at").value, probability = Number($("#forecast-probability").value);
  if (!resetAt || Number.isNaN(probability) || probability < 0 || probability > 100) { status.textContent = "请输入有效时间和 0–100 的概率"; return; }
  status.textContent = "保存中…";
  const response = await fetch("/api/forecast", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({forecast_reset_at:new Date(resetAt).toISOString(), probability_24h:probability / 100})});
  const payload = await response.json();
  if (!response.ok) { status.textContent = payload.error || "保存失败"; return; }
  status.textContent = "已保存"; await load();
}

async function clearForecast() {
  const status = $("#forecast-form-status");
  const response = await fetch("/api/forecast", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({clear:true})});
  const payload = await response.json();
  if (!response.ok) { status.textContent = payload.error || "清除失败"; return; }
  $("#forecast-reset-at").value = ""; $("#forecast-probability").value = ""; status.textContent = "已清除"; await load();
}

$("#refresh").addEventListener("click", () => load(true));
$("#forecast-form").addEventListener("submit", (event) => { event.preventDefault(); saveForecast(); });
$("#clear-forecast").addEventListener("click", clearForecast);
document.querySelectorAll("[data-range]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-range]").forEach((item) => item.classList.remove("active"));
    button.classList.add("active"); state.rangeHours = Number(button.dataset.range); state.chartEndMs = null; renderChart(state.data?.history || []);
  });
});
load(); setInterval(() => load(), 60000);
