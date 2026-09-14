const state = { data: null, rangeHours: 24, chartEndMs: null, chartDragging: false, chartPointerDown: false, dragMoved: false, dragPointerType: "", dragStartX: 0, dragStartEndMs: 0, chartPinnedTimes: [] };
const NOTIFY_PREF_KEY = "codex-quota-reset-notifications";
const NOTIFY_SNAPSHOT_KEY = "codex-quota-reset-snapshot";
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

function updateNotificationButton(status = "") {
  const button = $("#notify-toggle");
  if (!button) return;
  if (!("Notification" in window)) { button.textContent = "不支持"; button.disabled = true; return; }
  const enabled = Notification.permission === "granted" && localStorage.getItem(NOTIFY_PREF_KEY) === "1";
  button.textContent = enabled ? "提醒已开" : Notification.permission === "denied" ? "提醒被拒" : "开启提醒";
  button.classList.toggle("active", enabled);
  if (status) button.title = status;
}

async function enableNotifications() {
  const button = $("#notify-toggle");
  if (!("Notification" in window)) { updateNotificationButton("当前浏览器不支持系统通知"); return; }
  if (!window.isSecureContext) { updateNotificationButton("手机通知需要 HTTPS，局域网 HTTP 地址不支持"); return; }
  try {
    const permission = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
    if (permission === "granted") {
      localStorage.setItem(NOTIFY_PREF_KEY, "1");
      button.title = "已开启额度重置通知";
    } else if (permission === "denied") {
      button.title = "通知已被拒绝，请在浏览器或系统设置中开启";
    } else button.title = "尚未允许通知";
  } catch (_) { button.title = "无法开启通知，请检查浏览器权限"; }
  updateNotificationButton();
}

function notifyResetIfDetected(data) {
  const windows = data?.latest?.ok ? data.latest.windows || [] : [];
  if (!windows.length) return;
  let previous = {};
  try { previous = JSON.parse(localStorage.getItem(NOTIFY_SNAPSHOT_KEY) || "{}"); } catch (_) {}
  const snapshot = {}, resets = [];
  windows.forEach((window) => {
    const currentRemaining = Number(window.remaining_percent), prior = previous[window.id];
    const currentReset = window.reset_at ? new Date(window.reset_at).getTime() : 0;
    const priorReset = prior?.reset_at ? new Date(prior.reset_at).getTime() : 0;
    const resetMoved = currentReset > priorReset + 60 * 1000;
    const quotaJumped = Number.isFinite(currentRemaining) && Number(prior?.remaining) + 20 < currentRemaining;
    if (prior && (resetMoved || quotaJumped)) resets.push(`${window.name || "额度窗口"} ${Math.round(currentRemaining)}%`);
    snapshot[window.id] = {reset_at: window.reset_at || null, remaining: currentRemaining};
  });
  localStorage.setItem(NOTIFY_SNAPSHOT_KEY, JSON.stringify(snapshot));
  if (!resets.length || Notification.permission !== "granted" || localStorage.getItem(NOTIFY_PREF_KEY) !== "1") return;
  try { new Notification("额度已重置", {body: resets.join("、")}); } catch (_) {}
}

async function load(collect = false) {
  try {
    const response = await fetch(collect ? "api/collect" : "api/status", {cache:"no-store"});
    const payload = await response.json(); state.data = payload.dashboard || payload; notifyResetIfDetected(state.data); render();
  } catch (_) { notice("无法连接本地采集器，请确认 app.py 正在运行。", true); }
}

function notice(message, warning) {
  const el = $("#notice"); el.textContent = message; el.classList.remove("hidden"); el.style.borderColor = warning ? "rgba(244,184,96,.3)" : "rgba(86,224,230,.3)";
}

function render() {
  const d = state.data || {}, latest = d.latest, windows = d.windows || [];
  if (!latest) notice("还没有额度采样。点击右上角 ↻ 立即读取 Codex OAuth 额度。", false);
  else if (d.collector?.last_error) notice(`最近一次采样失败：${d.collector.last_error}，当前显示最近一次成功采样。`, true);
  else if (d.collector?.stale) notice("当前额度数据已过期，正在等待新的采样。", true);
  else $("#notice").classList.add("hidden");
  $("#last-sync").textContent = latest?.captured_at ? `SYNC ${dateText(latest.captured_at)}${d.collector?.stale ? " · STALE" : ""}` : "WAITING FOR SIGNAL";
  $("#data-location").textContent = d.data_dir || "";
  renderCards(windows); renderChart(d.history || [], windows, d.poll_seconds); renderForecast(d.forecast, windows);
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

function renderChart(history, windows = [], pollSeconds = 300) {
  const svg = $("#chart"), chartEnd = state.chartEndMs || Date.now(), rangeMs = state.rangeHours * 3600 * 1000, cutoff = chartEnd - rangeMs;
  const points = history.filter((item) => { const time = new Date(item.captured_at).getTime(); return time >= cutoff && time <= chartEnd; }), width = 1000, height = 390, left = 48, right = 18, top = 22, bottom = 32, plotW = width - left - right, plotH = height - top - bottom;
  const sourceWindows = windows.length ? windows : (points.at(-1)?.windows || []);
  const seriesMeta = sourceWindows.map((window, index) => ({id:window.id, label:window.name || "额度窗口", color:index % 2 ? "violet" : "cyan", marker:`#chart-hover-${index}`}));
  const legend = $("#chart-legend");
  if (legend) legend.innerHTML = `${seriesMeta.map((item) => `<span><i class="legend-dot ${item.color}"></i>${item.label}</span>`).join("")}${seriesMeta.length ? `<span><i class="legend-gap"></i>缺少采样</span>` : ""}`;
  const x = (t) => left + ((t - cutoff) / rangeMs) * plotW, y = (v) => top + (100 - Math.max(0, Math.min(100, v))) / 100 * plotH;
  const gapThreshold = Math.max(Number(pollSeconds) * 1.5 * 1000, 10 * 60 * 1000);
  const segmentsFor = (values) => {
    const segments = [], continuous = [];
    values.forEach((point, index) => {
      if (!continuous.length) { continuous.push(point); return; }
      const previous = values[index - 1];
      if (point.time - previous.time > gapThreshold) {
        if (continuous.length > 1) segments.push({values:continuous.splice(0), dashed:false});
        segments.push({values:[previous, point], dashed:true});
        continuous.push(point);
      } else continuous.push(point);
    });
    if (continuous.length > 1) segments.push({values:continuous, dashed:false});
    return segments;
  };
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
  const plottedValues = new Map();
  seriesMeta.forEach((item) => {
    const {id} = item;
    const values = [];
    points.forEach((item) => { const w = (item.windows || []).find((candidate) => candidate.id === id); if (w) values.push({time:new Date(item.captured_at).getTime(), value:Number(w.remaining_percent)}); });
    if (!values.length) return;
    plottedValues.set(item.id, values);
    const color = item.color;
    if (values.length < 2) { sparse = true; markup += `<circle class="point-${color}" cx="${x(values[0].time)}" cy="${y(values[0].value)}" r="4"/>`; return; }
    const segments = segmentsFor(values);
    segments.forEach((segment) => {
      const line = path(segment.values);
      const dashed = segment.dashed ? " series-gap" : "";
      if (!segment.dashed) {
        const area = `${line} L${x(segment.values.at(-1).time)},${y(0)} L${x(segment.values[0].time)},${y(0)} Z`;
        markup += `<path class="area area-${color}" d="${area}"/>`;
      }
      markup += `<path class="series series-${color}${dashed}" d="${line}"/>`;
    });
  });
  if (!points.length) markup += `<text class="axis-label" x="400" y="190">等待第一次额度采样…</text>`;
  else if (sparse) markup += `<text class="chart-note" x="${left + 12}" y="${top + 20}">继续采样后显示变化曲线</text>`;
  state.chartPinnedTimes.filter((time) => time >= cutoff && time <= chartEnd).forEach((time, pinIndex) => {
    const pinX = x(time);
    markup += `<line class="chart-pinned-line" x1="${pinX}" x2="${pinX}" y1="${top}" y2="${height - bottom}"/>`;
    seriesMeta.forEach((item) => {
      const point = plottedValues.get(item.id)?.find((candidate) => candidate.time === time);
      if (point) markup += `<circle class="chart-pinned-point point-${item.color}" cx="${x(point.time)}" cy="${y(point.value)}" r="6"/>`;
    });
  });
  markup += `<rect class="chart-hit-area" x="${left}" y="${top}" width="${plotW}" height="${plotH}"/><line id="chart-hover-guide" class="chart-hover-guide" x1="${left}" x2="${left}" y1="${top}" y2="${height - bottom}" visibility="hidden"/>${seriesMeta.map((item) => `<circle id="${item.marker.slice(1)}" class="chart-hover-point point-${item.color}" r="5" visibility="hidden"/>`).join("")}`;
  svg.innerHTML = markup;
  normalizeSvgText(svg);
  renderPinnedOverlays(seriesMeta, plottedValues, state.chartPinnedTimes.filter((time) => time >= cutoff && time <= chartEnd), x, y, width, height);
  renderPinnedPoints(seriesMeta, history, state.chartPinnedTimes.filter((time) => time >= cutoff && time <= chartEnd));
  bindChartInteraction({svg, history, cutoff, chartEnd, rangeMs, width, left, plotW, top, height, bottom, x, y, seriesMeta});
}

function renderPinnedOverlays(seriesMeta, plottedValues, pinnedTimes, x, y, width, height) {
  const overlay = $("#chart-pinned-overlays");
  if (!overlay) return;
  overlay.innerHTML = pinnedTimes.map((time, index) => {
    const values = seriesMeta.map((item) => {
      const point = plottedValues.get(item.id)?.find((candidate) => candidate.time === time);
      return `<span class="chart-pinned-value value-${item.color}">${item.label} ${point ? `${Math.round(point.value)}%` : "—"}</span>`;
    }).join("");
    const leftPercent = Math.max(12, Math.min(88, x(time) / width * 100));
    const timestamp = new Date(time).toLocaleString("zh-CN", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit"});
    return `<div class="chart-pinned-overlay" style="left:${leftPercent}%;top:${10 + (index % 3) * 54}px"><strong>${timestamp}</strong><div>${values}</div></div>`;
  }).join("");
}

function normalizeSvgText(svg) {
  const rect = svg.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const inverseX = (rect.height / 390) / (rect.width / 1000);
  svg.querySelectorAll("text").forEach((node) => {
    const x = Number(node.getAttribute("x")), y = Number(node.getAttribute("y"));
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    node.setAttribute("transform", `translate(${x} ${y}) scale(${inverseX} 1) translate(${-x} ${-y})`);
  });
}

function renderPinnedPoints(seriesMeta, history, pinnedTimes) {
  const panel = $("#chart-pins");
  if (!panel) return;
  if (!pinnedTimes.length) { panel.innerHTML = `<span class="chart-pins-hint">点击曲线上的时间点，可固定数值并多选对比</span>`; return; }
  const rows = pinnedTimes.map((time, index) => {
    const values = seriesMeta.map((meta) => {
      const point = history.flatMap((item) => (item.windows || []).filter((window) => window.id === meta.id && new Date(item.captured_at).getTime() === time).map((window) => window.remaining_percent));
      return `<span><i class="legend-dot ${meta.color}"></i>${meta.label} <b>${point.length ? `${Math.round(Number(point[0]))}%` : "—"}</b></span>`;
    }).join("");
    return `<div class="chart-pin-row"><strong>${new Date(time).toLocaleString("zh-CN", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit"})}</strong>${values}<button type="button" data-remove-pin="${index}" aria-label="取消固定该时间点">×</button></div>`;
  }).join("");
  panel.innerHTML = `<div class="chart-pins-head"><span>已固定 ${pinnedTimes.length} 个时间点</span><button type="button" id="clear-chart-pins">清除全部</button></div>${rows}`;
  panel.querySelectorAll("[data-remove-pin]").forEach((button) => button.addEventListener("click", () => { state.chartPinnedTimes.splice(Number(button.dataset.removePin), 1); renderChart(state.data?.history || [], state.data?.windows || [], state.data?.poll_seconds); }));
  panel.querySelector("#clear-chart-pins")?.addEventListener("click", () => { state.chartPinnedTimes = []; renderChart(state.data?.history || [], state.data?.windows || [], state.data?.poll_seconds); });
}

function bindChartInteraction(chart) {
  const {svg, history, cutoff, chartEnd, rangeMs, width, left, plotW, top, height, bottom, x, y, seriesMeta} = chart;
  const tooltip = $("#chart-tooltip");
  const valuesFor = (id) => history.flatMap((item) => (item.windows || []).filter((w) => w.id === id).map((w) => ({time:new Date(item.captured_at).getTime(), value:Number(w.remaining_percent)}))).filter((p) => p.time >= cutoff && p.time <= chartEnd);
  const series = seriesMeta.map((item) => ({...item, dot:item.color, values:valuesFor(item.id)})).filter((item) => item.values.length);
  const localX = (event) => { const rect = svg.getBoundingClientRect(); return Math.max(left, Math.min(width - 18, (event.clientX - rect.left) / rect.width * width)); };
  const valueAt = (values, time) => {
    if (values.length === 1 || time <= values[0].time) return {...values[0], time};
    if (time >= values.at(-1).time) return {...values.at(-1), time};
    const rightIndex = values.findIndex((item) => item.time >= time), right = values[rightIndex], leftPoint = values[rightIndex - 1], ratio = (time - leftPoint.time) / (right.time - leftPoint.time);
    return {time, value:leftPoint.value + (right.value - leftPoint.value) * ratio};
  };
  const nearestSampleTime = (time) => series.flatMap((item) => item.values).reduce((nearest, point) => Math.abs(point.time - time) < Math.abs(nearest - time) ? point.time : nearest, series[0].values[0].time);
  const togglePinnedTime = (event) => {
    if (!series.length) return;
    const cursorX = localX(event), time = cutoff + ((cursorX - left) / plotW) * rangeMs, snapped = nearestSampleTime(time), existing = state.chartPinnedTimes.findIndex((item) => item === snapped);
    if (existing >= 0) state.chartPinnedTimes.splice(existing, 1); else state.chartPinnedTimes.push(snapped);
    state.chartPinnedTimes.sort((a, b) => a - b);
  };
  const hideTooltip = () => { tooltip.classList.remove("visible"); ["#chart-hover-guide", ...seriesMeta.map((item) => item.marker)].forEach((selector) => { const node = svg.querySelector(selector); if (node) node.setAttribute("visibility", "hidden"); }); };
  const showTooltip = (event, allowDuringPointerDown = false) => {
    if ((state.chartDragging || (state.chartPointerDown && !allowDuringPointerDown)) || !series.length) return;
    const cursorX = localX(event), time = cutoff + ((cursorX - left) / plotW) * rangeMs;
    const selected = series.map((item) => ({...item, point:valueAt(item.values, time)}));
    const guide = svg.querySelector("#chart-hover-guide"); guide.setAttribute("x1", cursorX); guide.setAttribute("x2", cursorX); guide.setAttribute("visibility", "visible");
    selected.forEach((item) => { const marker = svg.querySelector(item.marker); marker.setAttribute("cx", x(item.point.time)); marker.setAttribute("cy", y(item.point.value)); marker.setAttribute("visibility", "visible"); });
    tooltip.innerHTML = `<strong>${new Date(time).toLocaleString("zh-CN", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit"})}</strong>${selected.map((item) => `<span><i class="legend-dot ${item.dot}"></i>${item.label} <b>${Math.round(item.point.value)}%</b></span>`).join("")}`;
    const wrap = $(".chart-wrap"), rect = wrap.getBoundingClientRect(); tooltip.style.left = `${Math.min(Math.max(event.clientX - rect.left + 14, 8), rect.width - tooltip.offsetWidth - 8)}px`; tooltip.style.top = `${Math.max(event.clientY - rect.top - tooltip.offsetHeight - 12, 8)}px`; tooltip.classList.add("visible");
  };
  svg.onpointermove = (event) => {
    const currentX = localX(event);
    if (state.chartPointerDown && !state.chartDragging && Math.abs(currentX - state.dragStartX) > 8) { state.chartDragging = true; state.dragMoved = true; hideTooltip(); }
    if (state.chartDragging) { const deltaMs = ((state.dragStartX - currentX) / plotW) * rangeMs; state.chartEndMs = state.dragStartEndMs + deltaMs; svg.classList.add("dragging"); return; }
    showTooltip(event, state.chartPointerDown && state.dragPointerType === "touch");
  };
  svg.onpointerdown = (event) => { state.chartPointerDown = true; state.dragPointerType = event.pointerType; state.dragMoved = false; state.chartDragging = false; state.dragStartX = localX(event); state.dragStartEndMs = chartEnd; svg.setPointerCapture(event.pointerId); hideTooltip(); };
  svg.onpointerup = (event) => { const wasTap = !state.dragMoved && !state.chartDragging; state.chartPointerDown = false; state.chartDragging = false; svg.releasePointerCapture(event.pointerId); svg.classList.remove("dragging"); if (wasTap) togglePinnedTime(event); renderChart(state.data?.history || [], state.data?.windows || [], state.data?.poll_seconds); };
  svg.onpointercancel = (event) => { state.chartPointerDown = false; state.chartDragging = false; svg.classList.remove("dragging"); if (event.pointerId != null && svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId); renderChart(state.data?.history || [], state.data?.windows || [], state.data?.poll_seconds); };
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
  const response = await fetch("api/forecast", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({forecast_reset_at:new Date(resetAt).toISOString(), probability_24h:probability / 100})});
  const payload = await response.json();
  if (!response.ok) { status.textContent = payload.error || "保存失败"; return; }
  status.textContent = "已保存"; await load();
}

async function clearForecast() {
  const status = $("#forecast-form-status");
  const response = await fetch("api/forecast", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({clear:true})});
  const payload = await response.json();
  if (!response.ok) { status.textContent = payload.error || "清除失败"; return; }
  $("#forecast-reset-at").value = ""; $("#forecast-probability").value = ""; status.textContent = "已清除"; await load();
}

$("#refresh").addEventListener("click", () => load(true));
$("#notify-toggle").addEventListener("click", enableNotifications);
$("#forecast-form").addEventListener("submit", (event) => { event.preventDefault(); saveForecast(); });
$("#clear-forecast").addEventListener("click", clearForecast);
document.querySelectorAll("[data-range]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-range]").forEach((item) => item.classList.remove("active"));
    button.classList.add("active"); state.rangeHours = Number(button.dataset.range); state.chartEndMs = null; renderChart(state.data?.history || [], state.data?.windows || [], state.data?.poll_seconds);
  });
});
window.addEventListener("resize", () => renderChart(state.data?.history || [], state.data?.windows || [], state.data?.poll_seconds));
updateNotificationButton(); load(); setInterval(() => load(), 60000);
