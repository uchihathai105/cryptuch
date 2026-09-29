/* Crypto Trading Advisor — dashboard. Talks only to the local app (never to Binance directly). */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  let S = null;            // last /api/state
  let list = "top_volume";
  let view = { symbol: null, market: "futures", tf: "1h" };
  let chart = null;

  // ------------------------------------------------------------ formatting
  function px(x) {
    if (x == null || !isFinite(x)) return "–";
    const a = Math.abs(x);
    if (a >= 1000) return x.toLocaleString("en-US", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
    if (a >= 100) return x.toFixed(2);
    if (a >= 1) return String(+x.toFixed(3));
    if (a === 0) return "0";
    const d = Math.max(4, -Math.floor(Math.log10(a)) + 3);
    return x.toFixed(d);
  }
  const pct = (x, d = 1) => (x == null || !isFinite(x) ? "–" : `${x > 0 ? "+" : ""}${x.toFixed(d)}%`);
  const usd = (x) => (x == null ? "–" : x.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 }));
  const qty = (x) => (x == null ? "–" : x >= 100 ? x.toLocaleString("en-US", { maximumFractionDigits: 1 }) : String(+x.toPrecision(4)));
  const cls = (x) => (x > 0 ? "up" : x < 0 ? "down" : "");
  const ago = (t) => {
    const m = Math.floor((Date.now() / 1000 - t) / 60);
    if (m < 1) return "just now";
    if (m < 60) return `${m} min ago`;
    return `${Math.floor(m / 60)} h ${m % 60} min ago`;
  };
  const clock = (t) => new Date(t * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  const dateTime = (ms) => new Date(ms).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

  const TONE = { Recovering: "good", Basing: "warn", Falling: "bad", Long: "good", Buy: "good", Accumulate: "good", Bull: "good", Hold: "", Short: "bad", Exit: "bad", Reduce: "bad", Bear: "bad", Neutral: "", Wait: "", New: "" };
  function pill(label, conf) {
    const tone = TONE[label] ?? "";
    const c = conf != null && label !== "Wait" ? `<span class="conf">${conf}</span>` : "";
    return `<span class="pill ${tone}">${esc(label)}${c}</span>`;
  }
  const actionable = (r) => r && ["Long", "Short", "Buy"].includes(r.call);

  // ------------------------------------------------------------ data
  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (r.status === 401) { location.href = "/login"; throw new Error("Login required"); }
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r.json();
  }
  async function load() {
    try {
      S = await api("/api/state");
      render();
    } catch (e) {
      showError(`Can't reach the app server. Is it still running? (${e.message})`);
    }
  }
  function showError(msg) {
    const el = $("#error");
    el.hidden = !msg;
    el.textContent = msg || "";
  }

  // ------------------------------------------------------------ header
  function renderHeader() {
    const reg = $("#regime");
    reg.className = `pill ${TONE[S.regime] ?? ""}`;
    reg.textContent = S.regime || "–";
    $("#updated").textContent = S.refreshing && !S.last_refresh ? "loading…" : S.last_refresh ? `${clock(S.last_refresh)} · ${ago(S.last_refresh)}` : "–";
    const nx = S.next_refresh - Date.now() / 1000;
    $("#next").textContent = S.refreshing ? "refreshing…" : nx > 0 ? `${clock(S.next_refresh)} (${Math.floor(nx / 60)}:${String(Math.floor(nx % 60)).padStart(2, "0")})` : "due";
    const stale = S.last_refresh && Date.now() / 1000 - S.last_refresh > 20 * 60;
    const st = $("#stale");
    st.hidden = !stale;
    if (stale) st.textContent = `Data is stale: last update ${ago(S.last_refresh)}. Calls below are from ${clock(S.last_refresh)} and may no longer be valid.`;
    showError(S.last_error ? `Last refresh failed: ${S.last_error}. Showing the previous data.` : "");
    const n = (S.active_calls || []).length;
    $("#activeCount").textContent = n ? `(${n})` : "";
  }

  // ------------------------------------------------------------ day trade
  function bestSetups() {
    const out = [];
    for (const c of Object.values(S.coins)) {
      for (const m of ["futures", "spot"]) if (actionable(c[m])) out.push({ c, r: c[m], m });
    }
    out.sort((a, b) => b.r.confidence - a.r.confidence);
    const seen = new Set();
    return out.filter((x) => (seen.has(x.c.symbol) ? false : seen.add(x.c.symbol))).slice(0, 6);
  }
  function renderBest() {
    const items = bestSetups();
    const el = $("#best");
    if (!items.length) {
      el.innerHTML = `<div class="empty">No coin passes all the rules right now. That is normal; waiting is a position. The next check is at ${clock(S.next_refresh)}.</div>`;
      return;
    }
    el.innerHTML = items.map(({ c, r, m }) => {
      const sz = r.sizing || {};
      return `<button type="button" class="setup" data-sym="${esc(c.symbol)}" data-mkt="${m}">
        <div class="top"><span class="coin">${esc(c.base)}</span>${pill(r.call, r.confidence)}</div>
        <div class="lv">
          <span>Market</span><span>${m === "futures" ? "Futures" : "Spot"}</span>
          <span>Entry</span><span>${px(r.entry_zone[0])} – ${px(r.entry_zone[1])}</span>
          <span>Stop</span><span>${px(r.stop)}</span>
          <span>T1 / T2</span><span>${px(r.t1)} / ${px(r.t2)}</span>
          <span>R:R</span><span>${r.rr.toFixed(1)}</span>
          <span>Size</span><span>${qty(sz.qty)} ${esc(c.base)}${sz.leverage ? ` · ${sz.leverage}x` : ""}</span>
        </div></button>`;
    }).join("");
  }
  function renderDay() {
    renderBest();
    const syms = S.lists[list] || [];
    const rows = syms.map((s) => S.coins[s]).filter(Boolean);
    const score = (c) => Math.max(actionable(c.futures) ? c.futures.confidence : -1, actionable(c.spot) ? c.spot.confidence : -1);
    const order = rows.map((c, i) => ({ c, i, k: score(c) }))
      .sort((a, b) => (b.k - a.k) || (a.i - b.i)).map((x) => x.c);
    const body = $("#dayTable tbody");
    if (!order.length) {
      body.innerHTML = `<tr><td colspan="7" class="wide">${S.last_refresh ? "No coins in this list." : "Loading Binance data… the first refresh takes about 20 seconds."}</td></tr>`;
      return;
    }
    body.innerHTML = order.map((c) => {
      const f = c.futures, s = c.spot;
      const lead = actionable(f) ? f : actionable(s) ? s : f || s;
      const rr = actionable(lead) ? lead.rr.toFixed(1) : "–";
      const tag = list !== "top_gainers" && c.is_gainer ? `<span class="tag">gainer</span>` : "";
      return `<tr class="click" data-sym="${esc(c.symbol)}">
        <td class="coin-cell">${esc(c.base)}${tag}</td>
        <td class="num">${px(c.price)}</td>
        <td class="num ${cls(c.pct24)}">${pct(c.pct24)}</td>
        <td>${f ? pill(f.call, f.confidence) : `<span class="hint">not listed</span>`}</td>
        <td>${s ? pill(s.call, s.confidence) : "–"}</td>
        <td class="num">${rr}</td>
        <td class="wide">${esc(lead?.reason || "")}</td></tr>`;
    }).join("");
  }

  // ------------------------------------------------------------ long term
  function renderLong() {
    const syms = [...new Set(["BTCUSDT", ...S.lists.watchlist, ...S.lists.top_volume])];
    const rank = { Accumulate: 0, Hold: 1, Reduce: 2, New: 3 };
    const rows = syms.map((s) => S.coins[s]).filter((c) => c && c.long_term)
      .sort((a, b) => rank[a.long_term.rating] - rank[b.long_term.rating]);
    $("#longTable tbody").innerHTML = rows.map((c) => {
      const lt = c.long_term;
      const vs200 = lt.sma200 ? (lt.close / lt.sma200 - 1) * 100 : null;
      const ch = lt.change ? `${esc(lt.change.previous)} → ${esc(lt.rating)}${lt.change.since ? ` · ${esc(lt.change.since)}` : ""}` : "No change recorded";
      return `<tr class="click" data-sym="${esc(c.symbol)}" data-mkt="spot" data-tf="1d" title="${esc(lt.reasons.join(" · "))}">
        <td class="coin-cell">${esc(c.base)}</td><td>${pill(lt.rating)}</td>
        <td class="num">${px(lt.close)}</td><td class="num ${cls(vs200)}">${pct(vs200)}</td>
        <td class="num ${cls(lt.ret90)}">${pct(lt.ret90)}</td><td class="num ${cls(lt.ret180)}">${pct(lt.ret180)}</td>
        <td class="num ${cls(lt.rs_btc)}">${lt.rs_btc == null ? "–" : `${lt.rs_btc > 0 ? "+" : ""}${lt.rs_btc.toFixed(1)} pts`}</td>
        <td class="num">${pct(lt.drawdown)}</td>
        <td class="mono">${lt.zone ? `${px(lt.zone[0])} – ${px(lt.zone[1])}` : "–"}</td>
        <td>${ch}</td></tr>`;
    }).join("") || `<tr><td colspan="10" class="wide">Loading daily and weekly candles…</td></tr>`;
  }

  // ------------------------------------------------------------ altcoin screener
  const screenOf = (sym) => (S && S.screener && S.screener.results || []).find((r) => r.symbol === sym);
  function renderScreen() {
    const sc = S.screener || {};
    const rows = sc.results || [];
    const rec = rows.filter((r) => r.stage === "Recovering").length;
    $("#screenKpis").innerHTML = sc.updated
      ? kpi(rec, "recovering") + kpi(rows.length - rec, "basing") + kpi(sc.scanned, "altcoins scanned") +
        kpi(sc.falling, "still making new lows") + kpi(new Date(sc.updated * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }), "last scan")
      : "";
    const body = $("#screenTable tbody");
    if (!rows.length) {
      const msg = sc.error ? `Scan failed: ${esc(sc.error)}` : sc.running || !sc.updated
        ? "Scanning every liquid altcoin on Binance… the first scan takes about a minute."
        : "No altcoin passes the rules today.";
      body.innerHTML = `<tr><td colspan="10" class="wide">${msg}</td></tr>`;
      return;
    }
    body.innerHTML = rows.map((r) => `<tr class="click" data-sym="${esc(r.symbol)}" data-mkt="spot" data-tf="1d" title="${esc(r.note)}">
      <td class="coin-cell">${esc(r.base)}</td><td>${pill(r.stage)}</td>
      <td class="num">${r.score}</td><td class="num">${px(r.price)}</td>
      <td class="num down">${pct(r.drawdown, 0)}</td><td class="num">${pct(r.up_from_low, 0)}</td>
      <td class="num ${cls(r.rs_btc30)}">${r.rs_btc30 == null ? "–" : `${r.rs_btc30 > 0 ? "+" : ""}${r.rs_btc30.toFixed(1)} pts`}</td>
      <td class="num">${r.vol_ratio.toFixed(2)}×</td>
      <td class="mono">${r.zone ? `${px(r.zone[0])} – ${px(r.zone[1])}` : "–"}</td>
      <td class="num">${px(r.invalidation)} <small>(${pct(r.invalidation_pct, 0)})</small></td></tr>`).join("");
  }
  function screenCard(r) {
    const names = { above_sma50: "Above 50-day", sma50_rising: "50-day rising", higher_low: "Higher low", beats_btc: "Beats BTC (30d)", volume_returning: "Volume returning", rsi_ok: "RSI", not_extended: "Not run up yet" };
    return `<div class="card"><div class="callhead"><h3>Altcoin screener</h3>${pill(r.stage)}</div>
      <p class="reason">${esc(r.note)}</p>
      <div><div class="label">Score ${r.score}/100</div><div class="meter"><i style="width:${r.score}%"></i></div></div>
      <dl class="levels">
        <dt>From 1-year high</dt><dd>${pct(r.drawdown, 0)} <small>(${px(r.high1y)})</small></dd>
        <dt>From 1-year low</dt><dd>${pct(r.up_from_low, 0)} <small>(${px(r.low1y)})</small></dd>
        ${r.zone ? `<dt>Buy-in zone</dt><dd>${px(r.zone[0])} – ${px(r.zone[1])}</dd>` : ""}
        <dt>Idea wrong below</dt><dd>${px(r.invalidation)} <small>(${pct(r.invalidation_pct, 0)})</small></dd>
      </dl>
      <ul class="checks">${r.checks.map((k) => `<li class="${k.passed ? "ok" : "no"}"><span class="ic">${k.passed ? "✓" : "✗"}</span><span><b>${names[k.name]}</b> · ${esc(k.detail)}</span></li>`).join("")}</ul>
      <p class="hint">Price and volume only. Check the project's token unlocks and news before buying.</p></div>`;
  }

  // ------------------------------------------------------------ active calls + record
  function renderActive() {
    const rows = S.active_calls || [];
    $("#activeTable tbody").innerHTML = rows.map((c) => {
      const coin = S.coins[c.symbol];
      const now = coin?.price;
      const move = now ? ((now - c.entry) / c.entry) * 100 * (c.side === "long" ? 1 : -1) : null;
      const stop = c.t1_hit ? c.entry : c.stop;
      return `<tr class="click" data-sym="${esc(c.symbol)}" data-mkt="${esc(c.market)}">
        <td class="coin-cell">${esc(c.symbol.replace(/USDT$/, ""))}<span class="tag">${esc(c.market)}</span></td>
        <td>${pill(c.call)}</td><td class="num">${px(c.entry)}</td>
        <td class="num">${px(stop)}${c.t1_hit ? " ↑" : ""}</td>
        <td class="num">${px(c.t1)}${c.t1_hit ? " ✓" : ""}</td><td class="num">${px(c.t2)}</td>
        <td class="num ${cls(move)}">${px(now)} <small>${pct(move)}</small></td>
        <td class="wide">${pill(c.advice || "Hold")} ${esc(c.advice_note || "")}</td>
        <td>${dateTime(c.created_at)}</td></tr>`;
    }).join("") || `<tr><td colspan="9" class="wide">No active calls. New Long, Short and Buy calls appear here and are tracked until they close.</td></tr>`;
    const t = S.track_record || {};
    $("#record").innerHTML = t.count
      ? kpi(t.count, "closed calls") + kpi(`${t.win_rate}%`, "win rate") + kpi(`${t.total_r > 0 ? "+" : ""}${t.total_r}R`, "total result") + kpi(`${t.avg_r > 0 ? "+" : ""}${t.avg_r}R`, "average per call")
      : `<div class="empty">No closed calls yet. The record fills in as calls hit targets, stops or exit rules.</div>`;
  }
  const kpi = (v, l) => `<div class="kpi"><b>${esc(v)}</b><span>${esc(l)}</span></div>`;
  async function renderHistory() {
    const rows = await api("/api/calls/history");
    $("#historyTable tbody").innerHTML = rows.map((c) => `<tr>
      <td>${dateTime(c.closed_at)}</td><td class="coin-cell">${esc(c.symbol.replace(/USDT$/, ""))}<span class="tag">${esc(c.market)}</span></td>
      <td>${pill(c.call)}</td><td class="num">${px(c.entry)}</td><td class="num">${px(c.exit_price)}</td>
      <td class="num ${cls(c.result_r)}">${c.result_r > 0 ? "+" : ""}${c.result_r.toFixed(2)}R</td>
      <td class="wide">${esc(c.outcome)}</td></tr>`).join("") || `<tr><td colspan="7" class="wide">Nothing closed yet.</td></tr>`;
  }

  // ------------------------------------------------------------ journal
  function pnl(j) {
    if (j.exit == null || j.entry == null || j.qty == null) return null;
    return (j.exit - j.entry) * j.qty * (j.side === "short" ? -1 : 1);
  }
  function renderJournal(rows) {
    $("#jTable tbody").innerHTML = rows.map((j) => {
      const p = pnl(j);
      return `<tr><td>${esc(j.trade_date)}</td><td class="coin-cell">${esc(j.symbol)}</td><td>${esc(j.market)}</td>
        <td>${j.side === "short" ? "Short" : "Long / Buy"}</td><td class="num">${px(j.entry)}</td><td class="num">${px(j.exit)}</td>
        <td class="num">${qty(j.qty)}</td><td class="num ${cls(p)}">${p == null ? "open" : `${p > 0 ? "+" : ""}${usd(p)}`}</td>
        <td>${j.followed ? "Yes" : "No"}</td><td class="wide">${esc(j.notes)}</td>
        <td><button class="btn ghost small" type="button" data-del="${j.id}">Delete</button></td></tr>`;
    }).join("") || `<tr><td colspan="11" class="wide">No trades logged yet.</td></tr>`;
    const closed = rows.filter((j) => pnl(j) != null);
    const wins = closed.filter((j) => pnl(j) > 0).length;
    const total = closed.reduce((a, j) => a + pnl(j), 0);
    const fol = closed.filter((j) => j.followed), folTotal = fol.reduce((a, j) => a + pnl(j), 0);
    $("#jstats").innerHTML = closed.length
      ? kpi(closed.length, "closed trades") + kpi(`${Math.round((wins / closed.length) * 100)}%`, "win rate") +
        kpi(`${total > 0 ? "+" : ""}${usd(total)}`, "total P/L (USDT)") + kpi(`${folTotal > 0 ? "+" : ""}${usd(folTotal)}`, "P/L when following calls")
      : "";
  }
  async function loadJournal() { renderJournal(await api("/api/journal")); }

  // ------------------------------------------------------------ settings
  function renderSettings(force) {
    const s = S.settings;
    const set = (id, v) => { const el = $(id); if (force || document.activeElement !== el) el.value = v; };
    set("#s-account", s.account_size); set("#s-risk", s.risk_pct); set("#s-lev", s.max_leverage); set("#s-maxpos", s.max_position_pct);
    $("#chips").innerHTML = s.watchlist.map((w) => `<span class="chip">${esc(w.replace(/USDT$/, ""))}<button type="button" data-rm="${esc(w)}" aria-label="Remove ${esc(w)}">×</button></span>`).join("");
    $("#tgStatus").textContent = S.telegram ? "Connected: alerts go to your Telegram chat." : "Not set up yet: alerts are off.";
    $("#tgTest").disabled = !S.telegram;
    $("#rulesVersion").textContent = S.rules_version;
    const w = S.used_weight || {};
    $("#weight").textContent = `spot ${w.spot ?? "–"} · futures ${w.futures ?? "–"}`;
  }
  async function saveSettings(body) {
    S.settings = await api("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    await load();
  }

  // ------------------------------------------------------------ coin view
  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function openCoin(symbol, market, tf) {
    const c = S.coins[symbol];
    view.symbol = symbol;
    view.market = market || (c && c.futures ? "futures" : "spot");
    if (view.market === "futures" && c && !c.futures) view.market = "spot";
    view.tf = tf || "1h";
    $("#coinView").hidden = false;
    document.body.style.overflow = "hidden";
    syncSegs();
    renderCoin();
  }
  function closeCoin() {
    $("#coinView").hidden = true;
    document.body.style.overflow = "";
    if (chart) { chart.remove(); chart = null; }
    view.symbol = null;
  }
  function syncSegs() {
    $$("#mktSeg button").forEach((b) => b.classList.toggle("on", b.dataset.mkt === view.market));
    $$("#tfSeg button").forEach((b) => b.classList.toggle("on", b.dataset.tf === view.tf));
    const c = S.coins[view.symbol];
    $('#mktSeg [data-mkt="futures"]').disabled = !(c && c.futures);
  }
  function renderCoin() {
    const c = S.coins[view.symbol];
    $("#cvTitle").textContent = view.symbol.replace(/USDT$/, " / USDT");
    const info = c || screenOf(view.symbol);
    $("#cvSub").innerHTML = info ? `${px(info.price)} · <span class="${cls(info.pct24)}">${pct(info.pct24)}</span> 24h · vol ${(info.quote_volume / 1e6).toFixed(0)}M USDT` : "";
    $("#lgVwap").hidden = !["15m", "1h"].includes(view.tf);
    $("#lgSma").hidden = view.tf !== "1d";
    renderSide(c);
    drawChart(c);
  }
  function renderSide(c) {
    const side = $("#cvSide");
    const sr = screenOf(view.symbol);
    if (!c) {
      side.innerHTML = sr ? screenCard(sr) : `<div class="card"><p class="reason">This coin is not in any list right now.</p></div>`;
      return;
    }
    const r = c[view.market];
    let html = "";
    if (r) {
      const lv = actionable(r) ? `<dl class="levels">
          <dt>Entry zone</dt><dd>${px(r.entry_zone[0])} – ${px(r.entry_zone[1])}</dd>
          <dt>Stop-loss</dt><dd>${px(r.stop)} <small>(${r.risk_pct}%)</small></dd>
          <dt>Target 1 (take half)</dt><dd>${px(r.t1)}</dd>
          <dt>Target 2</dt><dd>${px(r.t2)}</dd>
          <dt>Reward-to-risk</dt><dd>${r.rr.toFixed(1)}</dd></dl>` : "";
      html += `<div class="card">
        <div class="callhead"><h3>${view.market === "futures" ? "Futures" : "Spot"} day trade</h3>${pill(r.call, r.confidence)}</div>
        <p class="reason">${esc(r.reason)}</p>
        <div><div class="label">Confidence ${r.confidence}/100</div><div class="meter"><i style="width:${r.confidence}%"></i></div></div>
        ${lv}</div>`;
      if (r.sizing) {
        const z = r.sizing;
        html += `<div class="card"><h3>Position size</h3><dl class="levels">
          <dt>Risk</dt><dd>${usd(z.risk_usdt)} USDT (${S.settings.risk_pct}%)</dd>
          <dt>Quantity</dt><dd>${qty(z.qty)} ${esc(c.base)}</dd>
          <dt>Position value</dt><dd>${usd(z.notional)} USDT</dd>
          ${z.leverage ? `<dt>Leverage</dt><dd>${z.leverage}x</dd><dt>Margin</dt><dd>${usd(z.margin)} USDT</dd>` : ""}
          ${z.liquidation ? `<dt>Est. liquidation</dt><dd>${px(z.liquidation)}</dd>` : ""}
          </dl>${z.warnings.length ? `<ul class="warnings">${z.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}</div>`;
      }
      if (r.checks && r.checks.length) {
        const names = { trend: "Trend (4h)", setup: "Setup (1h)", rsi: "RSI (1h)", macd: "MACD (1h)", volume: "Volume (1h)", timing: "Entry timing (15m)", regime: "BTC regime", positioning: "Futures positioning" };
        html += `<div class="card"><h3>Checks for ${r.side === "short" ? "a short" : "a long"}</h3><ul class="checks">${r.checks.map((k) =>
          `<li class="${!k.applies ? "na" : k.passed ? "ok" : "no"}"><span class="ic">${!k.applies ? "–" : k.passed ? "✓" : "✗"}</span><span><b>${names[k.name]}</b> · ${esc(k.detail)}</span></li>`).join("")}</ul></div>`;
      }
    }
    const lt = c.long_term;
    if (lt) {
      html += `<div class="card"><div class="callhead"><h3>Long-term</h3>${pill(lt.rating)}</div>
        <ul class="checks">${lt.reasons.map((t) => `<li class="na" style="opacity:1"><span class="ic">·</span><span>${esc(t)}</span></li>`).join("")}</ul>
        ${lt.zone ? `<p class="reason">Buy-in zone for gradual buying: <span class="mono">${px(lt.zone[0])} – ${px(lt.zone[1])}</span></p>` : ""}</div>`;
    }
    if (sr) html += screenCard(sr);
    side.innerHTML = html;
  }
  async function drawChart(c) {
    const el = $("#chart");
    if (chart) { chart.remove(); chart = null; }
    el.innerHTML = "";
    let data;
    try {
      data = await api(`/api/candles?symbol=${encodeURIComponent(view.symbol)}&market=${view.market}&interval=${view.tf}`);
    } catch (e) {
      el.innerHTML = `<p class="reason" style="padding:16px">Could not load candles: ${esc(e.message)}</p>`;
      return;
    }
    if (!window.LightweightCharts) { el.innerHTML = `<p class="reason" style="padding:16px">Chart library missing.</p>`; return; }
    const off = -new Date().getTimezoneOffset() * 60; // show local time
    chart = LightweightCharts.createChart(el, {
      autoSize: true,
      localization: { locale: "en-US" },
      layout: { background: { color: css("--surface") }, textColor: css("--ink-2"), fontFamily: "IBM Plex Mono, ui-monospace, monospace", fontSize: 11 },
      grid: { vertLines: { color: css("--surface-2") }, horzLines: { color: css("--surface-2") } },
      rightPriceScale: { borderColor: css("--line") },
      timeScale: { borderColor: css("--line"), timeVisible: view.tf !== "1d", secondsVisible: false },
      crosshair: { mode: 0 },
    });
    const up = css("--candle-up"), down = css("--candle-down");
    const candle = chart.addCandlestickSeries({ upColor: up, downColor: down, borderVisible: false, wickUpColor: up, wickDownColor: down,
      priceFormat: { type: "price", precision: decimals(data), minMove: 1 / 10 ** decimals(data) } });
    candle.setData(data.map((d) => ({ time: d.t + off, open: d.o, high: d.h, low: d.l, close: d.c })));
    const vol = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(data.map((d) => ({ time: d.t + off, value: d.v, color: (d.c >= d.o ? up : down) + "55" })));
    const line = (key, color) => {
      const pts = data.filter((d) => d[key] != null).map((d) => ({ time: d.t + off, value: d[key] }));
      if (!pts.length) return;
      chart.addLineSeries({ color, lineWidth: 2, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }).setData(pts);
    };
    line("ema20", css("--s1")); line("ema50", css("--s2")); line("vwap", css("--s3")); line("sma200", css("--s4"));
    const r = c && c[view.market];
    if (actionable(r)) {
      const pl = (price, title, color) => candle.createPriceLine({ price, title, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true });
      pl(r.entry, "Entry", css("--ink-2")); pl(r.stop, "Stop", css("--bad")); pl(r.t1, "T1", css("--good")); pl(r.t2, "T2", css("--good"));
    }
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, data.length - 120), to: data.length + 3 });
  }
  function decimals(data) {
    const p = data.length ? data[data.length - 1].c : 1;
    if (p >= 1000) return 1; if (p >= 100) return 2; if (p >= 1) return 3;
    return Math.min(10, Math.max(4, -Math.floor(Math.log10(p)) + 3));
  }

  // ------------------------------------------------------------ render all
  function render() {
    $("#logout").hidden = !S.auth;
    renderHeader();
    renderDay();
    renderLong();
    renderScreen();
    renderActive();
    renderSettings(false);
    if (view.symbol && !$("#coinView").hidden) renderSide(S.coins[view.symbol]);
  }

  // ------------------------------------------------------------ events
  $$(".tab").forEach((t) => t.addEventListener("click", () => {
    $$(".tab").forEach((x) => x.classList.toggle("active", x === t));
    $$(".panel").forEach((p) => (p.hidden = p.id !== `tab-${t.dataset.tab}`));
    if (t.dataset.tab === "active") renderHistory().catch(() => {});
    if (t.dataset.tab === "journal") loadJournal().catch(() => {});
    if (t.dataset.tab === "settings" && S) renderSettings(true);
  }));
  $("#listSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    list = b.dataset.list;
    $$("#listSeg button").forEach((x) => x.classList.toggle("on", x === b));
    if (S) renderDay();
  });
  document.addEventListener("click", (e) => {
    const row = e.target.closest("[data-sym]");
    if (row && S && !e.target.closest("[data-del]")) openCoin(row.dataset.sym, row.dataset.mkt, row.dataset.tf);
  });
  $("#cvClose").addEventListener("click", closeCoin);
  $("#coinView").addEventListener("click", (e) => { if (e.target.id === "coinView") closeCoin(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#coinView").hidden) closeCoin(); });
  $("#mktSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b || b.disabled) return;
    view.market = b.dataset.mkt; syncSegs(); renderCoin();
  });
  $("#tfSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    view.tf = b.dataset.tf; syncSegs(); renderCoin();
  });
  $("#refreshBtn").addEventListener("click", async () => {
    await api("/api/refresh", { method: "POST" });
    S.refreshing = true; renderHeader();
    setTimeout(load, 4000); setTimeout(load, 15000);
  });
  $("#riskForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    await saveSettings({ account_size: +$("#s-account").value, risk_pct: +$("#s-risk").value, max_leverage: +$("#s-lev").value, max_position_pct: +$("#s-maxpos").value });
    renderSettings(true);
    const n = $("#riskSaved"); n.hidden = false; setTimeout(() => (n.hidden = true), 3000);
  });
  $("#addForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const v = $("#addSym").value.trim();
    if (!v) return;
    await saveSettings({ watchlist: [...S.settings.watchlist, v] });
    $("#addSym").value = "";
    setTimeout(load, 8000);
  });
  $("#chips").addEventListener("click", (e) => {
    const b = e.target.closest("[data-rm]"); if (!b) return;
    saveSettings({ watchlist: S.settings.watchlist.filter((w) => w !== b.dataset.rm) });
  });
  $("#tgTest").addEventListener("click", async () => {
    const r = await api("/api/telegram/test", { method: "POST" });
    $("#tgResult").textContent = r.ok ? "Test message sent. Check Telegram." : r.message;
  });
  $("#jform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      trade_date: $("#j-date").value, symbol: $("#j-symbol").value.trim().toUpperCase(), market: $("#j-market").value,
      side: $("#j-side").value, entry: $("#j-entry").value, exit: $("#j-exit").value, qty: $("#j-qty").value,
      followed: $("#j-followed").checked, notes: $("#j-notes").value,
    };
    renderJournal(await api("/api/journal", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));
    e.target.reset(); $("#j-date").valueAsDate = new Date(); $("#j-followed").checked = true;
  });
  $("#jTable").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-del]"); if (!b) return;
    renderJournal(await api(`/api/journal/${b.dataset.del}`, { method: "DELETE" }));
  });
  $("#j-date").valueAsDate = new Date();

  load();
  setInterval(load, 30_000);
  setInterval(() => S && renderHeader(), 1000);
})();

document.getElementById("logout").addEventListener("click", async (e) => {
  e.preventDefault();
  await fetch("/logout", { method: "POST" });
  location.href = "/login";
});
