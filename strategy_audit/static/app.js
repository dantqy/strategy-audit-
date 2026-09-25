/* Strategy Audit Platform front end. Vanilla JS; every dynamic value is rendered with textContent (via h()),
   never innerHTML, so user text cannot inject markup. All numbers shown come from the API. */
"use strict";

// ------------------------------------------------------------------ helpers
const app = document.getElementById("app");
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const pct = (x, d = 1) => (x === null || x === undefined) ? "n/a" : (x > 0 ? "+" : "") + (x * 100).toFixed(d) + "%";
const pctAbs = (x, d = 0) => (x === null || x === undefined) ? "n/a" : (x * 100).toFixed(d) + "%";
const num = (x, d = 2) => (x === null || x === undefined) ? "n/a" : Number(x).toFixed(d);
const usd = (x) => (x === null || x === undefined) ? "n/a" : "$" + Math.round(x).toLocaleString();
const anon = (() => {
  try {
    let a = localStorage.getItem("sa_anon");
    if (!a) { a = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)); localStorage.setItem("sa_anon", a); }
    return a;
  } catch { return null; }
})();
const store = {
  get(k) { try { return JSON.parse(sessionStorage.getItem(k)); } catch { return null; } },
  set(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode: state lives in memory only */ } },
};
async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json", ...(opts.headers || {}) }, ...opts,
                                body: opts.body ? JSON.stringify(opts.body) : undefined });
  let data = null;
  try { data = await r.json(); } catch { /* non-JSON error */ }
  if (!r.ok) {
    const d = data && data.detail;
    throw new Error(Array.isArray(d) ? d.join("; ") : (d || `Request failed (${r.status})`));
  }
  return data;
}
function track(name, props) { api("/api/events", { method: "POST", body: { name, anonymous_user_id: anon, props: props || {} } }).catch(() => {}); }
function render(...nodes) {
  app.replaceChildren(...nodes.flat(Infinity).filter(n => n !== null && n !== undefined && n !== false));
  window.scrollTo(0, 0);
}
function errorBox(e) { return h("div", { class: "banner fail", role: "alert" }, "⚠ ", e.message || String(e)); }
function busy(btn, label) { btn.disabled = true; btn.replaceChildren(h("span", { class: "spinner", "aria-hidden": "true" }), " " + label); }

const STATUS = {
  PASS: ["pass", "✓", "PASS"], MIXED: ["mixed", "◐", "MIXED"], FAIL: ["fail", "✕", "FAIL"],
  SEALED: ["neutral", "🔒", "SEALED"], "N/A": ["neutral", "–", "N/A"], WARNING: ["fail", "⚠", "WARNING"],
  LOW: ["pass", "✓", "LOW"], MODERATE: ["mixed", "⚠", "MODERATE"], HIGH: ["fail", "⚠", "HIGH"],
  "DATA LIMITATION": ["warn", "⚠", "DATA LIMITATION"],
};
function chip(status) {
  const [cls, icon, text] = STATUS[status] || ["neutral", "•", status];
  return h("span", { class: "chip " + cls }, h("span", { "aria-hidden": "true" }, icon), text);
}
const EVIDENCE_CLASS = { "NO EVIDENCE OF HISTORICAL EDGE": "no", "WEAK / INCONCLUSIVE HISTORICAL EVIDENCE": "weak",
  "PROMISING BUT UNSTABLE HISTORICAL EVIDENCE": "promising", "RELATIVELY ROBUST HISTORICAL EVIDENCE": "robust" };

let DATASET = null;
async function dataset() {
  if (!DATASET) DATASET = await api("/api/dataset");
  return DATASET;
}
function dataBanners(ds) {
  const out = [];
  if (ds.synthetic) out.push(h("div", { class: "banner fail" }, h("strong", {}, "DEMO DATA — NOT REAL BACKTEST RESULTS. "), ds.label));
  if (ds.survivorship_biased) out.push(h("div", { class: "banner warn" }, h("strong", {}, "⚠ DATASET LIMITATION. "),
    "This universe may contain survivorship bias because historical membership is reconstructed using currently available " +
    "securities. Results may therefore overstate historical performance."));
  return out;
}
function demoBar(step) {
  if (!store.get("sa_demo")) return null;
  const names = ["Paste", "Interpret", "Confirm", "Backtest", "Results", "Break it", "Weaknesses", "Assessment"];
  return h("div", { class: "steps-bar", "aria-label": "Demo progress" },
    h("span", { class: "on" }, "DEMO"), names.map((n, i) => h("span", { class: i < step ? "done" : i === step ? "on" : "" }, `${i + 1}. ${n}`)));
}

// ------------------------------------------------------------------ router
const routes = [
  [/^#?\/?$/, landing], [/^#\/new$/, inputPage], [/^#\/demo$/, demoStart], [/^#\/confirm$/, confirmPage],
  [/^#\/results\/([\w-]+)$/, resultsPage], [/^#\/audit\/([\w-]+)$/, auditPage], [/^#\/report\/([\w-]+)$/, reportPage],
  [/^#\/family\/([\w-]+)$/, lineagePage], [/^#\/plan$/, planPage], [/^#\/plan\/([\w-]+)$/, planResultPage], [/^#\/sample$/, samplePage], [/^#\/submit$/, submitPage], [/^#\/admin$/, adminPage],
];
async function route() {
  const hash = location.hash || "#/";
  for (const [rx, fn] of routes) {
    const m = hash.match(rx);
    if (m) {
      try { await fn(...m.slice(1)); } catch (e) { render(h("h1", {}, "Something went wrong"), errorBox(e)); }
      return;
    }
  }
  render(h("h1", {}, "Not found"), h("p", {}, h("a", { href: "#/" }, "Home")));
}
window.addEventListener("hashchange", route);

// ------------------------------------------------------------------ landing
async function landing() {
  const ref = new URLSearchParams(location.search).get("ref");
  if (ref && !store.get("sa_ref")) { store.set("sa_ref", ref.slice(0, 60)); track("referral_source", { ref: ref.slice(0, 60) }); }
  const steps = [["DESCRIBE", "Explain your strategy normally."], ["TEST", "Run it against historical data."],
                 ["BREAK", "Stress-test the result."], ["VERIFY", "Receive a transparent research report."]];
  render(
    h("section", { class: "hero" },
      h("div", { class: "eyebrow" }, "Strategy validation & audit"),
      h("h1", {}, "Think your trading strategy works?", h("br"), "Try to prove it doesn't."),
      h("p", { class: "lead" }, "Describe your trading idea in plain English. We'll backtest it and stress-test the result for " +
        "overfitting, transaction costs, market regimes and other common backtesting traps."),
      h("div", { class: "btn-row", style: "margin-top:26px" },
        h("a", { class: "btn primary big", href: "#/new" }, "TEST MY STRATEGY"),
        h("a", { class: "btn big", href: "#/sample" }, "VIEW SAMPLE AUDIT")),
      h("p", { class: "small", style: "margin-top:14px" }, "Investing monthly for the long term instead of trading? ",
        h("a", { href: "#/plan" }, "Test an investment plan →"))),
    h("section", { class: "grid g4 steps", style: "margin-top:34px" },
      steps.map(([k, t]) => h("div", { class: "card" }, h("div", { class: "k" }, k), h("div", { style: "margin-top:6px" }, t)))),
    h("section", { class: "grid g2", style: "margin-top:34px" },
      h("div", { class: "card" }, h("h3", {}, "What it checks"),
        h("ul", {}, ["Out-of-sample validation (sealed until you reveal it)", "Parameter sensitivity", "Transaction-cost stress",
          "Bull vs bear market regimes", "Year-by-year and outlier dependence", "Stock concentration",
          "Random-entry baseline in the same stocks", "Bootstrap confidence intervals", "Automated lookahead audit",
          "Multiple-testing lineage"].map(x => h("li", {}, x)))),
      h("div", { class: "card" }, h("h3", {}, "How it stays honest"),
        h("ul", {}, ["Plain English becomes explicit, validated rules. You confirm them before anything runs.",
          "Every number comes from a deterministic Python engine. The text only explains those numbers.",
          "Nothing ambiguous is silently assumed: you choose.",
          "Results are labelled as historical research, never recommendations."].map(x => h("li", {}, x))))),
    h("p", { class: "disclaimer" }, "Historical research only. Backtests do not predict future performance and are not investment recommendations."),
    h("div", { id: "ds" }));
  const ds = await dataset();
  document.getElementById("ds").append(h("p", { class: "small muted" },
    `Data: ${ds.label} · ${ds.n_symbols} US stocks · ${ds.test_start} → ${ds.last_bar} · development to ${ds.dev_end}, ` +
    `validation sealed from ${ds.validation_start}.`), ...dataBanners(ds));
}

// ------------------------------------------------------------------ input
async function demoStart() {
  store.set("sa_demo", true);
  const { text } = await api("/api/demo-text");
  store.set("sa_text", text);
  location.hash = "#/new";
}
async function inputPage() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const family = store.get("sa_edit_family");
  const ta = h("textarea", { id: "strategy-text", maxlength: "2000", "aria-label": "Describe your strategy",
    placeholder: "Buy S&P 500 stocks when RSI(14) falls below 30,\nthe stock remains above its 200-day moving average,\n" +
      "and it has fallen at least 5% over five trading days.\n\nEnter at the next day's open and hold for 10 trading days." });
  ta.value = store.get("sa_text") || "";
  const msg = h("div");
  const btn = h("button", { class: "btn primary big", onclick: go }, "INTERPRET STRATEGY");
  async function go() {
    if (!ta.value.trim()) { msg.replaceChildren(errorBox(new Error("Describe your strategy first."))); return; }
    busy(btn, "Interpreting…");
    try {
      store.set("sa_text", ta.value);
      const res = await api("/api/interpret", { method: "POST", body: { text: ta.value, anonymous_user_id: anon } });
      store.set("sa_interp", { result: res, choices: {}, name: (family && store.get("sa_edit_name")) ||
        (res.draft && res.draft.entry_conditions && res.draft.entry_conditions.length ? res.draft.name : "") });
      location.hash = "#/confirm";
    } catch (e) { msg.replaceChildren(errorBox(e)); btn.disabled = false; btn.textContent = "INTERPRET STRATEGY"; }
  }
  render(
    demoBar(0),
    h("h1", {}, family ? "Edit your strategy" : "Describe your strategy"),
    family ? h("div", { class: "banner info" }, "This creates a NEW VERSION in the same strategy family. Every version you test is " +
      "recorded, because testing many variants on the same data increases the risk of overfitting.") : null,
    h("p", { class: "lead" }, "Write it the way you'd explain it to a friend. We'll turn it into explicit rules and show you exactly " +
      "how we understood it before anything runs."),
    ta,
    h("div", { class: "btn-row", style: "margin-top:14px" }, btn,
      h("button", { class: "btn", onclick: async () => { ta.value = (await api("/api/demo-text")).text; } }, "Use the example"),
      family ? h("button", { class: "btn", onclick: () => { store.set("sa_edit_family", null); inputPage(); } }, "Start a new family instead") : null),
    msg,
    h("details", { class: "sec", style: "margin-top:26px" }, h("summary", {}, "What can I describe in V0?"),
      h("div", { class: "body" }, h("ul", {}, [
        "US large-cap stocks, daily candles, long only.",
        "RSI, simple/exponential moving averages, N-day returns, relative volume, N-day highs/lows, strength vs SPY, SPY trend filters, price levels.",
        "Entry at the next trading day's open. Exit after a fixed number of trading days, optionally with a stop-loss / take-profit.",
        "Not supported: short selling, options, crypto, forex, futures, intraday, leverage, fundamentals/news."].map(x => h("li", {}, x))))));
  if (params.get("focus") !== "0") ta.focus();
}

// ------------------------------------------------------------------ confirm
async function confirmPage() {
  const st = store.get("sa_interp");
  if (!st) { location.hash = "#/new"; return; }
  // Re-read the text on every load so a reload (or a parser update) never shows a stale interpretation.
  const text = store.get("sa_text");
  if (text) {
    try {
      const fresh = await api("/api/interpret", { method: "POST", body: { text, anonymous_user_id: anon } });
      if (JSON.stringify(fresh) !== JSON.stringify(st.result)) {
        st.result = fresh; st.choices = {};
        if (!st.name && fresh.draft && fresh.draft.entry_conditions && fresh.draft.entry_conditions.length) st.name = fresh.draft.name;
        store.set("sa_interp", st);
      }
    } catch { /* server unreachable: fall back to the saved interpretation */ }
  }
  const res = st.result;
  const planHint = /\b(dca|dollar[- ]cost|every month|each month|monthly|long[- ]term|never sell|havent (really )?sold)\b/i.test(text || "")
    ? h("div", { class: "banner info" }, h("strong", {}, "Sounds like an investment plan? "),
        "This page tests trades with a fixed holding period. For monthly investing (with or without buying extra on dips), use ",
        h("a", { href: "#/plan" }, "the investment plan tester"), ".") : null;
  const summary = h("div");
  const errs = h("div");
  const nameIn = h("input", { value: st.name || "", maxlength: "80", "aria-label": "Strategy name" });
  const confirmBtn = h("button", { class: "btn primary big", disabled: true, onclick: confirmGo }, "CONFIRM & BACKTEST");
  let resolved = null;
  const blocking = res.issues.filter(i => i.kind !== "unsupported");
  const unsupported = res.issues.filter(i => i.kind === "unsupported");

  function issueCard(iss) {
    const icon = { ambiguous: "⚠️", missing: "❓", not_understood: "❔", unsupported: "⛔", invalid: "⚠️" }[iss.kind] || "⚠️";
    return h("div", { class: "issue " + iss.kind },
      h("div", { class: "q" }, icon + " ", iss.phrase ? [h("span", { class: "phrase" }, iss.phrase), " "] : null, iss.message),
      iss.options.map((o, idx) => h("label", {},
        h("input", { type: "radio", name: iss.id, checked: st.choices[iss.id] === idx,
          onchange: () => { st.choices[iss.id] = idx; store.set("sa_interp", st); refresh(); } }), o.label)));
  }
  async function refresh() {
    const done = blocking.every(i => st.choices[i.id] !== undefined || i.options.length === 0);
    errs.replaceChildren();
    confirmBtn.disabled = true;
    if (unsupported.length) { summary.replaceChildren(); return; }
    const hasRule = (res.draft && res.draft.entry_conditions && res.draft.entry_conditions.length) ||
      blocking.some(i => i.options.some(o => o.patch.some(p => p.op === "add_condition")));
    if (!hasRule) {                                    // nothing testable was recognised: ask for a rephrase, no raw errors
      summarize(null);
      errs.append(h("div", { class: "banner fail", role: "alert" },
        h("strong", {}, "We couldn't find a rule we can test in your description. "),
        "Nothing will run. Please rephrase using rules like: ",
        h("span", { class: "mono" }, "RSI(14) below 30"), ", ", h("span", { class: "mono" }, "above its 50-day simple moving average"), ", ",
        h("span", { class: "mono" }, "fallen 5% over 5 days"), ", ", h("span", { class: "mono" }, "volume 2x its 20-day average"), ", ",
        h("span", { class: "mono" }, "new 20-day high"), ". Then add how long to hold, e.g. ",
        h("span", { class: "mono" }, "hold 10 days"), "."));
      return;
    }
    if (!done) { summarize(null); return; }
    const patches = blocking.flatMap(i => i.options.length ? i.options[st.choices[i.id]].patch : []);
    try {
      resolved = await api("/api/resolve", { method: "POST", body: { draft: res.draft, patches, name: nameIn.value || null } });
      if (resolved.spec) { confirmBtn.disabled = false; summarize(resolved); }
      else {
        summarize(null);
        const friendly = resolved.errors.map(e => e.startsWith("entry_conditions:") && e.includes("at least 1") ?
          "no entry rule was recognised" : e.startsWith("exit") ? "no holding period was set" : e);
        errs.append(errorBox(new Error("These rules can't run yet: " + friendly.join("; ") + ". Use EDIT to rephrase.")));
      }
    } catch (e) { errs.append(errorBox(e)); }
  }
  function summarize(r) {
    const s = r && r.spec;
    const ds = DATASET;
    const row = (k, ...v) => [h("dt", {}, k), h("dd", {}, ...v)];
    summary.replaceChildren(h("div", { class: "card" }, h("dl", { class: "understood" },
      row("UNIVERSE", s ? `${ds.n_symbols} large-cap US stocks (current dataset)` : h("span", { class: "muted" }, "needs your choice above")),
      row("DIRECTION", "Long"),
      row("ENTRY", s ? r.rules.map(x => h("div", { class: "ok" }, x)) :
        res.matched.filter(m => !/open|hold|large|stop|profit/i.test(m.meaning)).map(m => h("div", { class: "ok" }, m.meaning))),
      row("EXECUTION", "Next trading session's open (a signal is only known at the day's close)"),
      row("EXIT", s ? r.exit_text : (res.draft && res.draft.exit && res.draft.exit.trading_days) ?
        `Hold ${res.draft.exit.trading_days} trading sessions` : h("span", { class: "muted" }, "needs your choice above")),
      row("BENCHMARK", "SPY (buy and hold, same period)"),
      row("COSTS & SIZING", s ? `$${s.costs.platform_fee_per_order + s.costs.commission_per_order} per order + ${s.costs.slippage_bps} bps slippage per side; ` +
        `$${s.portfolio.starting_capital.toLocaleString()} split across up to ${s.portfolio.max_positions} equal positions` : "defaults"))));
  }
  async function confirmGo() {
    busy(confirmBtn, "Running backtest…");
    try {
      const fam = store.get("sa_edit_family");
      const spec = { ...resolved.spec, name: nameIn.value || resolved.spec.name };
      const v = await api("/api/versions", { method: "POST", body: { spec, family_id: fam, anonymous_user_id: anon } });
      store.set("sa_edit_family", null);
      const bt = await api("/api/backtests", { method: "POST", body: { version_id: v.version_id } });
      store.set("sa_last", { version: v, text: store.get("sa_text") });
      location.hash = "#/results/" + bt.backtest_id;
    } catch (e) { errs.replaceChildren(errorBox(e)); confirmBtn.disabled = false; confirmBtn.textContent = "CONFIRM & BACKTEST"; }
  }
  await dataset();
  render(
    demoBar(2),
    h("h1", {}, "Here's how we understood your strategy"),
    h("p", { class: "muted" }, "Nothing runs until you confirm. Anything unclear is highlighted below, and we will not guess."),
    planHint,
    unsupported.length ? h("div", {},
      h("div", { class: "banner fail" }, h("strong", {}, "This strategy can't be tested in V0. "),
        "Unsupported parts are rejected, not forced into the engine."),
      unsupported.map(issueCard)) : null,
    blocking.length ? h("h3", { style: "margin-top:22px" }, `${blocking.length} thing(s) need your decision`) : null,
    blocking.map(issueCard),
    res.matched.length ? h("details", { class: "sec" }, h("summary", {}, `Understood phrases (${res.matched.length})`),
      h("div", { class: "body" }, h("table", {}, h("tr", {}, h("th", {}, "Your words"), h("th", {}, "Became")),
        res.matched.map(m => h("tr", {}, h("td", { class: "mono" }, m.phrase), h("td", {}, m.meaning)))))) : null,
    h("h3", { style: "margin-top:22px" }, "Structured rules"),
    summary,
    h("label", { class: "f" }, "Strategy name"), nameIn,
    errs,
    h("div", { class: "btn-row", style: "margin-top:18px" },
      h("a", { class: "btn", href: "#/new" }, "EDIT"), unsupported.length ? null : confirmBtn),
    ...dataBanners(DATASET));
  nameIn.addEventListener("change", () => { st.name = nameIn.value; store.set("sa_interp", st); });
  refresh();
}

// ------------------------------------------------------------------ results
function kpi(label, value, sub) { return h("div", { class: "kpi" }, h("div", { class: "l" }, label), h("div", { class: "v" }, value), sub ? h("div", { class: "s" }, sub) : null); }
function equityCharts(bt) {
  const eq = h("div"), dd = h("div"), ann = h("div");
  const c = bt.charts;
  const box = h("div", { class: "grid" },
    h("div", { class: "card" }, h("h3", {}, "Equity curve: strategy vs SPY"), eq,
      h("div", { class: "legend" }, h("span", {}, h("i", { style: "background:var(--c-strat)" }), "Strategy"),
        h("span", {}, h("i", { style: "background:var(--c-spy)" }), "SPY buy & hold"))),
    h("div", { class: "grid g2" },
      h("div", { class: "card" }, h("h3", {}, "Drawdown"), dd),
      h("div", { class: "card" }, h("h3", {}, "Annual returns"), ann,
        h("div", { class: "legend" }, h("span", {}, h("i", { style: "background:var(--c-strat)" }), "Strategy"),
          h("span", {}, h("i", { style: "background:var(--c-spy)" }), "SPY")))));
  requestAnimationFrame(() => {
    Charts.line(eq, { dates: c.dates, fmt: v => "$" + Math.round(v / 1000) + "k", tipFmt: v => usd(v),
      series: [{ name: "SPY", values: c.spy, color: "--c-spy" }, { name: "Strategy", values: c.strategy, color: "--c-strat", width: 2.5 }] });
    Charts.area(dd, { dates: c.dates, values: c.drawdown, fmt: v => pctAbs(v) });
    Charts.bars(ann, { labels: c.annual.map(a => String(a.year)), fmt: v => pctAbs(v),
      series: [{ name: "Strategy", values: c.annual.map(a => a.strategy), color: "--c-strat" },
               { name: "SPY", values: c.annual.map(a => a.spy), color: "--c-spy" }] });
  });
  return box;
}
// 0 signals: show, rule by rule, how many stock-days survive, so the user can see which rule (or pair) never co-occurs.
function zeroSignalPanel(funnel) {
  if (!funnel || !funnel.length) return null;
  const killer = funnel.find(f => f.days_with_previous === 0);
  const fmt = (x) => x.toLocaleString();
  return h("div", { class: "banner warn" },
    h("strong", {}, "No trades: no day ever met all your rules at once. "),
    killer ? (killer.days_alone === 0
      ? `"${killer.rule}" never happened on its own in this period.`
      : `Everything works until "${killer.rule}". It happens on its own, but never on the same day as the rules above it. ` +
        "Rules that need opposite things (e.g. an oversold RSI and a new 20-day high) can't both be true.") : null,
    h("table", { class: "tbl", style: "margin-top:10px" },
      h("thead", {}, h("tr", {}, h("th", {}, "Rule (added in order)"), h("th", {}, "Stock-days true on its own"),
        h("th", {}, "Stock-days all rules so far are true"))),
      h("tbody", {}, funnel.map(f => h("tr", {}, h("td", {}, f.rule), h("td", {}, fmt(f.days_alone)),
        h("td", {}, fmt(f.days_with_previous)))))),
    h("div", { style: "margin-top:8px" }, "Tip: remove rules that pull in opposite directions, then run again."));
}

async function resultsPage(btId) {
  const [bt, ds] = await Promise.all([api("/api/backtests/" + btId), dataset()]);
  const s = bt.strategy, b = bt.benchmark, m = bt.meta, n = bt.counts;
  const breakBtn = h("button", { class: "btn break", onclick: runAudit }, "🔨 TRY TO BREAK MY STRATEGY");
  const msg = h("div");
  async function runAudit() {
    busy(breakBtn, "Running 12 checks: out-of-sample, parameters, costs, regimes, random entries… (about 10 seconds)");
    try {
      const au = await api("/api/audits", { method: "POST", body: { backtest_id: btId } });
      location.hash = "#/audit/" + au.audit_id;
    } catch (e) { msg.replaceChildren(errorBox(e)); breakBtn.disabled = false; breakBtn.textContent = "🔨 TRY TO BREAK MY STRATEGY"; }
  }
  render(
    demoBar(4),
    h("div", { class: "eyebrow" }, `Version ${bt.version_no} · ${m.period.toUpperCase()} PERIOD · ${m.start} → ${m.end}`),
    h("h1", {}, bt.name),
    h("div", { class: "muted" }, bt.rules.join("  ·  "), " · ", bt.exit),
    h("div", { class: "banner info" }, bt.validation_revealed ?
      "The validation period for this strategy family has already been revealed." :
      `🔒 Results below cover the development period only. The last 30% of the data (${ds.validation_start} → ${ds.last_bar}) ` +
      "is sealed for an out-of-sample test you can reveal once, after trying to break the strategy."),
    ...dataBanners(ds),
    zeroSignalPanel(bt.zero_signal_funnel),
    h("h2", {}, "Historical performance"),
    h("div", { class: "kpis" },
      kpi("Total return", pct(s.total_return), `SPY ${pct(b.total_return)}`), kpi("CAGR", pct(s.cagr), `SPY ${pct(b.cagr)}`),
      kpi("Max drawdown", pct(s.max_drawdown), `SPY ${pct(b.max_drawdown)}`), kpi("Sharpe", num(s.sharpe), `SPY ${num(b.sharpe)}`),
      kpi("Sortino", num(s.sortino), `SPY ${num(b.sortino)}`),
      kpi("Trades", s.trades ?? 0, `${n.signals} signals · ${n.skipped_portfolio_capacity} skipped (portfolio full)`),
      kpi("Win rate", pctAbs(s.win_rate)), kpi("Average trade", pct(s.avg_trade, 2), `median ${pct(s.median_trade, 2)}`),
      kpi("Avg winner / loser", `${pct(s.avg_winner)} / ${pct(s.avg_loser)}`), kpi("Profit factor", num(s.profit_factor)),
      kpi("Avg holding", num(s.avg_holding_sessions, 1) + " sessions"), kpi("Exposure", pctAbs(s.exposure), "average share of capital invested"),
      kpi("Estimated costs", usd(s.estimated_costs_usd), pct(s.estimated_costs_pct_of_capital) + " of starting capital"),
      kpi("Starting capital", usd(m.settings.portfolio.starting_capital), `max ${m.settings.portfolio.max_positions} positions`),
      kpi("Universe", m.universe_size + " stocks", "survivorship-biased")),
    h("h2", {}, "Charts"),
    equityCharts(bt),
    h("div", { class: "card", style: "margin-top:28px;text-align:center;padding:34px" },
      h("h2", { style: "margin-top:0" }, "Looks good? Now try to break it."),
      h("p", { class: "muted" }, "Most backtests look better than reality. The audit looks for evidence AGAINST this result."),
      breakBtn, msg),
    h("div", { class: "btn-row", style: "margin-top:18px" },
      h("button", { class: "btn", onclick: () => { store.set("sa_edit_family", bt.family_id); store.set("sa_edit_name", bt.name); location.hash = "#/new"; } }, "Edit & re-test (new version)"),
      h("a", { class: "btn", href: "#/family/" + bt.family_id }, "Research lineage")),
    h("p", { class: "small muted", style: "margin-top:22px" },
      `Engine ${m.engine_version} · methodology ${m.methodology_version} · dataset ${m.dataset_version} · spec ${m.spec_hash} · ${m.timestamp_utc}`));
}

// ------------------------------------------------------------------ audit
function tbl(headers, rows) {
  return h("table", {}, h("tr", {}, headers.map(x => h("th", { class: x.n ? "n" : null }, x.t ?? x))),
    rows.map(r => h("tr", {}, r.map((c, i) => h("td", { class: headers[i].n ? "n" : null }, c)))));
}
const H = (t) => ({ t, n: true });
function heatmap(sens) {
  if (!sens.grid || !sens.grid.length) return null;
  const gm = sens.grid_meta, max = Math.max(...sens.grid.map(g => Math.abs(g.mean || 0)), 1e-6);
  const cell = (t, hd) => sens.grid.find(g => g.threshold === t && g.hold === hd);
  const orig = sens.variants[0];
  return h("div", { style: "margin-top:14px" }, h("h3", {}, `Heatmap: ${gm.threshold_label} threshold × holding days (average trade after costs)`),
    h("table", { class: "heat" }, h("tr", {}, h("th", {}, "threshold \\ hold"), gm.holds.map(x => h("th", {}, x + " days"))),
      gm.thresholds.map(t => h("tr", {}, h("th", {}, String(t)), gm.holds.map(hd => {
        const g = cell(t, hd);
        if (!g || g.mean === null) return h("td", {}, "n/a");
        const a = Math.min(1, Math.abs(g.mean) / max) * 0.55 + 0.08;
        const col = g.mean > 0 ? `rgba(22,101,52,${a})` : `rgba(153,27,27,${a})`;
        const isOrig = g.n === orig.n && Math.abs((g.mean || 0) - (orig.mean || 0)) < 1e-12;
        return h("td", { class: isOrig ? "orig" : null, style: `background:${col}`, title: `${g.n} trades` }, pct(g.mean, 2), h("div", { class: "small" }, `${g.n} tr`));
      })))),
    h("p", { class: "small muted" }, "Outlined cell = your original parameters. Green = positive, red = negative. This is a stability check, not a search for the best cell."));
}
async function auditPage(auId) {
  const au = await api("/api/audits/" + auId);
  const bt = await api("/api/backtests/" + au.backtest_id);
  const t = au.tests, ev = au.evidence;
  const revealBox = h("div");
  function revealUI() {
    if (au.validation_revealed) {
      const o = t.oos, vp = o.validation_portfolio;
      revealBox.replaceChildren(h("div", { class: "card" }, h("h3", {}, "Sealed validation: revealed"),
        o.contaminated ? h("div", { class: "banner warn" }, "Validation was revealed for an earlier version of this family: this is no longer an unseen test.") : null,
        tbl(["", H("Trades"), H("Average trade"), H("95% CI"), H("Win rate")],
          [["Development", o.development.n, pct(o.development.mean, 2), `${pct(o.development.ci_low, 2)} … ${pct(o.development.ci_high, 2)}`, pctAbs(o.development.win_rate)],
           ["Validation (unseen)", o.validation.n, pct(o.validation.mean, 2), `${pct(o.validation.ci_low, 2)} … ${pct(o.validation.ci_high, 2)}`, pctAbs(o.validation.win_rate)]]),
        h("p", {}, `Degradation of the average trade: ${o.degradation === null ? "n/a" : pctAbs(o.degradation)}. `,
          vp ? `Validation-period portfolio: ${pct(vp.strategy.total_return)} vs SPY ${pct(vp.benchmark.total_return)} (${vp.meta.start} → ${vp.meta.end}).` : "")));
      return;
    }
    const btn = h("button", { class: "btn primary", onclick: modal }, "REVEAL VALIDATION");
    revealBox.replaceChildren(h("div", { class: "sealed" }, h("h3", {}, "🔒 Sealed validation data"),
      h("p", {}, "The most recent 30% of the history has not been used. Revealing it runs your exact rules, unchanged, on data this strategy family has never been measured against."),
      btn));
  }
  function modal() {
    const bg = h("div", { class: "modal-bg", role: "dialog", "aria-modal": "true" });
    const go = h("button", { class: "btn primary", onclick: async () => {
      busy(go, "Revealing…");
      try {
        const r = await api(`/api/families/${au.family_id}/reveal`, { method: "POST", body: { version_id: au.version_id, confirm: true } });
        bg.remove(); location.hash = "#/audit/" + r.audit.audit_id;
      } catch (e) { bg.querySelector(".modal").append(errorBox(e)); go.disabled = false; go.textContent = "Reveal now"; }
    } }, "Reveal now");
    bg.append(h("div", { class: "modal" }, h("h3", {}, "Reveal the validation period?"),
      h("p", {}, h("strong", {}, "This is a one-time validation test for this strategy family. "),
        "Once revealed, this period can no longer be considered unseen within this research history."),
      h("p", { class: "small muted" }, "Sealed data does NOT eliminate hindsight: you may already know how these years went. " +
        "It only prevents repeated measurement against the held-back sample inside this platform."),
      h("div", { class: "btn-row" }, go, h("button", { class: "btn", onclick: () => bg.remove() }, "Cancel"))));
    document.body.append(bg);
    go.focus();
  }
  revealUI();
  const r = t.random, sens = t.sensitivity, mt = t.multiple_testing;
  render(
    demoBar(au.validation_revealed ? 7 : 6),
    h("div", { class: "eyebrow" }, `Version ${au.version_no} · audit ${au.audit_id}`),
    h("h1", {}, "Strategy Robustness Audit"),
    h("div", { class: "muted" }, bt.name, " · ", bt.rules.join("  ·  ")),
    h("div", { class: "evidence " + (EVIDENCE_CLASS[ev.level] || ""), style: "margin-top:22px" },
      h("div", { class: "eyebrow" }, "Evidence assessment (historical evidence only)"),
      h("div", { class: "lvl" }, ev.level),
      h("p", { style: "margin:0" }, "Because " + ev.reasons.join("; ") + ".")),
    h("div", { class: "card", style: "margin-top:18px;padding:8px 14px" },
      h("table", { class: "audit-table" }, au.table.map(row => h("tr", {}, h("td", {}, row.label), h("td", {}, chip(row.status)), h("td", {}, row.summary))))),
    h("h2", {}, "What the numbers say"),
    h("ul", { class: "explain" }, au.explanation.map(x => h("li", {}, x.text))),
    h("p", { class: "small muted" }, "Every figure above is copied from the deterministic engine's output; no number is generated by AI."),
    h("h2", {}, "Out-of-sample"), revealBox,
    h("h2", {}, "Test details"),
    sec("Parameter sensitivity", sens.status, h("div", {}, h("p", {}, sens.summary),
      tbl(["Parameter", "Value", H("Trades"), H("Average trade")], sens.variants.map(v => [v.parameter, v.value, v.n, pct(v.mean, 2)])), heatmap(sens))),
    sec("Transaction-cost stress", t.costs.status, h("div", {}, h("p", {}, t.costs.summary),
      tbl(["Scenario", H("Gross expectancy"), H("Net expectancy"), H("Degradation")],
        t.costs.scenarios.map(x => [x.scenario, pct(x.gross_expectancy, 2), pct(x.net_expectancy, 2), x.degradation === null ? "n/a" : pctAbs(x.degradation)])))),
    sec("Market regimes", t.regimes.status, h("div", {}, h("p", {}, t.regimes.summary),
      tbl(["Regime", H("Trades"), H("Average trade"), H("Share of profit")],
        t.regimes.regimes.map(x => [x.label, x.n, pct(x.mean, 2), x.share_of_total === null ? "n/a" : pctAbs(x.share_of_total)])))),
    sec("Year-by-year", t.years.status, h("div", {}, h("p", {}, t.years.summary),
      tbl(["Year", H("Trades"), H("Average trade"), H("Sum of trade returns")], t.years.years.map(x => [x.year, x.n, pct(x.mean, 2), pct(x.sum)])))),
    sec("Outlier dependence", t.outliers.status, h("div", {}, h("p", {}, t.outliers.summary, ` Dependence: ${t.outliers.dependence}.`),
      tbl(["Variant", H("Trades"), H("Average trade")], t.outliers.variants.map(x => [x.variant, x.n, pct(x.mean, 2)])))),
    sec("Stock concentration", t.concentration.status, h("div", {}, h("p", {}, t.concentration.summary),
      t.concentration.top && t.concentration.top.length ? [
        tbl(["Ticker", H("Trades"), H("Share of profit")], t.concentration.top.map(x => [x.ticker, x.trades, pctAbs(x.share)])),
        h("p", {}, `Top stock ${pctAbs(t.concentration.top1_share)}, top 3 ${pctAbs(t.concentration.top3_share)}, top 5 ${pctAbs(t.concentration.top5_share)} of profit. ` +
          `Without ${t.concentration.best_ticker}: average trade ${pct(t.concentration.mean_without_best_ticker, 2)}.`)] : null)),
    sec("Random-entry baseline", r.status, h("div", {}, h("p", {}, r.summary),
      r.difference ? tbl(["", H("Average trade")], [["Strategy", pct(r.strategy_mean, 2)], ["Random entries (same stocks)", pct(r.random_mean, 2)],
        ["Difference", `${pct(r.difference.mean, 2)}  (95% CI ${pct(r.difference.ci_low, 2)} … ${pct(r.difference.ci_high, 2)})`]]) : null,
      r.matched_on ? h("p", { class: "small muted" }, `Matched on: ${r.matched_on.join(", ")}. ${r.samples_per_trade} samples per trade, seed ${r.seed}.`) : null)),
    sec("Statistical confidence (bootstrap)", t.stats.status, h("div", {}, h("p", {}, t.stats.summary),
      tbl(["Sample size", H("Mean trade"), H("95% CI"), H("Win rate")], [[t.stats.n + (t.stats.small_sample ? " (SMALL)" : ""), pct(t.stats.mean, 2),
        `${pct(t.stats.ci_low, 2)} … ${pct(t.stats.ci_high, 2)}`, pctAbs(t.stats.win_rate)]]),
      h("p", { class: "small muted" }, "Bootstrap resamples trading DATES (trades on the same day move together), so intervals are honest about clustering. A confidence interval is not certainty."))),
    sec("Lookahead audit", t.lookahead.status, h("div", {}, h("p", {}, t.lookahead.summary),
      h("ul", {}, t.lookahead.checks.map(c => h("li", {}, (c.passed ? "✓ " : "✕ ") + c.check + " — ", h("span", { class: "muted" }, c.detail)))))),
    sec("Multiple testing", mt.status, h("div", {},
      mt.versions > 1 ? h("div", { class: "banner warn" }, h("strong", {}, "⚠️ MULTIPLE-TESTING RISK. "), mt.summary) : h("p", {}, mt.summary),
      h("p", { class: "small muted" }, mt.note), h("a", { href: "#/family/" + au.family_id }, "View research lineage"))),
    sec("Survivorship bias", t.survivorship.status, h("p", {}, t.survivorship.summary)),
    h("div", { class: "btn-row no-print", style: "margin-top:24px" },
      h("a", { class: "btn primary", href: "#/report/" + au.audit_id }, "View shareable report"),
      h("button", { class: "btn", onclick: () => { store.set("sa_edit_family", au.family_id); store.set("sa_edit_name", bt.name); location.hash = "#/new"; } }, "Edit & re-test (new version)"),
      h("a", { class: "btn", href: "#/family/" + au.family_id }, "Research lineage")),
    h("h2", {}, "Known limitations"),
    h("ul", {}, LIMITS.map(x => h("li", {}, x))));
}
function sec(title, status, body) {
  return h("details", { class: "sec" }, h("summary", {}, h("span", {}, title), chip(status)), h("div", { class: "body" }, body));
}
const LIMITS = ["Historical performance does not predict future returns.", "Backtests are sensitive to data quality and assumptions.",
  "Out-of-sample testing reduces but does not eliminate hindsight bias.", "Research lineage cannot perfectly detect related strategies across users/accounts.",
  "Current datasets may contain survivorship bias.", "Transaction-cost estimates may differ from real execution.",
  "This platform provides historical research tools, not investment recommendations."];

// ------------------------------------------------------------------ report
async function reportPage(auId) {
  const r = await api("/api/reports/" + auId);
  const au = r.audit, bt = r.backtest, s = bt.strategy, b = bt.benchmark, ds = r.dataset, sp = r.strategy.spec, lin = r.lineage;
  const o = au.tests.oos;
  render(h("article", { class: "report" },
    h("div", { class: "eyebrow" }, "Strategy audit · historical validation report"),
    h("h1", { style: "font-size:36px" }, sp.name),
    h("p", { class: "muted" }, `Version ${r.strategy.version_no} · generated ${au.created_at} · audit ${au.audit_id}`),
    h("div", { class: "btn-row no-print" }, h("button", { class: "btn", onclick: () => window.print() }, "Print / save as PDF"),
      h("button", { class: "btn", onclick: () => { navigator.clipboard && navigator.clipboard.writeText(location.href); } }, "Copy link"),
      h("span", { class: "small muted" }, "Link works on this computer only (local prototype).")),
    h("div", { class: "evidence " + (EVIDENCE_CLASS[au.evidence.level] || ""), style: "margin-top:18px" },
      h("div", { class: "eyebrow" }, "Evidence assessment"), h("div", { class: "lvl" }, au.evidence.level),
      h("p", { style: "margin:0" }, "Because " + au.evidence.reasons.join("; ") + "."),
      h("p", { class: "small muted", style: "margin:8px 0 0" }, "This assesses historical evidence only. It is not a recommendation to trade.")),
    h("h2", {}, "Strategy definition"),
    tbl(["Component", "Rule"], [["Universe", `${ds.n_symbols} large-cap US stocks (current dataset)`], ["Direction", "Long only"],
      ...r.strategy.rules.map((x, i) => [i === 0 ? "Entry (all must hold)" : "", x]), ["Execution", "Next trading session's open"],
      ["Exit", r.strategy.exit], ["Benchmark", "SPY buy & hold"],
      ["Costs", `$${sp.costs.platform_fee_per_order + sp.costs.commission_per_order}/order, ${sp.costs.slippage_bps} bps slippage/side, ${sp.costs.regulatory_fee_bps_on_sells} bps on sells`],
      ["Sizing", `$${sp.portfolio.starting_capital.toLocaleString()}, up to ${sp.portfolio.max_positions} equal positions`]]),
    h("h2", {}, "Test period & data assumptions"),
    tbl(["Item", "Value"], [["Data", ds.label + (ds.synthetic ? " (SYNTHETIC)" : "")], ["Dataset version", h("span", { class: "mono" }, ds.dataset_version)],
      ["Development period", `${ds.test_start} → ${ds.dev_end}`],
      ["Validation period", `${ds.validation_start} → ${ds.last_bar} · ${au.validation_revealed ? "REVEALED" : "SEALED (not viewed)"}`],
      ["Survivorship", ds.survivorship_biased ? "Biased: today's large caps only, no delisted securities" : "Point-in-time"],
      ["Data rights", ds.license_status]]),
    ...dataBanners(ds),
    h("h2", {}, "Performance (development period)"),
    tbl(["Metric", H("Strategy"), H("SPY")], [["Total return", pct(s.total_return), pct(b.total_return)], ["CAGR", pct(s.cagr), pct(b.cagr)],
      ["Max drawdown", pct(s.max_drawdown), pct(b.max_drawdown)], ["Sharpe", num(s.sharpe), num(b.sharpe)], ["Sortino", num(s.sortino), num(b.sortino)],
      ["Trades", s.trades, ""], ["Win rate", pctAbs(s.win_rate), ""], ["Average trade (after costs)", pct(s.avg_trade, 2), ""],
      ["Profit factor", num(s.profit_factor), ""], ["Exposure", pctAbs(s.exposure), "100%"], ["Estimated costs", usd(s.estimated_costs_usd), ""]]),
    equityCharts(bt),
    o.validation ? [h("h2", {}, "Out-of-sample validation"),
      tbl(["", H("Trades"), H("Average trade"), H("95% CI")], [["Development", o.development.n, pct(o.development.mean, 2), `${pct(o.development.ci_low, 2)} … ${pct(o.development.ci_high, 2)}`],
        ["Validation", o.validation.n, pct(o.validation.mean, 2), `${pct(o.validation.ci_low, 2)} … ${pct(o.validation.ci_high, 2)}`]])] : null,
    h("h2", {}, "Robustness audit"),
    h("table", { class: "audit-table" }, au.table.map(row => h("tr", {}, h("td", {}, row.label), h("td", {}, chip(row.status)), h("td", {}, row.summary)))),
    h("h2", {}, "Findings"), h("ul", { class: "explain" }, au.explanation.map(x => h("li", {}, x.text))),
    h("h2", {}, "Research lineage"),
    h("p", {}, `${lin.versions.length} version(s) in this strategy family; validation ${lin.validation_revealed ? "revealed " + lin.revealed_at : "still sealed"}. `,
      lin.multiple_testing.summary),
    h("h2", {}, "Known limitations"), h("ul", {}, r.limitations.map(x => h("li", {}, x))),
    h("h2", {}, "Methodology"),
    h("p", { class: "mono" }, `engine ${r.methodology.engine_version} · methodology ${r.methodology.methodology_version} · dataset ${r.methodology.dataset_version} · ` +
      `bootstrap ${r.methodology.bootstrap_resamples} resamples (clustered by date) · random baseline ${r.methodology.random_samples_per_trade}/trade · seed ${r.methodology.random_seed}`),
    h("p", { class: "disclaimer" }, "Historical research only. Backtests do not predict future performance and are not investment recommendations. " +
      "This report is not a certification of any kind.")));
}

// ------------------------------------------------------------------ lineage
async function lineagePage(fid) {
  const lin = await api("/api/families/" + fid);
  const mt = lin.multiple_testing;
  render(h("h1", {}, "Research lineage"),
    h("p", { class: "muted mono" }, "Family " + lin.family_id),
    mt.versions > 1 ? h("div", { class: "banner warn" }, h("strong", {}, "⚠️ MULTIPLE-TESTING RISK. "), mt.summary) : h("div", { class: "banner info" }, mt.summary),
    h("p", { class: "small muted" }, mt.note),
    h("p", {}, "Validation period: ", lin.validation_revealed ? h("strong", {}, `revealed ${lin.revealed_at}`) : h("strong", {}, "sealed (never viewed)")),
    h("div", { class: "card" }, tbl(["Version", "Created", "Name", "Parameters changed", "Results viewed", "Validation viewed", "Audits"],
      lin.versions.map(v => ["v" + v.version_no, v.created_at.replace("T", " ").slice(0, 16), v.name, h("ul", { style: "margin:0;padding-left:18px" }, v.changes.map(c => h("li", {}, c))),
        v.results_viewed ? "yes" : "no", v.validation_viewed ? "yes" : "no",
        v.audits.length ? v.audits.map(a => h("div", {}, h("a", { href: "#/audit/" + a.id }, a.evidence))) : "–"]))));
}

async function samplePage() {
  render(h("h1", {}, "Sample audit"), h("p", { class: "muted" }, h("span", { class: "spinner" }), " Preparing the sample audit from real data… (first time about 10 seconds)"));
  const r = await api("/api/sample-audit");
  location.replace("#/report/" + r.audit_id);
}

// ------------------------------------------------------------------ Stage-1 submission
async function submitPage() {
  const f = {};
  const field = (key, label, el) => { f[key] = el; return [h("label", { class: "f", for: "f-" + key }, label), Object.assign(el, { id: "f-" + key })]; };
  const out = h("div");
  const btn = h("button", { class: "btn primary big", onclick: send }, "Send for a manual audit");
  async function send() {
    const body = { anonymous_user_id: anon, strategy_name: f.strategy_name.value.trim(), description: f.description.value.trim(),
      market: f.market.value.trim(), holding_period: f.holding_period.value.trim(), entry_conditions: f.entry_conditions.value.trim(),
      exit_conditions: f.exit_conditions.value.trim(), traded_yn: f.traded_yn.value, notes: f.notes.value.trim(),
      belief_1_5: f.belief_1_5.value ? Number(f.belief_1_5.value) : null, referral_source: (store.get("sa_ref") || f.referral.value.trim()).slice(0, 120) };
    if (!body.strategy_name || !body.description) { out.replaceChildren(errorBox(new Error("Please give the strategy a name and a description."))); return; }
    busy(btn, "Sending…");
    try {
      const r = await api("/api/submissions", { method: "POST", body });
      render(h("h1", {}, "Thanks, received"), h("p", { class: "lead" }, "We'll audit it and send back a research report. Reference: ", h("span", { class: "mono" }, r.submission_id)),
        h("p", {}, h("a", { href: "#/new" }, "Or test a supported strategy yourself right now →")));
    } catch (e) { out.replaceChildren(errorBox(e)); btn.disabled = false; btn.textContent = "Send for a manual audit"; }
  }
  const sel = (opts) => h("select", {}, opts.map(([v, t]) => h("option", { value: v }, t)));
  render(h("h1", {}, "Request a manual audit"),
    h("p", { class: "lead" }, "Can't express your idea in the tester yet? Describe it and we'll audit it by hand. No account needed."),
    h("div", { class: "card", style: "max-width:720px" },
      field("strategy_name", "Strategy name *", h("input", { maxlength: "120" })),
      field("description", "Describe your strategy *", h("textarea", { maxlength: "4000" })),
      field("market", "Asset / market", h("input", { maxlength: "120", placeholder: "e.g. US large-cap stocks" })),
      field("holding_period", "Typical holding period", h("input", { maxlength: "120", placeholder: "e.g. 1–2 weeks" })),
      field("entry_conditions", "Entry conditions", h("textarea", { maxlength: "2000", style: "min-height:90px" })),
      field("exit_conditions", "Exit conditions", h("textarea", { maxlength: "2000", style: "min-height:90px" })),
      field("traded_yn", "Have you actually traded this?", sel([["", "—"], ["Y", "Yes"], ["N", "No"]])),
      field("belief_1_5", "How strongly do you believe it works? (1–5)", sel([["", "—"], ["1", "1 · not sure"], ["2", "2"], ["3", "3"], ["4", "4"], ["5", "5 · very sure"]])),
      field("referral", "How did you hear about this? (optional)", h("input", { maxlength: "120" })),
      field("notes", "Optional notes", h("textarea", { maxlength: "2000", style: "min-height:80px" })),
      h("p", { class: "small muted" }, "We store only what you type here plus a random anonymous browser ID. No email or name is required."),
      btn, out));
}

// ------------------------------------------------------------------ investment plans
const assetName = (code, opts) => code === opts.benchmark ? "SPY (S&P 500 ETF)" : code.replace(/^[A-Z]+\./, "");

async function planPage() {
  const opts = await api("/api/plan-options");
  const prev = store.get("sa_plan_spec") || {};
  const pd = prev.dip || {};
  const n = (attrs) => h("input", { type: "number", ...attrs });
  const asset = h("select", {}, [opts.benchmark, ...opts.assets].map(c =>
    h("option", { value: c, selected: c === (prev.asset || opts.benchmark) }, assetName(c, opts))));
  const monthly = n({ min: "10", max: "100000", step: "10", value: prev.monthly_amount ?? 500 });
  const start = h("input", { type: "month", min: opts.earliest_start, max: opts.last_bar.slice(0, 7),
    value: prev.start_month || opts.earliest_start });
  const useDip = h("input", { type: "checkbox", checked: prev.asset ? !!prev.dip : true, style: "width:auto" });
  const tierBox = h("div");
  let tiers = (pd.tiers || [{ drawdown: .10, deploy: 1 / 3 }, { drawdown: .15, deploy: .5 }, { drawdown: .20, deploy: 1 }])
    .map(t => ({ dd: Math.round(t.drawdown * 100), dep: Math.round(t.deploy * 100) }));
  const fundRes = h("input", { type: "radio", name: "fund", value: "RESERVE", checked: pd.funding !== "EXTRA", style: "width:auto" });
  const fundExtra = h("input", { type: "radio", name: "fund", value: "EXTRA", checked: pd.funding === "EXTRA", style: "width:auto" });
  const reserve = n({ min: "5", max: "90", step: "5", value: Math.round((pd.reserve_share ?? .25) * 100), style: "width:90px" });
  const extra = n({ min: "10", max: "1000000", step: "10", value: pd.extra_amount ?? 500, style: "width:120px" });
  const cashRate = n({ min: "0", max: "10", step: "0.5", value: ((pd.cash_rate ?? 0) * 100), style: "width:90px" });
  const fee = n({ min: "0", max: "20", step: "0.01", value: prev.fee_per_order ?? opts.defaults.fee_per_order });
  const slip = n({ min: "0", max: "100", step: "1", value: prev.slippage_bps ?? opts.defaults.slippage_bps });
  const dipBox = h("div", { class: "card", style: "margin-top:12px" });
  const msg = h("div");
  const btn = h("button", { class: "btn primary big", onclick: go }, "RUN PLAN");

  function drawTiers() {
    const reserveMode = fundRes.checked;
    tierBox.replaceChildren(...tiers.map((t, i) => {
      const dd = n({ min: "3", max: "60", step: "1", value: t.dd, style: "width:80px", "aria-label": `Dip level ${i + 1} drop %` });
      dd.addEventListener("input", () => t.dd = +dd.value);
      const dep = n({ min: "5", max: "100", step: "5", value: t.dep, style: "width:80px", "aria-label": `Dip level ${i + 1} share of cash` });
      dep.addEventListener("input", () => t.dep = +dep.value);
      return h("div", { class: "tier-row" },
        h("span", {}, "At"), dd, h("span", {}, "% below the 52-week high, spend"),
        reserveMode ? [dep, h("span", {}, "% of the saved cash")] : h("span", {}, "the extra amount"),
        tiers.length > 1 ? h("button", { class: "linkish", type: "button", "aria-label": `Remove dip level ${i + 1}`,
          onclick: () => { tiers.splice(i, 1); drawTiers(); } }, "✕ remove") : null);
    }), tiers.length < 5 ? h("button", { class: "btn", type: "button", style: "margin-top:10px",
      onclick: () => { tiers.push({ dd: (tiers.at(-1)?.dd || 10) + 10, dep: 100 }); drawTiers(); } }, "+ Add a dip level") : null);
  }
  function drawDip() {
    dipBox.style.display = useDip.checked ? "" : "none";
    dipBox.replaceChildren(
      h("h3", {}, "Where does the dip money come from?"),
      h("label", { class: "f", style: "display:flex;gap:10px;align-items:center;font-weight:400" }, fundRes,
        h("span", {}, "Hold back ", reserve, "% of every monthly amount as cash, and spend it on dips ",
          h("span", { class: "muted" }, "(same total money as plain investing: a fair head-to-head)"))),
      fundRes.checked ? h("div", { style: "margin:0 0 6px 34px" }, "Saved cash earns ", cashRate, "% a year while it waits") : null,
      h("label", { class: "f", style: "display:flex;gap:10px;align-items:center;font-weight:400" }, fundExtra,
        h("span", {}, "Add new money: $", extra, " at each dip buy ", h("span", { class: "muted" }, "(on top of the monthly amount)"))),
      h("h3", { style: "margin-top:16px" }, "Dip levels"),
      h("p", { class: "small muted" }, "Each level buys once, then waits until the price makes a new 52-week high before it can buy again."),
      tierBox);
    drawTiers();
  }
  [fundRes, fundExtra].forEach(r => r.addEventListener("change", drawDip));
  useDip.addEventListener("change", drawDip);

  async function go() {
    const spec = { asset: asset.value, monthly_amount: +monthly.value, start_month: start.value || null,
      fee_per_order: +fee.value, slippage_bps: +slip.value, dip: null };
    if (useDip.checked) {
      const funding = fundRes.checked ? "RESERVE" : "EXTRA";
      spec.dip = { funding, tiers: tiers.map(t => ({ drawdown: t.dd / 100, deploy: funding === "RESERVE" ? t.dep / 100 : 1 })) };
      if (funding === "RESERVE") { spec.dip.reserve_share = +reserve.value / 100; spec.dip.cash_rate = +cashRate.value / 100; }
      else spec.dip.extra_amount = +extra.value;
    }
    busy(btn, "Simulating every month…");
    try {
      const out = await api("/api/plans", { method: "POST", body: { spec, anonymous_user_id: anon } });
      store.set("sa_plan_spec", spec);
      location.hash = "#/plan/" + out.plan_id;
    } catch (e) { msg.replaceChildren(errorBox(e)); btn.disabled = false; btn.textContent = "RUN PLAN"; }
  }
  const lab = (t, el, hint) => h("div", {}, h("label", { class: "f" }, t), el, hint ? h("div", { class: "small muted", style: "margin-top:4px" }, hint) : null);
  render(
    h("div", { class: "eyebrow" }, "Investment plan"),
    h("h1", {}, "Test a monthly investing plan"),
    h("p", { class: "lead" }, "For long-term investing rather than trading: put in a fixed amount every month, optionally buy extra " +
      "after the market falls, and never sell. We compare it with plain monthly investing in the same asset."),
    h("div", { class: "card", style: "max-width:820px" },
      h("div", { class: "grid g2" },
        lab("What to buy", asset, "US data only: SPY or one of the 93 large US stocks."),
        lab("Monthly amount ($)", monthly),
        lab("Start month", start, `Earliest ${opts.earliest_start}: the 52-week high needs a year of history. Runs to ${opts.last_bar}.`)),
      h("label", { class: "f", style: "display:flex;gap:10px;align-items:center;margin-top:18px" }, useDip,
        "Buy extra after drops from the 52-week high"),
      dipBox,
      h("details", { class: "sec", style: "margin-top:14px" }, h("summary", {}, "Costs"),
        h("div", { class: "body grid g2" }, lab("Fee per order ($)", fee), lab("Slippage per buy (basis points)", slip))),
      h("div", { class: "btn-row", style: "margin-top:18px" }, btn), msg),
    h("p", { class: "disclaimer" }, "Historical research only, not investment advice. Past returns do not predict future returns."));
  drawDip();
}

async function planResultPage(planId) {
  const r = await api("/api/plans/" + planId);
  const opts = await api("/api/plan-options");
  const s = r.spec, P = r.plain, Q = r.plan, v = r.verdict;
  const eq = h("div");
  const money = (x) => usd(x);
  const cols = Q ? ["", "Plain monthly investing", "Your plan"] : ["", "Plain monthly investing"];
  const row = (label, f) => Q ? [label, f(P), f(Q)] : [label, f(P)];
  const rows = [
    row("Money put in", x => money(x.put_in)), row("Value at the end", x => money(x.final_value)),
    row("Profit", x => money(x.profit)), row("Yearly return on money put in", x => pct(x.irr, 2)),
    row("Largest fall in account value", x => pct(x.worst_drop)), row("Fees paid", x => money(x.fees)),
    ...(Q && s.dip.funding === "RESERVE" ? [row("Cash still waiting at the end", x => money(x.cash_end)),
      row("Average share of the account held in cash", x => pctAbs(x.avg_cash_share))] : [])];
  render(
    h("div", { class: "eyebrow" }, `Investment plan · ${assetName(s.asset, opts)} · ${P.start} → ${P.end} · ${usd(s.monthly_amount)} a month`),
    h("h1", {}, Q ? "Your plan vs plain monthly investing" : "Plain monthly investing"),
    r.synthetic_data ? h("div", { class: "banner fail" }, h("strong", {}, "DEMO DATA — NOT REAL MARKET DATA.")) : null,
    s.asset !== opts.benchmark && r.survivorship_biased ? h("div", { class: "banner warn" }, h("strong", {}, "⚠ DATASET LIMITATION. "),
      "This stock is in the dataset because it is a large company today. Picking today's winners makes past results look better.") : null,
    v ? h("div", { class: "evidence promising", style: "margin-top:16px" },
      h("div", { class: "eyebrow" }, "Result across start dates"), h("div", { class: "lvl" }, v.label),
      h("p", {}, `Started in each of ${v.n} different years, the plan earned a higher yearly return in ${v.ahead}, a lower one in ` +
        `${v.behind}, and about the same (within 0.1 points) in ${v.ties}.` +
        (v.median_irr_diff != null ? ` Typical difference: ${(v.median_irr_diff * 100).toFixed(2)} percentage points a year.` : "")),
      v.notes.length ? h("ul", {}, v.notes.map(x => h("li", {}, x))) : null) : null,
    h("div", { class: "card", style: "margin-top:18px" }, h("h3", {}, "Account value over time"), eq,
      h("div", { class: "legend" },
        Q ? h("span", {}, h("i", { class: "sw", style: "background:var(--c-strat)" }), "Your plan") : null,
        h("span", {}, h("i", { class: "sw", style: "background:var(--c-spy)" }), "Plain monthly investing"),
        h("span", {}, h("i", { class: "sw dash" }), "Money put in (the gap above it is profit)"))),
    h("div", { class: "card", style: "margin-top:18px" }, h("h3", {}, `Starting ${P.start}`), tbl(cols.map((c, i) => i ? H(c) : c), rows)),
    r.by_start_year.length ? h("div", { class: "card", style: "margin-top:18px" },
      h("h3", {}, "Does it depend on when you started?"),
      h("p", { class: "small muted" }, "The same comparison from each January, all running to the end of the data. " +
        "Yearly return = return on money put in, so plans that put in different amounts are compared fairly."),
      tbl(["Start", H("Plain: yearly return"), H("Your plan: yearly return"), H("Difference"), H("Plain: end value"),
           H("Your plan: end value"), H("Dip buys")],
        r.by_start_year.map(x => [x.start, pct(x.plain_irr, 2), pct(x.plan_irr, 2),
          x.plan_irr != null && x.plain_irr != null ? ((x.plan_irr - x.plain_irr) * 100).toFixed(2) + " pts" : "n/a",
          money(x.plain_value) + (x.plain_in !== x.plan_in ? ` (in ${money(x.plain_in)})` : ""),
          money(x.plan_value) + (x.plain_in !== x.plan_in ? ` (in ${money(x.plan_in)})` : ""), x.dip_buys]))) : null,
    Q ? h("div", { class: "card", style: "margin-top:18px" }, h("h3", {}, `Dip buys (${r.dip_buys.length})`),
      r.dip_buys.length ? tbl(["Signal (close)", "Bought (next open)", H("Below 52-week high"), H("Level"), H("Amount"), H("Price")],
        r.dip_buys.map(b => [b.signal_date, b.date, pctAbs(b.drawdown, 1), pctAbs(b.tier), money(b.amount), num(b.price)]))
        : h("p", {}, "No dip level was reached in this period.")) : null,
    r.variants_tried > 1 ? h("div", { class: "banner warn", style: "margin-top:18px" },
      h("strong", {}, `You've run ${r.variants_tried} plan variants on this data. `),
      "Choosing the best of many settings on the same history makes that setting look better than it is likely to be.") : null,
    h("details", { class: "sec", style: "margin-top:18px" }, h("summary", {}, "How this was calculated"),
      h("div", { class: "body" }, h("ul", {}, [
        "Monthly buy at the open of the first trading day of each month.",
        "52-week high = highest daily close of the last 252 trading days. A dip is checked at the close and bought at the next day's open, so nothing uses information from the future.",
        "Each dip level buys once, then re-arms only after a new 52-week high.",
        `Fees: ${usd(s.fee_per_order)} per order plus ${s.slippage_bps} basis points slippage. Fractional units. No taxes, no currency conversion.`,
        "Prices are adjusted for splits and dividends, so dividends are treated as reinvested (approximately).",
        "Yearly return on money put in is money-weighted (internal rate of return): it accounts for when each dollar went in.",
        "Largest fall in account value includes new money arriving, so it understates how far the asset itself fell.",
        "US data only. Singapore or Irish-domiciled funds are not in the dataset. One historical period, mostly a rising market.",
        `Dataset ${r.dataset_version} · engine ${r.engine_version}`].map(x => h("li", {}, x))))),
    h("div", { class: "btn-row", style: "margin-top:20px" },
      h("a", { class: "btn primary", href: "#/plan" }, "Change settings"),
      h("button", { class: "btn", onclick: (e) => { navigator.clipboard?.writeText(location.href); e.target.textContent = "Link copied"; } }, "Copy link")),
    h("p", { class: "disclaimer" }, "Historical research only, not investment advice. Past returns do not predict future returns."));
  requestAnimationFrame(() => {
    const c = r.chart, series = [];
    series.push({ name: "Plain", values: c.plain_value, color: "--c-spy" });
    if (c.plan_value) series.push({ name: "Your plan", values: c.plan_value, color: "--c-strat", width: 2.5 });
    series.push({ name: "Money put in", values: c.plan_in || c.plain_in, color: "--muted", width: 1.5, dash: true });
    Charts.line(eq, { dates: c.dates, fmt: v => "$" + Math.round(v / 1000) + "k", tipFmt: v => usd(v), series });
  });
}

// ------------------------------------------------------------------ admin
async function adminPage() {
  const tokIn = h("input", { type: "password", placeholder: "Admin token (server env ADMIN_TOKEN)", value: store.get("sa_admin") || "" });
  const box = h("div");
  async function load() {
    store.set("sa_admin", tokIn.value);
    const hd = { "X-Admin-Token": tokIn.value };
    try {
      const [subs, fun] = await Promise.all([api("/api/admin/submissions", { headers: hd }), api("/api/admin/funnel", { headers: hd })]);
      const rate = (x) => x === null ? "n/a" : pctAbs(x);
      box.replaceChildren(
        h("h2", {}, "Business validation (not strategy validation)"),
        h("div", { class: "kpis" }, kpi("Submissions", fun.submissions), kpi("% testable", rate(fun.rates.testable)), kpi("% report delivered", rate(fun.rates.report_delivered)),
          kpi("% returned unprompted", rate(fun.rates.returned_without_prompt)), kpi("% referred someone", rate(fun.rates.referred)), kpi("% willing to pay", rate(fun.rates.willing_to_pay))),
        h("div", { class: "card", style: "margin-top:14px" }, h("h3", {}, "Product funnel (anonymous events)"),
          tbl(["Event", H("Count"), H("Distinct browsers")], fun.events.map(e => [e.name, e.n, e.users]))),
        h("p", { class: "small muted" }, "Compliments are not demand: track returns, referrals and actual payment tests."),
        h("h2", {}, `Submissions (${subs.length})`), ...subs.map(s => submissionCard(s, hd)));
    } catch (e) { box.replaceChildren(errorBox(e)); }
  }
  render(h("h1", {}, "Admin · Stage-1 research"), h("div", { class: "btn-row" }, tokIn, h("button", { class: "btn", onclick: load }, "Load")), box);
  if (tokIn.value) load();
}
function submissionCard(s, hd) {
  const yn = (k, opts = [["", "—"], ["Y", "Y"], ["N", "N"]]) => { const e = h("select", { "aria-label": k }, opts.map(([v, t]) => h("option", { value: v }, t))); e.value = s[k] || ""; return e; };
  const txt = (k, type = "text") => h("input", { type, value: s[k] ?? "", "aria-label": k });
  const F = { strategy_type: txt("strategy_type"), testable_yn: yn("testable_yn"), reason_if_not_testable: txt("reason_if_not_testable"),
    audit_minutes: txt("audit_minutes", "number"), report_delivered: yn("report_delivered"), returned_without_prompt: yn("returned_without_prompt"),
    days_until_return: txt("days_until_return", "number"), referred_someone: yn("referred_someone"),
    willing_to_pay: yn("willing_to_pay", [["", "—"], ["Y", "Y"], ["N", "N"], ["MAYBE", "MAYBE"]]), actual_payment_test: txt("actual_payment_test"), admin_notes: txt("admin_notes") };
  const msg = h("span", { class: "small muted" });
  const save = h("button", { class: "btn", onclick: async () => {
    const body = {};
    for (const [k, e] of Object.entries(F)) if (e.value !== "") body[k] = (k === "audit_minutes" || k === "days_until_return") ? Number(e.value) : e.value;
    try { await api("/api/admin/submissions/" + s.id, { method: "PATCH", headers: hd, body }); msg.textContent = "saved"; } catch (e) { msg.textContent = e.message; }
  } }, "Save");
  return h("details", { class: "sec" }, h("summary", {}, h("span", {}, s.strategy_name, " ", h("span", { class: "small muted" }, s.created_at.slice(0, 10))),
    h("span", { class: "small muted mono" }, s.id)),
    h("div", { class: "body" }, h("p", {}, s.description), h("p", { class: "small muted" },
      `market: ${s.market || "–"} · holding: ${s.holding_period || "–"} · traded: ${s.traded_yn || "–"} · belief: ${s.belief_1_5 || "–"} · referral: ${s.referral_source || "–"} · anon: ${s.anonymous_user_id || "–"}`),
      s.entry_conditions ? h("p", {}, h("strong", {}, "Entry: "), s.entry_conditions) : null, s.exit_conditions ? h("p", {}, h("strong", {}, "Exit: "), s.exit_conditions) : null,
      s.notes ? h("p", {}, h("strong", {}, "Notes: "), s.notes) : null,
      h("div", { class: "grid g3" }, Object.entries(F).map(([k, e]) => h("div", {}, h("label", { class: "f" }, k.replaceAll("_", " ")), e))),
      h("div", { class: "btn-row", style: "margin-top:12px" }, save, msg)));
}

dataset().then(ds => { const el = document.getElementById("ds-foot"); if (el) el.textContent = ` Data: ${ds.label}.`; }).catch(() => {});
route();
