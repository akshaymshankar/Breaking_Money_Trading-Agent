/* Breaking Money · Trading Arena — client */
(() => {
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem("arena." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("arena." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

const ICONS = {
  bolt: '<path d="M13 3L5 13h6l-1 8 8-10h-6z"/>',
  "arrow.uturn": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a5 5 0 0 1 0 10h-3"/>',
  "chart.up": '<path d="M4 18l6-6 4 4 6-7"/><path d="M15 9h5v5"/>',
  shield: '<path d="M12 3l8 4v5c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V7z"/>',
  "chart.down": '<path d="M4 6l6 6 4-4 6 7"/><path d="M15 15h5v-5"/>',
  people: '<circle cx="8" cy="9" r="2.8"/><circle cx="16.5" cy="9.5" r="2.3"/><path d="M3 18.5c.6-2.8 2.6-4.3 5-4.3s4.400 1.500 5 4.300M14.500 14.600c2.600-.4 4.800 1 5.500 3.900"/>',
  waveform: '<path d="M3 12h2M7 8v8M11 5v14M15 9v6M19 11v2"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
};
const icon = (k) => `<svg class="i" viewBox="0 0 24 24">${ICONS[k] || ICONS.sparkle}</svg>`;
const PRESET_COLORS = { momentum: "#0A84FF", reversion: "#BF5AF2", breakout: "#FF9F0A", guardian: "#30D158", index: "#5E5CE6", bear: "#FF375F", crowd: "#64D2FF" };

/* ---------- state ---------- */
const S = {
  server: null,
  view: store.get("view", "trade"),
  region: store.get("region", null),
  focus: store.get("focus", null),       // agent id shown on Trade
  journalId: null,
  symbol: store.get("symbol", null),
  interval: store.get("interval", "5m"),
  quotes: {}, indices: {},
  detail: null, intel: null,
  decFilter: "act", logFilter: "agent",
  sheetPreset: null,
};

/* ---------- formatting ---------- */
function mkt(region) { return (S.server && S.server.markets[region]) || { symbol: "$", locale: "en-US", currency: "USD" }; }
function money(v, region, opts = {}) {
  if (v === null || v === undefined || isNaN(v)) return "—";
  const m = mkt(region);
  const s = Math.abs(v).toLocaleString(m.locale, { minimumFractionDigits: opts.dp ?? 2, maximumFractionDigits: opts.dp ?? 2 });
  return (v < 0 ? "−" : opts.sign && v > 0 ? "+" : "") + m.symbol + s;
}
function price(v, region) {
  if (v === null || v === undefined) return "—";
  return Number(v).toLocaleString(mkt(region).locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
const pct = (v, dp = 2) => (v === null || v === undefined || isNaN(v)) ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(dp) + "%";
const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
function vol(v) { if (!v) return "—"; if (v >= 1e9) return (v / 1e9).toFixed(2) + "B"; if (v >= 1e6) return (v / 1e6).toFixed(2) + "M"; if (v >= 1e3) return (v / 1e3).toFixed(1) + "K"; return String(v); }
function hhmm(iso) { if (!iso) return ""; const t = iso.slice(11, 16); const d = iso.slice(5, 10); return `${d.replace("-", "/")} ${t}`; }
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now"; if (s < 3600) return Math.round(s / 60) + "m ago"; if (s < 86400) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
}

let toastTimer;
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 3600);
}

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  return r.json();
}

function sparkSVG(values, w, h, color) {
  if (!values || values.length < 2) return `<svg viewBox="0 0 ${w} ${h}" class="spark"></svg>`;
  const lo = Math.min(...values), hi = Math.max(...values), rng = hi - lo || 1;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, h - 2 - ((v - lo) / rng) * (h - 4)]);
  const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join("");
  const id = "g" + Math.random().toString(36).slice(2, 8);
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".22"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs><path d="${d}L${w} ${h}L0 ${h}Z" fill="url(#${id})"/><path d="${d}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

/* ---------- theme ---------- */
function applyTheme(t) {
  if (t) document.documentElement.setAttribute("data-theme", t); else document.documentElement.removeAttribute("data-theme");
  store.set("theme", t); $("themePick").value = t || "";
  setTimeout(restyleCharts, 30);
}

/* ---------- agents helpers ---------- */
const agents = () => (S.server ? S.server.agents : []);
const agentById = (id) => agents().find((a) => a.id === id);
function primaryFor(region) { return agents().find((a) => a.primary && a.region === region && a.status === "active" && a.mode === "live"); }
function focusAgent() {
  let a = agentById(S.focus);
  if (a) return a;
  a = agents().find((x) => x.running && x.region === S.region) || primaryFor(S.region) || agents().find((x) => x.region === S.region) || agents()[0];
  if (a) S.focus = a.id;
  return a;
}
function phaseInfo(a) {
  if (!a) return { tag: "", label: "", dot: "" };
  if (a.error && a.phase === "error") return { tag: "red", label: "Error", dot: "red" };
  const map = {
    deciding: ["blue", "Claude is thinking", "green pulse"], trading: ["green", a.mode === "replay" ? "Replaying" : "Live", "green pulse"],
    waiting: ["orange", "Waiting for open", "orange"], loading: ["blue", "Loading data", "green pulse"], starting: ["blue", "Starting", "green pulse"],
    stopping: ["orange", "Stopping", "orange"], paused: ["", "Paused", ""], idle: ["", a.status === "active" ? "Ready" : a.status, ""],
    complete: ["", "Complete", ""],
  };
  const [tag, label, dot] = map[a.phase] || ["", a.phase || "Ready", ""];
  return { tag, label, dot };
}
function avatar(a, size = 40) {
  return `<div class="avatar" style="background:${a.color};width:${size}px;height:${size}px">${icon(a.icon)}</div>`;
}

/* ---------- render: chrome ---------- */
function renderChrome() {
  const sv = S.server; if (!sv) return;
  const m = sv.markets[S.region];
  document.querySelectorAll("#regionSeg button").forEach((b) => b.classList.toggle("on", b.dataset.r === S.region));
  const pill = $("mktPill");
  pill.innerHTML = `<span class="dot ${m.open ? "green pulse" : ""}"></span><span>${m.open ? "Market open" : "Closed"} · ${esc(m.time.slice(0, 5))} ${esc(m.tz)}</span>`;
  pill.title = `${m.exchange} · session ${m.session} ${m.tz}` + (m.open ? "" : ` · opens ${m.next_open}`);

  // Start / Stop button follows the primary agent of this market
  const p = primaryFor(S.region);
  const btn = $("startBtn");
  const running = p && p.running;
  btn.className = "btn lg " + (running ? "danger" : "primary");
  btn.innerHTML = running
    ? '<svg class="i" viewBox="0 0 24 24"><rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor" stroke="none"/></svg><span>Stop</span>'
    : `<svg class="i" viewBox="0 0 24 24"><path d="M7 5l12 7-12 7z" fill="currentColor" stroke="none"/></svg><span>${p && p.trades ? "Resume trading" : "Start trading"}</span>`;
  btn.dataset.running = running ? "1" : "";
  btn.dataset.id = p ? p.id : "";

  // Claude card
  const c = sv.claude || {};
  const brain = sv.brain_setting;
  let name = "Claude Code", sub = "", dot = "";
  if (!c.checked) { sub = "Checking connection…"; }
  else if (brain === "rules") { name = "Rules strategy"; sub = "No AI · change in Settings"; dot = "orange"; }
  else if (c.cli && c.logged_in === false && !c.api_key) { name = "Claude Code"; sub = "Installed · not signed in"; dot = "orange"; }
  else if (c.cli) { sub = (c.version || "Installed").replace(" (Claude Code)", "") + (c.ok === true ? " · connected" : c.ok === false ? " · test failed" : " · ready"); dot = c.ok === false ? "red" : "green"; }
  else if (c.api_key) { name = "Claude API"; sub = c.ok === false ? "Test failed" : "API key found"; dot = c.ok === false ? "red" : "green"; }
  else { name = "Claude not found"; sub = "Install Claude Code to let Claude trade"; dot = "red"; }
  if (c.ok === false && c.error) sub = c.error.slice(0, 80);
  // One button that does whatever is missing: install -> sign in -> test.
  const mode = !c.checked || brain === "rules" ? "test" : !c.cli && !c.api_key ? "install" : c.cli && c.logged_in === false && !c.api_key ? "login" : "test";
  const tb = $("claudeTest");
  if (!tb.disabled && tb.dataset.mode !== mode) {
    tb.dataset.mode = mode;
    tb.textContent = { install: "Install Claude Code", login: "Sign in with Claude", test: "Test connection" }[mode];
    tb.classList.toggle("primary", mode !== "test");
  }
  $("claudeName").textContent = name; $("claudeSub").textContent = sub; $("claudeSub").title = c.error || c.cli_path || "";
  $("claudeDot").className = "dot " + dot;

  const nRun = sv.running.length;
  $("navCount").textContent = nRun; $("navCount").classList.toggle("hidden", !nRun);

  const titles = { trade: "Trade", agents: "Agents", journal: "Journal", settings: "Settings" };
  $("viewTitle").textContent = titles[S.view];
  const fa = focusAgent();
  const subs = {
    trade: fa ? `${fa.name} · ${m.exchange} · ${phaseInfo(fa).label}` : `${m.exchange} · paper trading with Claude`,
    agents: `${agents().filter((a) => a.status === "active").length} agents · ${nRun} running`,
    journal: "Every trade and every call, with Claude's reasons",
    settings: "Saved to config.toml",
  };
  $("viewSub").textContent = subs[S.view];
}

function renderStrip() {
  const ix = Object.values(S.indices || {});
  $("strip").innerHTML = ix.length
    ? ix.map((q) => `<div class="ix"><b>${esc(q.name)}</b><span class="num">${price(q.ltp, S.region)}</span><span class="num ${cls(q.chg_pct)}">${pct(q.chg_pct)}</span></div>`).join("")
    : `<div class="ix"><span>Loading market indices…</span></div>`;
}

/* ---------- render: trade ---------- */
function watchSymbols() {
  const a = focusAgent();
  if (a && a.region === S.region && a.watchlist && a.watchlist.length) return a.watchlist;
  return (S.server && S.server.markets[S.region].watchlist) || [];
}

function renderWatchlist() {
  const syms = watchSymbols();
  if (!S.symbol || !syms.includes(S.symbol)) S.symbol = syms[0];
  const held = new Set(((S.detail && S.detail.positions_list) || []).map((p) => p.symbol));
  $("wlMeta").textContent = syms.length + " symbols";
  $("watchlist").innerHTML = syms.map((s) => {
    const q = S.quotes[s];
    const ch = q ? q.chg_pct : null;
    const color = ch > 0 ? cssVar("--green") : ch < 0 ? cssVar("--red") : cssVar("--label-3");
    return `<button class="wl-row ${s === S.symbol ? "on" : ""}" data-sym="${esc(s)}">
      <div style="min-width:0"><div class="sym">${esc(s)}${held.has(s) ? '<span class="held-tag">HELD</span>' : ""}</div><div class="nm">${esc(q ? q.name : "")}</div></div>
      ${q ? sparkSVG(q.spark, 54, 24, color).replace("<svg", '<svg class="spark"') : '<span></span>'}
      <div class="px num"><b>${q ? price(q.ltp, S.region) : "—"}</b><span class="chip ${ch > 0 ? "up" : ch < 0 ? "down" : "flat"}">${q ? pct(ch) : "—"}</span></div>
    </button>`;
  }).join("") || '<div class="empty">No symbols in this watchlist</div>';
}

function renderQuoteHead() {
  const q = S.quotes[S.symbol];
  $("qSym").textContent = S.symbol || "—";
  $("qName").textContent = q ? q.name + " · " + mkt(S.region).exchange : mkt(S.region).exchange || "";
  $("qPx").textContent = q ? money(q.ltp, S.region) : "";
  $("qChg").innerHTML = q ? `<span class="${cls(q.chg)}">${q.chg >= 0 ? "+" : "−"}${price(Math.abs(q.chg), S.region)} (${pct(q.chg_pct)})</span> <span class="muted">today</span>` : "";
  $("qStats").innerHTML = q
    ? [["Open", price(q.open, S.region)], ["High", price(q.high, S.region)], ["Low", price(q.low, S.region)], ["Prev close", price(q.prev_close, S.region)], ["Volume", vol(q.volume)]]
        .map(([k, v]) => `<div>${k}<b>${v}</b></div>`).join("")
    : "";
}

function renderAccount() {
  const sel = $("agentPick");
  const list = agents().filter((a) => a.status === "active" || a.id === S.focus);
  const a = focusAgent();
  sel.innerHTML = list.length
    ? list.map((x) => `<option value="${x.id}" ${a && x.id === a.id ? "selected" : ""}>${esc(x.name)} · ${x.region}${x.mode === "replay" ? " · replay" : ""}</option>`).join("")
    : '<option value="">No agent yet: press Start</option>';
  if (!a) {
    $("aEq").textContent = money(S.server ? S.server.settings.starting_capital : 0, S.region, { dp: 0 });
    $("aPnl").innerHTML = '<span class="muted">Starting capital · virtual money</span>';
    $("aPhase").innerHTML = "";
    ["aCash", "aPos", "aTrades", "aWin"].forEach((id) => ($(id).textContent = "—"));
    return;
  }
  $("aEq").textContent = money(a.equity, a.region);
  $("aPnl").innerHTML = `<span class="${cls(a.pnl)}">${money(a.pnl, a.region, { sign: true })} (${pct(a.pnl_pct)})</span> <span class="muted">all time</span>`;
  const ph = phaseInfo(a);
  let detail = a.phase_detail || "";
  if (a.phase === "error") detail = a.error;
  $("aPhase").innerHTML = `<span class="dot ${ph.dot}"></span><span>${esc(ph.label)}${detail ? " · " + esc(detail) : ""}</span>`;
  $("aCash").textContent = money(a.cash, a.region, { dp: 0 });
  $("aPos").textContent = a.positions;
  $("aTrades").textContent = a.trades;
  $("aWin").textContent = a.win_rate === null ? "—" : a.win_rate + "%";
}

function renderPositions() {
  const d = S.detail;
  const list = (d && d.positions_list) || [];
  $("posMeta").textContent = list.length ? list.length + " open" : "";
  if (!list.length) { $("positions").innerHTML = '<div class="empty">No open positions</div>'; return; }
  const region = d.region;
  $("positions").innerHTML = list.map((p) => {
    const ltp = p.last_price || p.avg_price;
    const short = p.qty < 0;
    const pnl = (ltp - p.avg_price) * p.qty, pp = (ltp / p.avg_price - 1) * (short ? -100 : 100);
    // position of price between stop and target
    const span = p.target - p.stop || 1, at = Math.max(0, Math.min(1, (ltp - p.stop) / span)), en = Math.max(0, Math.min(1, (p.avg_price - p.stop) / span));
    const left = Math.min(at, en) * 100, width = Math.abs(at - en) * 100;
    return `<div class="pos-row" data-sym="${esc(p.symbol)}">
      <div><div class="s">${esc(p.symbol)}${short ? '<span class="short-tag">SHORT</span>' : ""}</div><div class="d num">${Math.abs(p.qty)} @ ${price(p.avg_price, region)} · stop ${price(p.stop, region)} · target ${price(p.target, region)}</div></div>
      <div class="r num ${cls(pnl)}">${money(pnl, region, { sign: true })}<div class="d">${pct(pp)}</div></div>
      <div class="bar" title="Stop ← entry → target"><i style="left:${left}%;width:${Math.max(width, 1.5)}%;background:${pnl >= 0 ? "var(--green)" : "var(--red)"}"></i></div>
    </div>`;
  }).join("");
}

function renderDecisions() {
  const d = S.detail;
  const mv = d && d.market_view;
  $("marketView").classList.toggle("hidden", !mv || !mv.market_view);
  if (mv && mv.market_view) $("marketView").innerHTML = `<span class="when">Market view · ${esc(hhmm(mv.ts))} · ${esc(mv.brain)}</span>${esc(mv.market_view)}`;
  let list = (d && d.decisions) || [];
  if (S.decFilter === "act") list = list.filter((x) => x.action !== "HOLD");
  if (!list.length) {
    $("decisions").innerHTML = `<div class="empty">${d && d.decisions && d.decisions.length ? "Only HOLD calls so far. Claude is waiting for a setup." : "Press Start and Claude's calls appear here, each with its reason."}</div>`;
    return;
  }
  $("decisions").innerHTML = list.slice(0, 40).map((x) => {
    const filled = (x.outcome || "").startsWith(x.action) && !(x.outcome || "").includes("rejected");
    return `<div class="dec"><div class="dec-top"><span class="act ${x.action}">${x.action}</span><span class="s">${esc(x.symbol)}</span>
      <span class="conf" title="Confidence ${Math.round((x.confidence || 0) * 100)}%"><i style="width:${Math.round((x.confidence || 0) * 100)}%"></i></span>
      <span class="t">${esc(hhmm(x.ts))}</span></div>
      <p>${esc(x.reason)}</p>
      ${x.action !== "HOLD" ? `<div class="out"><span class="dot ${filled ? "green" : "orange"}" style="width:6px;height:6px;box-shadow:none"></span>${esc(x.outcome || "")}</div>` : ""}</div>`;
  }).join("");
}

const REGIME_TAG = { bull: "green", neutral: "", bear: "red", panic: "red" };
function renderMood() {
  const x = S.intel, box = $("mood"), tag = $("regimeTag");
  const d = S.detail, run = d && d.region === S.region ? d : null;
  // The agent's own regime (full data) beats the quick estimate, while that agent is working.
  const a = focusAgent();
  const reg = (run && a && a.running && run.regime) || (x && x.regime);
  $("moodCard").classList.toggle("hidden", !!x && x.enabled === false);
  tag.className = "tag " + (reg ? REGIME_TAG[reg.label] || "" : "");
  tag.textContent = reg ? `${reg.label[0].toUpperCase() + reg.label.slice(1)} market` : "—";
  tag.title = reg ? `Score ${reg.score}: ${reg.why}` : "";
  if (!x || !x.mood) { box.innerHTML = `<div class="empty">${x && x.error ? "News is unavailable right now." : "Reading world news…"}</div>`; return; }
  const m = x.mood, open = box.querySelectorAll("details"), was = [...open].map((e) => e.open);
  const shorts = reg ? (x.short_mode === "off" ? "Short selling is off." : x.short_mode === "always" || ["bear", "panic"].includes(reg.label)
    ? "Shorts can open." : "Shorts stay closed until the market turns bearish.") : "";
  const emo = Object.entries(m.emotions || {}).filter(([, v]) => v >= 0.1).sort((p, q) => q[1] - p[1]).slice(0, 3);
  const steps = (run && run.desk && run.desk.steps && run.desk.steps.length ? run.desk.steps : x.trace) || [];
  box.innerHTML = `<div class="mood-top"><b class="num">${m.mood}</b><span>${esc(m.label)}</span></div>
    <div class="mood-bar" role="img" aria-label="Mood ${m.mood} out of 100, ${esc(m.label)}"><i style="left:${Math.max(3, Math.min(97, m.mood))}%"></i></div>
    <div class="mood-ends"><span>Fear</span><span>Greed</span></div>
    <p>${esc(m.crowd_read)}</p>
    ${emo.length ? `<div class="emo">${emo.map(([k, v]) => `<span class="tag">${esc(k)} ${Math.round(v * 100)}%</span>`).join("")}</div>` : ""}
    ${shorts ? `<p>${shorts}</p>` : ""}
    <details ${was[0] ? "open" : ""}><summary>Headlines</summary><ul>${(x.headlines || []).slice(0, 8).map((h) =>
      `<li>${/^https?:\/\//.test(h.link) ? `<a href="${esc(h.link)}" target="_blank" rel="noopener noreferrer">${esc(h.title)}</a>` : esc(h.title)}<small>${esc(h.source)} · ${h.age_min < 60 ? h.age_min + "m" : Math.round(h.age_min / 60) + "h"} ago</small></li>`).join("")}</ul></details>
    <details ${was[1] ? "open" : ""}><summary>Agent desk</summary><ul>${steps.map((t) =>
      `<li><span class="dot ${t.status === "ok" ? "green" : t.status === "failed" ? "red" : "orange"}"></span>${esc(t.agent)}<small>${esc(t.summary)}</small></li>`).join("")}</ul></details>`;
}
async function loadIntel() {
  const region = S.region;
  if (!region) return;
  try {
    const d = await api("/api/intel?region=" + region);
    if (region === S.region) { S.intel = d; renderMood(); }
  } catch { /* keep last */ }
}

function renderBanner() {
  const m = S.server.markets[S.region];
  const p = primaryFor(S.region);
  const show = !m.open;
  $("closedBanner").classList.toggle("hidden", !show);
  if (!show) return;
  $("cbTitle").textContent = `${m.name} market is closed`;
  $("cbText").textContent = p && p.running
    ? `Claude is armed and starts trading at the open: ${m.next_open} ${m.tz}. Want to see it work now? Replay the last real session.`
    : `Opens ${m.next_open} ${m.tz}. Press Start to arm Claude for the open, or practice right now on the last real session.`;
}

/* ---------- chart ---------- */
let chart, candleSeries, volSeries, chartKey = "", chartTimer;
function chartColors() {
  return { text: cssVar("--label-3"), grid: cssVar("--chart-grid"), up: cssVar("--green"), down: cssVar("--red"), line: cssVar("--hairline") };
}
function ensureChart() {
  if (chart || !window.LightweightCharts) return;
  const c = chartColors();
  chart = LightweightCharts.createChart($("chart"), {
    autoSize: true,
    layout: { background: { type: "solid", color: "transparent" }, textColor: c.text, fontFamily: getComputedStyle(document.body).fontFamily, fontSize: 11 },
    grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.24 } },
    timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false, rightOffset: 4 },
    crosshair: { mode: 0, vertLine: { labelBackgroundColor: "#636366" }, horzLine: { labelBackgroundColor: "#636366" } },
    handleScale: { axisPressedMouseMove: true }, localization: { locale: "en-US" },
  });
  candleSeries = chart.addCandlestickSeries({ upColor: c.up, downColor: c.down, borderVisible: false, wickUpColor: c.up, wickDownColor: c.down, priceLineVisible: true });
  volSeries = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
}
function restyleCharts() {
  const c = chartColors();
  const opts = { layout: { textColor: c.text }, grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } } };
  if (chart) { chart.applyOptions(opts); candleSeries.applyOptions({ upColor: c.up, downColor: c.down, wickUpColor: c.up, wickDownColor: c.down }); loadChart(true); }
  if (eqChart) eqChart.applyOptions(opts);
}
async function loadChart(force) {
  if (!S.symbol) return;
  ensureChart();
  if (!chart) { $("chartLoading").textContent = "Chart library unavailable (offline?)"; return; }
  const a = focusAgent();
  const key = `${S.region}|${S.symbol}|${S.interval}`;
  const fresh = key !== chartKey;
  if (fresh) { $("chartLoading").textContent = "Loading chart…"; $("chartLoading").classList.remove("hidden"); }
  const qs = new URLSearchParams({ region: S.region, symbol: S.symbol, interval: S.interval });
  if (a && a.region === S.region) qs.set("agent", a.id);
  try {
    const d = await api("/api/candles?" + qs);
    if (`${S.region}|${S.symbol}|${S.interval}` !== key) return; // user switched meanwhile
    const c = chartColors();
    const bars = d.bars || [];
    candleSeries.setData(bars.map((b) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
    volSeries.setData(bars.map((b) => ({ time: b.time, value: b.volume, color: (b.close >= b.open ? c.up : c.down) + "55" })));
    const markers = (d.markers || []).map((m) => {
      // snap to the bar that contains the fill
      let t = m.time; for (let i = bars.length - 1; i >= 0; i--) { if (bars[i].time <= m.time) { t = bars[i].time; break; } }
      const up = m.side === "BUY" || m.side === "COVER";
      return { time: t, position: up ? "belowBar" : "aboveBar", color: up ? c.up : c.down,
        shape: up ? "arrowUp" : "arrowDown", text: `${m.side} ${m.qty}` };
    }).filter((m) => bars.length && m.time >= bars[0].time).sort((x, y) => x.time - y.time);
    candleSeries.setMarkers(markers);
    chart.applyOptions({ timeScale: { timeVisible: S.interval !== "1D" } });
    if (fresh) chart.timeScale().fitContent();
    chartKey = key;
    $("chartLoading").classList.toggle("hidden", bars.length > 0);
    if (!bars.length) $("chartLoading").textContent = "No price data for " + S.symbol;
  } catch (e) {
    $("chartLoading").textContent = "Could not load chart data";
    $("chartLoading").classList.remove("hidden");
  }
}
function scheduleChart() {
  clearInterval(chartTimer);
  const live = S.server && S.server.markets[S.region].open;
  chartTimer = setInterval(() => { if (S.view === "trade" && !document.hidden) loadChart(false); }, live && ["1m", "5m"].includes(S.interval) ? 20000 : 90000);
}

/* ---------- render: agents ---------- */
function renderPresets() {
  const p = (S.server && S.server.presets) || [];
  $("presets").innerHTML = p.map((x) => `<button class="preset" data-preset="${x.key}">
    <div class="pi" style="background:${PRESET_COLORS[x.key] || "#0A84FF"}">${icon(x.icon)}</div>
    <b>${esc(x.name)}</b><p>${esc(x.persona)}</p><span class="add"><svg class="i" viewBox="0 0 24 24" style="width:14px;height:14px"><path d="M12 5v14M5 12h14"/></svg>Spawn</span></button>`).join("");
}
function renderAgents() {
  const list = agents().filter((a) => a.status !== "archived");
  const live = list.filter((a) => a.running);
  const byReg = {};
  list.filter((a) => a.status === "active").forEach((a) => { (byReg[a.region] = byReg[a.region] || []).push(a); });
  $("mRunning").textContent = live.length;
  $("mRunningSub").textContent = `${list.filter((a) => a.status === "active").length} active · ${list.length} total`;
  const regions = Object.keys(byReg);
  if (regions.length) {
    const r = regions.includes(S.region) ? S.region : regions[0];
    const eq = byReg[r].reduce((s, a) => s + a.equity, 0), cap = byReg[r].reduce((s, a) => s + a.capital, 0);
    $("mEquity").textContent = money(eq, r, { dp: 0 });
    $("mEquitySub").innerHTML = `<span class="${cls(eq - cap)}">${money(eq - cap, r, { sign: true, dp: 0 })}</span> across ${byReg[r].length} ${r} agent${byReg[r].length > 1 ? "s" : ""}`;
  } else { $("mEquity").textContent = "—"; $("mEquitySub").textContent = "Spawn an agent to begin"; }
  const ranked = [...list].sort((a, b) => b.pnl_pct - a.pnl_pct);
  const leader = ranked.find((a) => a.trades > 0) || null;
  $("mLeader").textContent = leader ? leader.name : "—";
  $("mLeaderSub").innerHTML = leader ? `<span class="${cls(leader.pnl_pct)}">${pct(leader.pnl_pct)}</span> · ${leader.trades} trades` : "No closed trades yet";
  const cycles = list.reduce((s, a) => s + (a.cycles || 0), 0);
  $("mCycles").textContent = cycles.toLocaleString();
  $("mCyclesSub").textContent = "Claude decision cycles, all agents";

  $("agents").innerHTML = list.length ? list.map((a) => {
    const ph = phaseInfo(a);
    const color = a.pnl >= 0 ? cssVar("--green") : cssVar("--red");
    const model = a.brain === "claude-cli" ? "Claude Code" + (a.model ? " · " + a.model : "") : a.brain === "claude-api" ? "Claude API" : "Rules";
    const detail = a.phase === "error" ? `<span class="err">${esc(a.error)}</span>` : esc(a.phase_detail || (a.running ? "" : a.started_at ? "Created " + ago(a.started_at) : ""));
    return `<div class="card agent ${a.running ? "running" : ""}">
      <div class="agent-top">${avatar(a)}<div class="nm"><b>${esc(a.name)}</b><small>${a.region} · ${esc(model)}${a.mode === "replay" ? " · replay" : ""}</small></div>
        <span class="state tag ${ph.tag}">${ph.dot.includes("pulse") ? '<span class="dot green pulse" style="width:6px;height:6px"></span>' : ""}${esc(ph.label)}</span></div>
      <p class="persona">${esc(a.persona || (a.primary ? "Main agent behind the Start button. Uses your strategy notes from Settings." : "General-purpose Claude agent. Uses your strategy notes from Settings."))}</p>
      <div class="figs"><div><div class="e num">${money(a.equity, a.region, { dp: 0 })}</div><div class="p num ${cls(a.pnl)}">${money(a.pnl, a.region, { sign: true })} · ${pct(a.pnl_pct)}</div></div>${sparkSVG(a.spark, 120, 40, color)}</div>
      <div class="mini num"><span><b>${a.positions}</b> open</span><span><b>${a.trades}</b> trades</span><span><b>${a.win_rate === null ? "—" : a.win_rate + "%"}</b> win</span><span><b>${a.cycles}</b> calls</span></div>
      <div class="detail">${detail}</div>
      <div class="foot">
        ${a.status === "active" ? (a.running ? `<button class="btn sm" data-act="stop" data-id="${a.id}">Stop</button>` : `<button class="btn sm primary" data-act="start" data-id="${a.id}">${a.cycles ? "Resume" : "Start"}</button>`) : ""}
        <button class="btn sm" data-act="view" data-id="${a.id}">View</button>
        <button class="btn sm ghost" data-act="remove" data-id="${a.id}" title="Archive this agent">Archive</button>
      </div></div>`;
  }).join("") : '<div class="card empty" style="grid-column:1/-1">No agents yet. Pick a preset above or press <b>Start trading</b>.</div>';

  $("board").innerHTML = ranked.length ? `<thead><tr><th>#</th><th>Agent</th><th>Market</th><th>Model</th><th class="r">Equity</th><th class="r">Return</th><th class="r">Trades</th><th class="r">Win rate</th><th class="r">Charges</th><th>Status</th></tr></thead><tbody>` +
    ranked.map((a, i) => `<tr><td><span class="rank ${i === 0 && a.trades ? "g" : ""}">${i + 1}</span></td>
      <td><div style="display:flex;align-items:center;gap:10px">${avatar(a, 26).replace('font-size:15px', '')}<b style="font-weight:600">${esc(a.name)}</b></div></td>
      <td>${a.region}${a.mode === "replay" ? " · replay" : ""}</td><td>${esc(a.brain === "claude-cli" ? (a.model || "default") : a.brain)}</td>
      <td class="r">${money(a.equity, a.region, { dp: 0 })}</td><td class="r ${cls(a.pnl_pct)}">${pct(a.pnl_pct)}</td><td class="r">${a.trades}</td>
      <td class="r">${a.win_rate === null ? "—" : a.win_rate + "%"}</td><td class="r">${money(a.charges, a.region)}</td><td><span class="tag ${phaseInfo(a).tag}">${esc(phaseInfo(a).label)}</span></td></tr>`).join("") + "</tbody>"
    : `<tbody><tr><td class="empty">Nothing to rank yet.</td></tr></tbody>`;
}

/* ---------- render: journal ---------- */
let eqChart, eqSeries;
async function renderJournal() {
  const list = agents();
  if (!S.journalId || !agentById(S.journalId)) S.journalId = (focusAgent() || {}).id;
  $("journalPick").innerHTML = list.map((a) => `<option value="${a.id}" ${a.id === S.journalId ? "selected" : ""}>${esc(a.name)} · ${a.region}${a.status !== "active" ? " · " + a.status : ""}</option>`).join("") || "<option>No agents yet</option>";
  if (!S.journalId) { $("tradesTbl").innerHTML = ""; $("callsTbl").innerHTML = ""; renderLog(null); return; }
  $("reportLink").href = "/report?id=" + S.journalId;
  const d = S.journalId === (S.detail && S.detail.id) ? S.detail : await api("/api/agent?id=" + S.journalId);
  const region = d.region;
  if (!eqChart && window.LightweightCharts) {
    const c = chartColors();
    eqChart = LightweightCharts.createChart($("eqChart"), {
      autoSize: true, layout: { background: { type: "solid", color: "transparent" }, textColor: c.text, fontSize: 11, fontFamily: getComputedStyle(document.body).fontFamily },
      grid: { vertLines: { visible: false }, horzLines: { color: c.grid } }, rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false, timeVisible: true },
      crosshair: { mode: 0 }, handleScroll: false, handleScale: false,
    });
    eqSeries = eqChart.addAreaSeries({ lineColor: cssVar("--blue"), topColor: "rgba(10,132,255,.25)", bottomColor: "rgba(10,132,255,0)", lineWidth: 2 });
  }
  if (eqSeries) {
    const seen = new Set();
    const pts = (d.equity_curve || []).map((p) => ({ time: Math.floor(new Date(p.ts).getTime() / 1000), value: p.equity }))
      .filter((p) => !seen.has(p.time) && seen.add(p.time)).sort((a, b) => a.time - b.time);
    eqSeries.setData(pts); eqChart.timeScale().fitContent();
  }
  const tr = d.trades_list || [];
  $("tradesMeta").textContent = tr.length ? `${tr.length} trades · net ${money(tr.reduce((s, t) => s + t.net_pnl, 0), region, { sign: true })}` : "";
  $("tradesTbl").innerHTML = tr.length ? `<thead><tr><th>Symbol</th><th>Entry</th><th>Exit</th><th class="r">Qty</th><th class="r">In</th><th class="r">Out</th><th class="r">Net P&amp;L</th><th>Exit reason</th><th class="r">Held</th></tr></thead><tbody>` +
    tr.map((t) => `<tr><td><b style="font-weight:600">${esc(t.symbol)}</b>${t.side === "SHORT" ? '<span class="short-tag">SHORT</span>' : ""}</td><td>${esc(hhmm(t.entry_ts))}</td><td>${esc(hhmm(t.exit_ts))}</td><td class="r">${t.qty}</td>
      <td class="r">${price(t.entry_price, region)}</td><td class="r">${price(t.exit_price, region)}</td><td class="r ${cls(t.net_pnl)}">${money(t.net_pnl, region, { sign: true })}</td>
      <td>${esc((t.exit_reason || "").replace("_", " "))}</td><td class="r">${Math.round(t.hold_minutes)}m</td></tr>`).join("") + "</tbody>"
    : '<tbody><tr><td class="empty">No closed trades yet.</td></tr></tbody>';
  const calls = (d.decisions || []).filter((x) => x.action !== "HOLD").slice(0, 60);
  $("callsTbl").innerHTML = calls.length ? `<thead><tr><th>Time</th><th>Call</th><th>Symbol</th><th class="r">Conf.</th><th>Reason</th><th>Outcome</th></tr></thead><tbody>` +
    calls.map((x) => `<tr><td class="num">${esc(hhmm(x.ts))}</td><td><span class="act ${x.action}">${x.action}</span></td><td><b style="font-weight:600">${esc(x.symbol)}</b></td>
      <td class="r num">${Math.round((x.confidence || 0) * 100)}%</td><td class="wrap">${esc(x.reason)}</td><td class="wrap">${esc(x.outcome || "")}</td></tr>`).join("") + "</tbody>"
    : '<tbody><tr><td class="empty">No trade calls yet.</td></tr></tbody>';
  renderLog(d);
}
function renderLog(d) {
  const lines = S.logFilter === "all" || !d ? (S.server ? S.server.log : []) : (d.log || []);
  const el = $("log");
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 8;
  el.innerHTML = lines.length ? lines.map((l) => `<div class="${l.lvl}"><span class="t">${esc(l.t)}</span><span class="w">${esc(l.who)}</span><span class="m">${esc(l.m)}</span></div>`).join("") : '<div class="muted" style="display:block">Nothing logged yet.</div>';
  if (atBottom) el.scrollTop = el.scrollHeight;
}

/* ---------- settings ---------- */
let settingsLoaded = false;
function fillSettings(force) {
  if (settingsLoaded && !force) return;
  const s = S.server.settings;
  Object.entries(s).forEach(([k, v]) => { const el = $("s_" + k); if (el) el.value = v; });
  settingsLoaded = true;
}
async function saveSettings() {
  const keys = ["region", "watchlist_us", "watchlist_in", "feed", "brain", "cli_model", "decision_minutes", "notes", "starting_capital", "challenge_days", "profile", "product", "max_positions", "max_position_pct", "max_daily_loss_pct", "short_mode", "intel_enabled"];
  const body = {}; keys.forEach((k) => (body[k] = $("s_" + k).value));
  const r = await api("/api/settings", body);
  toast(r.msg === "ok" ? "Settings saved" : r.msg);
  if (r.msg === "ok") { await poll(); fillSettings(true); loadQuotes(); loadIntel(); }
}

/* ---------- sheet ---------- */
function openSheet(presetKey) {
  const presets = S.server.presets;
  S.sheetPreset = presetKey || null;
  $("sheetPresets").innerHTML = `<button data-k="" class="${!presetKey ? "on" : ""}">Custom</button>` + presets.map((p) => `<button data-k="${p.key}" class="${p.key === presetKey ? "on" : ""}">${esc(p.name)}</button>`).join("");
  fillFromPreset(presetKey);
  $("f_region").value = S.region;
  $("f_mode").value = S.server.markets[S.region].open ? "live" : "replay";
  $("f_model").value = S.server.settings.cli_model || "";
  $("f_capital").value = S.server.settings.starting_capital;
  $("f_watchlist").value = "";
  $("f_watchlist").placeholder = (S.server.markets[S.region].watchlist || []).join(", ");
  toggleDays();
  $("sheet").classList.remove("hidden");
  setTimeout(() => $("f_name").focus(), 50);
}
function fillFromPreset(k) {
  const p = S.server.presets.find((x) => x.key === k);
  $("f_name").value = p ? p.name : "";
  $("f_persona").value = p ? p.persona : "";
  $("f_minutes").value = p ? p.decision_minutes : S.server.settings.decision_minutes;
}
function toggleDays() { $("f_days_wrap").style.visibility = $("f_mode").value === "replay" ? "visible" : "hidden"; }
function closeSheet() { $("sheet").classList.add("hidden"); }
async function spawnFromSheet() {
  const btn = $("sheetSpawn"); btn.disabled = true;
  const body = {
    preset: S.sheetPreset, name: $("f_name").value, persona: $("f_persona").value, region: $("f_region").value, mode: $("f_mode").value,
    model: $("f_model").value, decision_minutes: +$("f_minutes").value, capital: +$("f_capital").value, replay_days: +$("f_days").value,
    watchlist: $("f_watchlist").value,
  };
  const r = await api("/api/agents", body);
  btn.disabled = false;
  if (r.msg !== "ok") { toast(r.msg); return; }
  closeSheet(); toast(`${body.name || "Agent"} spawned`);
  S.focus = r.id; store.set("focus", r.id);
  await poll();
}

/* ---------- actions ---------- */
async function onStart() {
  const btn = $("startBtn");
  if (btn.dataset.running) {
    await api("/api/stop", { id: +btn.dataset.id });
    toast("Stopping after the current step. Cash and positions are saved.");
  } else {
    btn.disabled = true; btn.innerHTML = '<span class="spinner"></span><span>Starting…</span>';
    const r = await api("/api/start", { region: S.region });
    btn.disabled = false;
    if (r.msg !== "ok") toast(r.msg);
    else {
      S.focus = r.id; store.set("focus", r.id);
      const m = S.server.markets[S.region];
      toast(m.open ? "Claude is trading. First decision in a few seconds." : `Armed. Claude starts at the open: ${m.next_open} ${m.tz}.`);
    }
  }
  await poll();
}
async function practice() {
  const r = await api("/api/agents", { name: "Claude · Replay " + S.region, region: S.region, mode: "replay", replay_days: 1,
    persona: "", model: S.server.settings.cli_model || "" });
  if (r.msg !== "ok") return toast(r.msg);
  S.focus = r.id; store.set("focus", r.id);
  toast("Replaying the last real session. Claude decides every " + S.server.settings.decision_minutes + " market minutes.");
  await poll();
}
async function agentAction(act, id) {
  if (act === "view") { S.focus = id; store.set("focus", id); const a = agentById(id); if (a && a.region !== S.region) setRegion(a.region); setView("trade"); return; }
  if (act === "remove" && !confirm("Archive this agent? It stops trading; its history stays in the journal.")) return;
  const r = await api("/api/agents/" + act, { id });
  if (r.msg !== "ok") toast(r.msg);
  await poll();
}
async function spawnPreset(key) {
  const m = S.server.markets[S.region];
  const r = await api("/api/agents", { preset: key, region: S.region, mode: m.open ? "live" : "replay", replay_days: 1, model: S.server.settings.cli_model || "" });
  if (r.msg !== "ok") return toast(r.msg);
  const p = S.server.presets.find((x) => x.key === key);
  toast(`${p ? p.name : "Agent"} spawned${m.open ? "" : " on the last session (market closed)"}`);
  await poll();
}
async function spawnSquad() {
  const m = S.server.markets[S.region];
  const r = await api("/api/agents/squad", { region: S.region, mode: m.open ? "live" : "replay", replay_days: 1, model: S.server.settings.cli_model || "" });
  if (r.msg !== "ok") return toast(r.msg);
  toast(`Squad of ${r.ids.length} agents spawned${m.open ? "" : " on the last session"}`);
  await poll();
}

/* ---------- navigation ---------- */
function setView(v) {
  S.view = v; store.set("view", v);
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
  document.querySelectorAll(".view").forEach((s) => s.classList.toggle("hidden", s.id !== "view-" + v));
  if (v === "settings") fillSettings(true);
  render();
  if (v === "trade") setTimeout(() => loadChart(true), 30);
  if (v === "journal") renderJournal();
}
function setRegion(r) {
  if (r === S.region) return;
  S.region = r; store.set("region", r);
  const a = agentById(S.focus);
  if (a && a.region !== r) S.focus = null;
  S.symbol = null; S.quotes = {}; S.indices = {}; S.detail = null; S.intel = null;
  renderMood(); loadIntel();
  render(); loadQuotes(); loadDetail(); scheduleChart();
}

/* ---------- data loops ---------- */
async function poll() {
  try {
    S.server = await api("/api/state");
  } catch { return; }
  if (!S.region) S.region = S.server.region;
  if (!$("themePick").dataset.init) { $("themePick").dataset.init = 1; }
  render();
}
async function loadQuotes() {
  const syms = watchSymbols(); if (!syms.length) return;
  try {
    const d = await api(`/api/quotes?region=${S.region}&symbols=${encodeURIComponent(syms.join(","))}`);
    S.quotes = d.quotes || {}; S.indices = d.indices || {};
    renderWatchlist(); renderQuoteHead(); renderStrip();
  } catch { /* keep last */ }
}
async function loadDetail() {
  const a = focusAgent();
  if (!a) { S.detail = null; renderPositions(); renderDecisions(); return; }
  try {
    const d = await api("/api/agent?id=" + a.id);
    const fillsChanged = !S.detail || S.detail.id !== d.id || (S.detail.fills || []).length !== (d.fills || []).length;
    S.detail = d;
    renderPositions(); renderDecisions(); renderWatchlist(); renderMood();
    if (fillsChanged && S.view === "trade") loadChart(false);
    if (S.view === "journal" && S.journalId === d.id) renderJournal();
  } catch { /* ignore */ }
}

function render() {
  if (!S.server) return;
  renderChrome();
  if (S.view === "trade") { renderBanner(); renderAccount(); renderWatchlist(); renderQuoteHead(); }
  if (S.view === "agents") { if (!$("presets").children.length) renderPresets(); renderAgents(); }
  if (S.view === "journal") renderLog(S.journalId === (S.detail && S.detail.id) ? S.detail : null);
}

/* ---------- events ---------- */
document.querySelectorAll(".nav-item").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
document.querySelectorAll("#regionSeg button").forEach((b) => b.addEventListener("click", () => setRegion(b.dataset.r)));
document.querySelectorAll("#intervalSeg button").forEach((b) => b.addEventListener("click", () => {
  S.interval = b.dataset.i; store.set("interval", S.interval);
  document.querySelectorAll("#intervalSeg button").forEach((x) => x.classList.toggle("on", x === b));
  loadChart(true); scheduleChart();
}));
document.querySelectorAll("#decFilter button").forEach((b) => b.addEventListener("click", () => {
  S.decFilter = b.dataset.f; document.querySelectorAll("#decFilter button").forEach((x) => x.classList.toggle("on", x === b)); renderDecisions();
}));
document.querySelectorAll("#logFilter button").forEach((b) => b.addEventListener("click", () => {
  S.logFilter = b.dataset.l; document.querySelectorAll("#logFilter button").forEach((x) => x.classList.toggle("on", x === b)); renderJournal();
}));
$("watchlist").addEventListener("click", (e) => {
  const row = e.target.closest(".wl-row"); if (!row) return;
  S.symbol = row.dataset.sym; store.set("symbol", S.symbol); renderWatchlist(); renderQuoteHead(); loadChart(true);
});
$("positions").addEventListener("click", (e) => {
  const row = e.target.closest(".pos-row"); if (!row) return;
  S.symbol = row.dataset.sym; renderWatchlist(); renderQuoteHead(); loadChart(true);
});
$("agentPick").addEventListener("change", (e) => {
  S.focus = +e.target.value; store.set("focus", S.focus);
  const a = agentById(S.focus); if (a && a.region !== S.region) setRegion(a.region);
  S.detail = null; render(); loadDetail(); loadQuotes(); loadChart(true);
});
$("journalPick").addEventListener("change", (e) => { S.journalId = +e.target.value; renderJournal(); });
$("startBtn").addEventListener("click", onStart);
$("practiceBtn").addEventListener("click", practice);
let claudeWatch = null;
function watchClaude() {  // after install / sign-in, re-check every 4s until it is ready (max 5 min)
  clearInterval(claudeWatch);
  let n = 0;
  claudeWatch = setInterval(async () => {
    const r = await api("/api/claude/refresh", {});
    if ((r.cli && r.logged_in !== false) || ++n > 75) {
      clearInterval(claudeWatch);
      if (r.cli && r.logged_in !== false) toast("Claude Code is connected. Press Start to trade.");
    }
    poll();
  }, 4000);
}
$("claudeTest").addEventListener("click", async () => {
  const b = $("claudeTest"), mode = b.dataset.mode || "test";
  if (mode !== "test") {
    const r = await api(mode === "install" ? "/api/claude/install" : "/api/claude/login", {});
    toast(r.msg); watchClaude(); return;
  }
  b.disabled = true; b.innerHTML = '<span class="spinner"></span>Testing…';
  const r = await api("/api/claude/check", {});
  b.disabled = false; b.textContent = "Test connection";
  toast(r.ok ? `Claude replied "${r.reply}" in ${r.seconds}s. Ready to trade.` : "Claude test failed: " + (r.error || "no reply"));
  poll();
});
$("presets").addEventListener("click", (e) => { const p = e.target.closest(".preset"); if (p) openSheet(p.dataset.preset); });
$("agents").addEventListener("click", (e) => { const b = e.target.closest("[data-act]"); if (b) agentAction(b.dataset.act, +b.dataset.id); });
$("newAgentBtn").addEventListener("click", () => openSheet(null));
$("squadBtn").addEventListener("click", spawnSquad);
$("stopAllBtn").addEventListener("click", async () => { const r = await api("/api/stop", {}); toast(r.msg === "ok" ? "Stopping all agents" : r.msg); poll(); });
$("sheetPresets").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  S.sheetPreset = b.dataset.k || null;
  document.querySelectorAll("#sheetPresets button").forEach((x) => x.classList.toggle("on", x === b));
  fillFromPreset(S.sheetPreset);
});
$("f_mode").addEventListener("change", toggleDays);
$("f_region").addEventListener("change", () => { $("f_watchlist").placeholder = (S.server.markets[$("f_region").value].watchlist || []).join(", "); });
$("sheetClose").addEventListener("click", closeSheet);
$("sheetCancel").addEventListener("click", closeSheet);
$("sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") closeSheet(); });
$("sheetSpawn").addEventListener("click", spawnFromSheet);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
$("saveBtn").addEventListener("click", saveSettings);
$("demoBtn").addEventListener("click", async () => { const r = await api("/api/demo", { days: 3 }); toast(r.msg === "ok" ? "Demo running on made-up prices. Open /demo when it finishes." : r.msg); });
$("reviewBtn").addEventListener("click", async () => { if (!S.journalId) return; const r = await api("/api/review", { id: S.journalId }); toast(r.msg === "ok" ? "Claude is writing a coach review. It appears in the full report." : r.msg); });
$("themePick").addEventListener("change", (e) => applyTheme(e.target.value));
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => setTimeout(restyleCharts, 30));

/* ---------- boot ---------- */
applyTheme(store.get("theme", ""));
document.querySelectorAll("#intervalSeg button").forEach((x) => x.classList.toggle("on", x.dataset.i === S.interval));
(async function boot() {
  await poll();
  setView(S.view);
  loadQuotes(); loadDetail(); loadIntel(); scheduleChart();
  setInterval(() => { if (!document.hidden) loadIntel(); }, 60000);
  setInterval(() => { if (!document.hidden) poll(); }, 2500);
  setInterval(() => { if (!document.hidden) loadDetail(); }, 3500);
  setInterval(() => { if (!document.hidden) loadQuotes(); }, 15000);
})();
})();
