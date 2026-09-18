"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {build, render} = require("../static/chart-axis.js");
const HOUR = 3600000, DAY = 24 * HOUR;

function inZone(zone, run) {
  const original = process.env.TZ;
  process.env.TZ = zone;
  try { return run(); }
  finally {
    if (original === undefined) delete process.env.TZ;
    else process.env.TZ = original;
  }
}
function axis(days = 1, width = 1100, end = "2026-09-18T10:37:00+08:00") {
  const endMs = Date.parse(end);
  return build({startMs:endMs - days * DAY, endMs, plotWidthPx:width});
}

for (const [name, days, expectedUnit, expectedStep] of [["24H", 1, "hour", 3], ["7D", 7, "day", 1], ["30D", 30, "day", 5]]) {
  test(`${name}: uses readable calendar intervals on desktop`, () => inZone("Asia/Shanghai", () => {
    const model = axis(days);
    assert.equal(model.unit, expectedUnit);
    assert.equal(model.step, expectedStep);
    assert.ok(model.ticks.length >= 4 && model.ticks.length <= 12);
    for (const tick of model.ticks) {
      const date = new Date(tick.time);
      assert.equal(date.getMinutes(), 0);
      assert.equal(date.getSeconds(), 0);
      if (model.unit === "day") assert.equal(date.getHours(), 0);
      else assert.equal(date.getHours() % expectedStep, 0);
    }
  }));
  test(`${name}: mobile reduces density, not font size`, () => inZone("Asia/Shanghai", () => {
    const desktop = axis(days), mobile = axis(days, 300);
    assert.ok(mobile.step > desktop.step);
    assert.ok(mobile.ticks.length < desktop.ticks.length);
    assert.ok(mobile.ticks.length >= 2);
    mobile.ticks.slice(1).forEach((tick, index) => assert.ok((tick.ratio - mobile.ticks[index].ratio) * 300 >= 76));
  }));
  test(`${name}: refreshing or panning does not re-anchor shared ticks`, () => inZone("Asia/Shanghai", () => {
    const before = axis(days), after = axis(days, 1100, "2026-09-18T10:38:00+08:00");
    assert.deepEqual(before.ticks.map(t => t.time), after.ticks.map(t => t.time));
    const panned = axis(days, 1100, "2026-09-18T14:37:00+08:00");
    const overlap = before.ticks.filter(t => t.time >= Date.parse("2026-09-18T14:37:00+08:00") - days * DAY);
    assert.ok(overlap.length > 0);
    assert.ok(overlap.every(t => panned.ticks.some(other => other.time === t.time)));
  }));
}

test("24H: first tick and new-day ticks carry explicit dates", () => inZone("Asia/Shanghai", () => {
  const model = axis();
  assert.equal(model.ticks[0].secondary, "9月17日");
  const midnight = model.ticks.find(t => new Date(t.time).getHours() === 0);
  assert.equal(midnight.secondary, "9月18日");
  assert.equal(midnight.major, true);
  assert.equal(model.ticks.find(t => t.primary === "15:00").secondary, "");
}));

test("7D: dates and weekdays agree", () => inZone("Asia/Shanghai", () => {
  const tick = axis(7).ticks.find(t => t.primary === "9月18日");
  assert.equal(tick.secondary, "周五");
  assert.match(tick.title, /2026\/09\/18 00:00 周五/);
}));

test("cross-year range includes both years and highlights the new year", () => inZone("Asia/Shanghai", () => {
  const model = axis(7, 1100, "2027-01-03T10:37:00+08:00");
  assert.match(model.rangeText, /^2026\/12\/27 10:37 — 2027\/01\/03 10:37$/);
  assert.equal(model.ticks.find(t => t.primary === "1月1日").major, true);
}));

test("30D: cross-month labels never reduce to ambiguous day numbers", () => inZone("Asia/Shanghai", () => {
  const model = axis(30);
  assert.ok(model.ticks.some(t => t.primary.startsWith("8月")));
  assert.ok(model.ticks.some(t => t.primary.startsWith("9月")));
  assert.ok(model.ticks.every(t => /^\d+月\d+日$/.test(t.primary)));
  assert.equal(model.ticks.find(t => t.primary.startsWith("9月")).major, true);
}));

test("leap day is retained in calendar-day enumeration", () => inZone("UTC", () => {
  assert.ok(axis(7, 1100, "2028-03-02T12:00:00Z").ticks.some(t => t.primary === "2月29日"));
}));

for (const [zone, offset] of [["Asia/Shanghai", "UTC+08:00"], ["Asia/Tokyo", "UTC+09:00"], ["Asia/Kathmandu", "UTC+05:45"], ["America/St_Johns", "UTC-02:30"], ["UTC", "UTC+00:00"]]) {
  test(`${zone}: all labels use browser-local time and disclose the offset`, () => inZone(zone, () => {
    const model = axis();
    assert.ok(model.zoneText.includes(offset));
    assert.ok(model.ticks.every(t => t.title.includes(offset)));
    assert.ok(model.ticks.every(t => new Date(t.time).getMinutes() === 0));
  }));
}

test("DST spring-forward: skip missing hour, never fabricate 02:00", () => inZone("America/New_York", () => {
  const model = build({startMs:Date.parse("2026-03-08T00:00:00-05:00"), endMs:Date.parse("2026-03-08T06:00:00-04:00"), plotWidthPx:1100});
  assert.equal(model.step, 1);
  assert.ok(!model.ticks.some(t => t.primary === "02:00"));
  assert.ok(model.ticks.some(t => t.primary === "03:00"));
  assert.match(model.zoneText, /UTC-05:00 → UTC-04:00/);
}));

test("DST fall-back: repeated clock hours have distinct offset annotations", () => inZone("America/New_York", () => {
  const model = build({startMs:Date.parse("2026-11-01T00:00:00-04:00"), endMs:Date.parse("2026-11-01T05:00:00-05:00"), plotWidthPx:1100});
  const repeated = model.ticks.filter(t => t.primary === "01:00");
  assert.equal(repeated.length, 2);
  assert.deepEqual(repeated.map(t => t.secondary), ["UTC-04:00", "UTC-05:00"]);
  assert.equal(repeated[1].time - repeated[0].time, HOUR);
}));

test("DST daily ticks remain at local midnight, including 23-hour days", () => inZone("America/New_York", () => {
  const model = axis(7, 1100, "2026-03-11T12:00:00-04:00");
  assert.ok(model.ticks.every(t => new Date(t.time).getHours() === 0));
  assert.ok(model.ticks.slice(1).some((t, i) => t.time - model.ticks[i].time === 23 * HOUR));
}));

test("30-minute DST still produces whole local hours", () => inZone("Australia/Lord_Howe", () => {
  const model = build({startMs:Date.parse("2026-10-04T00:00:00+10:30"), endMs:Date.parse("2026-10-04T08:00:00+11:00"), plotWidthPx:1100});
  assert.ok(model.ticks.every(t => new Date(t.time).getMinutes() === 0));
  assert.match(model.zoneText, /UTC\+10:30 → UTC\+11:00/);
}));

test("ticks at viewport boundaries are included exactly once", () => inZone("UTC", () => {
  const model = axis(1, 1100, "2026-09-18T00:00:00Z");
  assert.equal(model.ticks[0].ratio, 0);
  assert.equal(model.ticks.at(-1).ratio, 1);
  assert.equal(new Set(model.ticks.map(t => t.time)).size, model.ticks.length);
}));

test("positions retain elapsed time, not evenly spaced date strings", () => inZone("America/New_York", () => {
  const endMs = Date.parse("2026-03-11T12:00:00-04:00"), startMs = endMs - 7 * DAY;
  const model = build({startMs, endMs});
  model.ticks.forEach(t => assert.equal(t.ratio, (t.time - startMs) / (endMs - startMs)));
}));

test("epoch zero is a valid endpoint", () => inZone("UTC", () => {
  const model = build({startMs:0, endMs:DAY});
  assert.equal(model.ticks[0].time, 0);
}));

test("invalid dates and unbounded ranges are rejected without looping", () => {
  for (const [startMs, endMs] of [[NaN, 1], [0, Infinity], [1, 1], [2, 1], [0, 367 * DAY], [1e20, 1e20 + DAY]]) {
    assert.throws(() => build({startMs, endMs}), RangeError);
  }
});

test("zero or unavailable layout width has a deterministic fallback", () => inZone("UTC", () => {
  assert.deepEqual(axis(1, 0), axis(1, 320));
  assert.deepEqual(axis(1, NaN), axis(1, 320));
}));

test("DOM rendering uses text, datetime attributes, and leaves grid positions exact", () => inZone("Asia/Shanghai", () => {
  const document = {createElement:() => node(), createDocumentFragment:() => node()};
  function node() { return {children:[], style:{}, dataset:{}, ownerDocument:document, appendChild(child){this.children.push(child);}, replaceChildren(child){this.children = child.children;}, querySelectorAll(){return [];}, getBoundingClientRect(){return {left:0, right:400};}}; }
  const target = node(), summary = node(), scale = node(), zone = node(), model = axis();
  render(model, {axis:target, summary, scale, zone, leftRatio:.048, rightRatio:.018});
  assert.equal(target.children.length, model.ticks.length);
  assert.equal(summary.textContent, `显示范围：${model.rangeText}`);
  assert.equal(scale.textContent, "3 小时刻度");
  assert.equal(zone.textContent, model.zoneText);
  const first = target.children[0], label = first.children[0];
  assert.equal(label.dateTime, new Date(model.ticks[0].time).toISOString());
  assert.equal(label.children[0].textContent, model.ticks[0].primary);
  assert.equal(parseFloat(first.style.left), (.048 + model.ticks[0].ratio * .934) * 100);
  assert.doesNotThrow(() => render(model, {axis:null}));
}));

test("HTML loads axis before app, wires accessible caption, and retains all ranges", () => {
  const html = fs.readFileSync(path.join(__dirname, "../static/index.html"), "utf8");
  assert.ok(html.indexOf('src="chart-axis.js?') < html.indexOf('src="app.js?'));
  for (const id of ["chart-time-axis", "chart-time-summary", "chart-time-scale", "chart-time-zone"]) assert.ok(html.includes(`id="${id}"`));
  assert.ok(html.includes('aria-describedby="chart-time-summary chart-time-scale chart-time-zone"'));
  for (const range of [24, 168, 720]) assert.ok(html.includes(`data-range="${range}"`));
  assert.ok(!html.includes('app.js?v=pins3'));
});
