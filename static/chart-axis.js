/* Calendar-aligned local time ticks, shared by the SVG grid and HTML labels. */
(() => {
  "use strict";
  const MINUTE = 60 * 1000, HOUR = 60 * MINUTE, DAY = 24 * HOUR;
  const MIN_LABEL_GAP = 76;
  const pad = (value) => String(value).padStart(2, "0");
  const dayLabel = (date) => `${date.getMonth() + 1}月${date.getDate()}日`;
  const clockLabel = (date) => `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  const fullLabel = (date) => `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${clockLabel(date)}`;
  const dateKey = (date) => `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
  const weekday = (date) => `周${"日一二三四五六"[date.getDay()]}`;
  const offsetLabel = (date) => {
    const minutes = -date.getTimezoneOffset();
    return `UTC${minutes >= 0 ? "+" : "-"}${pad(Math.floor(Math.abs(minutes) / 60))}:${pad(Math.abs(minutes) % 60)}`;
  };

  function tickTimes(startMs, endMs, unit, step) {
    const times = [];
    const cursor = new Date(startMs);
    if (unit === "hour") {
      cursor.setMinutes(0, 0, 0);
      // Scan real instants: retain repeated hours and skip nonexistent local hours.
      // Quarter-hour steps also handle fractional-offset zones and 30-minute DST.
      for (let time = cursor.getTime(); time <= endMs; time += 15 * MINUTE) {
        const date = new Date(time);
        if (time >= startMs && date.getMinutes() === 0 && date.getHours() % step === 0) times.push(time);
      }
    } else {
      cursor.setHours(0, 0, 0, 0);
      while (cursor.getTime() <= endMs) {
        // Anchor to calendar dates, not the viewport: panning does not move ticks.
        const serialDay = Math.floor(Date.UTC(cursor.getFullYear(), cursor.getMonth(), cursor.getDate()) / DAY);
        if (cursor.getTime() >= startMs && serialDay % step === 0) times.push(cursor.getTime());
        cursor.setDate(cursor.getDate() + 1);
        cursor.setHours(0, 0, 0, 0);
      }
    }
    return times;
  }

  function build({startMs, endMs, plotWidthPx = 900}) {
    const rangeMs = endMs - startMs;
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || rangeMs <= 0 || rangeMs > 366 * DAY
        || !Number.isFinite(new Date(startMs).getTime()) || !Number.isFinite(new Date(endMs).getTime())) {
      throw new RangeError("Time axis needs a valid increasing range of at most 366 days");
    }
    const width = Number.isFinite(plotWidthPx) && plotWidthPx > 0 ? plotWidthPx : 320;
    const unit = rangeMs <= DAY ? "hour" : "day";
    const steps = unit === "hour" ? (rangeMs >= 18 * HOUR ? [3, 6, 12, 24] : [1, 2, 3, 6, 12, 24])
      : rangeMs <= 14 * DAY ? [1, 2, 3, 5, 7, 14] : [5, 7, 10, 14, 30, 60, 90, 180, 365];
    const budget = Math.max(2, Math.min(12, Math.floor(width / MIN_LABEL_GAP)));
    let step = steps.at(-1), times = [];
    for (const candidate of steps) {
      if (rangeMs / ((unit === "hour" ? HOUR : DAY) * candidate) > budget) continue;
      const candidates = tickTimes(startMs, endMs, unit, candidate);
      const spaced = candidates.every((time, index) => index === 0 || (time - candidates[index - 1]) / rangeMs * width >= MIN_LABEL_GAP);
      step = candidate; times = candidates;
      if (spaced || candidates.length < 2) break;
    }
    const duplicates = new Map();
    times.forEach((time) => {
      const date = new Date(time), key = `${dateKey(date)} ${clockLabel(date)}`;
      duplicates.set(key, (duplicates.get(key) || 0) + 1);
    });
    const ticks = times.map((time, index) => {
      const date = new Date(time), previous = index ? new Date(times[index - 1]) : null;
      const midnight = date.getHours() === 0 && date.getMinutes() === 0;
      const newDate = !previous || dateKey(date) !== dateKey(previous);
      const repeated = duplicates.get(`${dateKey(date)} ${clockLabel(date)}`) > 1;
      return {
        time, ratio: (time - startMs) / rangeMs,
        primary: unit === "hour" ? clockLabel(date) : dayLabel(date),
        secondary: unit === "hour" ? (repeated ? offsetLabel(date) : newDate ? dayLabel(date) : "") : weekday(date),
        major: unit === "hour" ? midnight : !previous || date.getMonth() !== previous.getMonth() || date.getFullYear() !== previous.getFullYear(),
        title: `${fullLabel(date)} ${weekday(date)} · ${offsetLabel(date)}`,
      };
    });
    const start = new Date(startMs), end = new Date(endMs);
    const startOffset = offsetLabel(start), endOffset = offsetLabel(end);
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return {
      ticks, unit, step,
      intervalText: unit === "hour" ? `${step} 小时刻度` : step === 1 ? "每日刻度" : `${step} 天刻度`,
      rangeText: `${fullLabel(start)} — ${fullLabel(end)}`,
      zoneText: `本地时间 · ${zone || ""} (${startOffset}${startOffset === endOffset ? "" : ` → ${endOffset}`})`,
    };
  }

  function render(model, {axis, summary, scale, zone, leftRatio, rightRatio}) {
    if (!axis) return;
    const document = axis.ownerDocument;
    const fragment = document.createDocumentFragment();
    model.ticks.forEach((tick) => {
      const mark = document.createElement("div");
      mark.className = `time-axis-tick${tick.major ? " time-axis-major" : ""}`;
      mark.style.left = `${(leftRatio + tick.ratio * (1 - leftRatio - rightRatio)) * 100}%`;
      mark.dataset.time = String(tick.time);
      const label = document.createElement("time");
      label.className = "time-axis-label";
      label.dateTime = new Date(tick.time).toISOString();
      label.title = tick.title;
      for (const [className, text] of [["time-axis-primary", tick.primary], ["time-axis-secondary", tick.secondary]]) {
        const line = document.createElement("span");
        line.className = className;
        line.textContent = text;
        label.appendChild(line);
      }
      mark.appendChild(label);
      fragment.appendChild(mark);
    });
    axis.replaceChildren(fragment);
    // Keep edge labels inside the panel; the tick/grid positions remain exact.
    const bounds = axis.getBoundingClientRect();
    axis.querySelectorAll(".time-axis-label").forEach((label) => {
      const rect = label.getBoundingClientRect();
      const shift = Math.max(0, bounds.left - rect.left) - Math.max(0, rect.right - bounds.right);
      if (shift) label.style.marginLeft = `${shift}px`;
    });
    if (summary) summary.textContent = `显示范围：${model.rangeText}`;
    if (scale) scale.textContent = model.intervalText;
    if (zone) zone.textContent = model.zoneText;
  }

  const api = {build, render};
  if (typeof module === "object" && module.exports) module.exports = api;
  else globalThis.QuotaTimeAxis = api;
})();
