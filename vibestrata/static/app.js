/* vibestrata — vanilla JS, no build step. */
(() => {
  "use strict";

  const TOKEN = document.querySelector('meta[name="vibestrata-token"]').content;
  const $ = (s, el = document) => el.querySelector(s);
  const SVGNS = "http://www.w3.org/2000/svg";
  const DAY = 86400;
  // building · done (in use, done, moved on) · unfinished (stuck, on hold, tried once, dropped)
  const STATES = ["active", "in-use", "delivered", "superseded", "blocked", "paused", "one-shot"];
  const USER_STATES = [...STATES, "closed"];
  const GROUPS = [["now", ["active"]], ["done", ["in-use", "delivered", "superseded"]], ["unfinished", ["blocked", "paused", "one-shot", "closed"]]];
  const groupOf = (s) => (GROUPS.find(([, ss]) => ss.includes(s)) || GROUPS[2])[0];
  const COLORS = { active: "#C2461F", "in-use": "#2B4C7E", delivered: "#3F6B4E", superseded: "#5B6C7A", blocked: "#7B3B63", paused: "#C8912A", "one-shot": "#A79C86", closed: "#BDB3A0" };
  const COMP_COLORS = { source: "#1D1A15", git: "#7C7466", deps: "#C2461F", cache: "#C8912A", build: "#5B6C7A", output: "#A79C86" };

  const S = {
    data: null, state: { pins: [], overrides: {}, labels: {}, names: {}, notes: {} }, progress: {}, ops: [],
    lang: localStorage.getItem("vibestrata.lang") || ((navigator.language || "en").startsWith("ko") ? "ko" : "en"),
    f: { q: "", status: "all", reclaim: false, unsaved: false, pinned: false },
    sort: localStorage.getItem("vibestrata.sort2") || "state",
    sel: null, picks: new Set(), checked: new Set(), hover: null, editing: false, legend: false,
  };

  // ── utils ────────────────────────────────────────────────────────────
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function t(key, vars) {
    const pick = (lang) => key.split(".").reduce((o, k) => (o == null ? o : o[k]), window.STRINGS[lang]);
    let s = pick(S.lang);
    if (s == null) s = pick("en");
    if (s == null) return key;
    if (vars) for (const k in vars) s = s.split("{" + k + "}").join(vars[k]);
    return s;
  }
  function bytes(n) {
    if (!n) return "0 B";
    const u = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i < 2 ? Math.round(n) : n >= 100 ? Math.round(n) : n.toFixed(1)) + " " + u[i];
  }
  function ago(ts) {
    if (!ts) return "—";
    const d = (Date.now() / 1000 - ts) / DAY;
    if (d < 1) return t("today");
    if (d < 60) return t("days_ago", { n: Math.round(d) });
    if (d < 365) return t("months_ago", { n: Math.round(d / 30) });
    return t("years_ago", { n: (d / 365).toFixed(1).replace(/\.0$/, "") });
  }
  function dateStr(ts) {
    if (!ts) return "—";
    const d = new Date(ts * 1000), same = d.getFullYear() === new Date().getFullYear();
    return d.toLocaleDateString(S.lang === "ko" ? "ko-KR" : "en-US", { year: same ? undefined : "numeric", month: "short", day: "numeric" });
  }
  const q = (s) => "“" + s + "”";
  // effective state: what you set > the triage skill's reading > the rules
  const LEGACY = { keep: "in-use" };
  function eff(p) {
    const o = LEGACY[(S.state.overrides || {})[p.id]] || (S.state.overrides || {})[p.id];
    if (USER_STATES.includes(o)) return o;
    const l0 = ((S.state.labels || {})[p.id] || {}).state, l = LEGACY[l0] || l0;
    if (USER_STATES.includes(l)) return l;
    return p.state || "paused";
  }
  const source = (p) => USER_STATES.includes(LEGACY[(S.state.overrides || {})[p.id]] || (S.state.overrides || {})[p.id]) ? "you" : ((S.state.labels || {})[p.id] && (S.state.labels || {})[p.id].state ? "triage" : "rules");
  const pinned = (p) => S.state.pins.includes(p.id);
  const label = (p) => (S.state.labels || {})[p.id] || {};
  const named = (p) => (S.state.names || {})[p.id] || {};
  const titleOf = (p) => named(p).title || label(p).title || p.title || p.name;
  const summaryOf = (p) => named(p).summary || label(p).summary || p.summary || "";
  const noteOf = (p) => ((S.state.notes || {})[p.id] || {}).text || "";
  // mirrors segment.plan(): what the drawer pre-ticks
  function planFor(p) {
    const s = eff(p), th = S.data ? S.data.thresholds : { cooling: 45 };
    if (pinned(p) || s === "active" || s === "in-use") return { regen: false, byproducts: false };
    const stale = p.days == null || p.days >= th.cooling;
    const regen = ["delivered", "superseded", "one-shot", "closed"].includes(s) || stale;
    const byproducts = ["delivered", "superseded", "closed"].includes(s) && !(p.flags && p.flags.unsaved);
    return { regen, byproducts };
  }
  const forgotten = (p) => eff(p) === "paused" && p.stale;
  function stateLabel(p) {
    const s = eff(p);
    return t("state." + s) + (s === "paused" && p.days != null ? " · " + t("days_n", { n: Math.round(p.days) }) : "");
  }
  function regLabel(l) {
    const m = String(l).match(/^(skill|agent|command|launch agent|mcp): (.*)$/);
    if (m) return t("reg." + m[1].replace(" ", "_"), { n: m[2] });
    return l === "shell config" ? t("reg.shell") : l;
  }
  function whyText(p) {
    const s = eff(p), src = source(p);
    if (src === "you") return t("why_you");
    if (src === "triage") { const l = label(p); return t("why_triage", { c: t("conf." + (l.confidence || "low")) }) + (l.why ? " — " + l.why : ""); }
    const parts = (p.why || []).filter(([k]) => k !== "idle");
    if (!parts.length) return t(s === "active" ? "why.active" : s === "paused" && p.stale ? "why.forgotten" : "why.paused_plain");
    return parts.map(([k, v]) => t("why." + k, Object.assign({}, v, { q: v && v.q ? q(v.q) : "", f: v && v.f ? v.f.split("/").pop() : "", src: v && v.src ? regLabel(v.src) : t("why.outputs_only") }))).join(" · ");
  }
  const kindLabel = (p) => t("kind." + (p.kind || "other"));
  const projects = () => (S.data && S.data.projects) || [];
  const byId = (id) => projects().find((x) => x.id === id);

  async function api(path, body) {
    const opt = body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Vibestrata-Token": TOKEN }, body: JSON.stringify(body),
    };
    const r = await fetch(path, opt);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(t("reasons." + (j.error || "")) !== "reasons." + (j.error || "") ? t("reasons." + j.error) : (j.error || r.statusText));
    return j;
  }

  let toastTimer;
  function toast(msg, action) {
    const el = $("#toast");
    el.innerHTML = esc(msg) + (action ? ` <button class="btn sm inv">${esc(action.label)}</button>` : "");
    if (action) el.querySelector("button").onclick = () => { el.classList.remove("on"); action.fn(); };
    el.classList.add("on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("on"), action ? 9000 : 4200);
  }

  // ── data flow ────────────────────────────────────────────────────────
  async function load() {
    const j = await api("/api/scan");
    S.data = j.result && j.result.projects ? j.result : null;
    S.state = Object.assign({ pins: [], overrides: {}, labels: {}, names: {}, notes: {} }, j.state);
    S.cfg = j.config;
    if (j.app_mode && !S.appMode) {
      S.appMode = true;
      $("#quit").hidden = false;
      const ping = () => api("/api/ping", {}).catch(() => {});
      ping();
      setInterval(ping, 5000);
    }
    S.progress = j.progress || {};
    await loadOps();
    render();
    if (S.progress.running) poll();
  }
  async function loadOps() {
    try { S.ops = (await api("/api/ops")).ops; } catch (e) { S.ops = []; }
  }
  let polling = false;
  async function poll() {
    if (polling) return;
    polling = true;
    renderProgress();
    while (true) {
      await new Promise((r) => setTimeout(r, 500));
      try { S.progress = await api("/api/progress"); } catch (e) { break; }
      renderProgress();
      if (!S.progress.running) break;
    }
    polling = false;
    const j = await api("/api/scan");
    S.data = j.result && j.result.projects ? j.result : S.data;
    render();
  }
  function mergeProjects(fresh, totals, gone) {
    if (!S.data) return;
    const byPath = new Map((fresh || []).map((p) => [p.path, p]));
    const goneSet = new Set(gone || []);
    S.data.projects = S.data.projects.filter((p) => !goneSet.has(p.path)).map((p) => { const f = byPath.get(p.path); byPath.delete(p.path); return f || p; });
    byPath.forEach((p) => S.data.projects.push(p));  // back from the Trash
    for (const id of [...S.checked]) if (!byId(id)) S.checked.delete(id);
    if (totals) S.data.totals = totals;
  }

  // ── render: header & lede ────────────────────────────────────────────
  function renderStatic() {
    document.documentElement.lang = S.lang;
    document.querySelectorAll("[data-t]").forEach((el) => { el.textContent = t(el.dataset.t); });
    $("#q").placeholder = t("search");
    const sort = $("#sort");
    sort.innerHTML = Object.keys(window.STRINGS.en.sort).map((k) => `<option value="${k}">${esc(t("sort." + k))}</option>`).join("");
    sort.value = S.sort;
  }
  function renderProgress() {
    const pr = S.progress || {};
    const bar = $("#progress");
    const i = bar.firstElementChild;
    if (pr.running) {
      bar.classList.toggle("indet", !pr.total);
      i.style.width = pr.total ? (100 * pr.done / pr.total).toFixed(1) + "%" : "30%";
      $("#rescan").disabled = true;
      $("#rescan").textContent = `${t("surveying")} ${pr.done || 0}/${pr.total || "…"}`;
    } else {
      bar.classList.remove("indet");
      i.style.width = "0";
      $("#rescan").disabled = false;
      $("#rescan").textContent = t("rescan");
    }
    if (!S.data) $("#lede").textContent = t("first_run");
  }
  function renderMeta() {
    const d = S.data;
    const cur = (S.progress.current || []).slice(0, 2).join(", ");
    $("#meta").innerHTML = d ? (d.demo ? `<span>mode<b class="mono" style="color:var(--signal)">DEMO · synthetic</b></span>` : "") + `
      <span>${esc(t("scanned"))}<b>${esc(new Date(d.scanned_at * 1000).toLocaleString(S.lang === "ko" ? "ko-KR" : "en-US", { dateStyle: "medium", timeStyle: "short" }))}</b></span>
      <span>root<b class="mono">${esc(d.home)}</b></span>
      <span>scan<b class="mono">${d.scan_seconds}s</b></span>
      ${S.progress.running && cur ? `<span>${esc(t("surveying"))}<b class="mono">${esc(cur)}</b></span>` : ""}` : "";
  }
  function counts() {
    const c = { now: 0, done: 0, unfinished: 0 };
    projects().forEach((p) => { const s = eff(p); c[s] = (c[s] || 0) + 1; c[groupOf(s)]++; });
    c.forgotten = projects().filter(forgotten).length;
    return c;
  }
  function spanMonths() {
    const firsts = projects().map((p) => (p.claude && (p.claude.first || p.claude.p_first)) || 0).filter(Boolean);
    if (!firsts.length) return 0;
    return Math.max(1, Math.round((Date.now() / 1000 - Math.min(...firsts)) / DAY / 30.4));
  }
  function renderLede() {
    if (!S.data) { $("#lede").textContent = t("first_run"); $("#mini").innerHTML = ""; return; }
    const T = S.data.totals, c = counts(), m = spanMonths();
    const week = projects().filter((p) => p.days != null && p.days < 7).length;
    $("#lede").innerHTML = t(m ? "lede" : "lede_nospan", { p: T.projects, m, now: c.now, done: c.done, un: c.unfinished })
      + `<span class="lede2">${t(week ? "lede2" : "lede2_none", { w: week })}</span>`;
    const row = (s) => `<div class="st" title="${esc(t("state_hint." + s))}"><span><i class="sw" style="background:${COLORS[s]}"></i>${esc(t("state." + s))}${s === "paused" && c.forgotten ? ` <span class="dim">${esc(t("stale_n", { n: c.forgotten }))}</span>` : ""}</span><span>${c[s] || 0}</span></div>`;
    $("#mini").innerHTML = GROUPS.map(([g, ss]) => `<div class="grp"><span>${esc(t("group." + g))}</span><span>${c[g]}</span></div>`
      + ss.filter((s) => STATES.includes(s) || c[s]).map(row).join("")).join("")
      + `<div class="grp disk"><span>${esc(t("disk"))}</span><span>${bytes(T.bytes)}</span></div>`
      + `<div class="st" title="${esc(t("regen_hint"))}"><span>${esc(t("regenerable"))}</span><span>${bytes(T.reclaim)}</span></div>`
      + `<div class="st" title="${esc(t("byproducts_hint"))}"><span>${esc(t("byproducts"))}</span><span>${bytes(T.byproducts || 0)}</span></div>`;
  }
  // ── render: the board, where things stand ────────────────────────────
  const BOARD = 4;
  const LANES = [
    ["now", "active", (p) => eff(p) === "active", (a, b) => (b.activity.last || 0) - (a.activity.last || 0)],
    ["stuck", "blocked", (p) => eff(p) === "blocked", (a, b) => (b.activity.last || 0) - (a.activity.last || 0)],
    ["forgot", "paused", forgotten, (a, b) => ((b.claude || {}).meaningful || 0) - ((a.claude || {}).meaningful || 0) || (b.activity.last || 0) - (a.activity.last || 0)],
  ];
  // "save progress", "commit and push": how sessions end, not what they were about (mirrors claude.RITUAL_RE)
  const RITUAL = /중간\s*저장|저장해\s*줘|여기까지|커밋\s*해|푸시\s*해|이어서\s*하자|\bcommit\b|\bpush\b|save (?:it|progress|this)|continue (?:tomorrow|later)/i;
  function lastWork(p) {
    const c = p.claude || {};
    if (!c.last_prompt || !RITUAL.test(c.last_prompt)) return c.last_prompt || "";
    const r = (c.recent || []).slice().reverse().find(([, x]) => x && x.length >= 8 && !RITUAL.test(x));
    return r ? r[1] : c.last_prompt;
  }
  // "[1.3.1] — 2026-08-16 — Fixed …" → "Fixed …": a changelog line, without its version and date
  const noteBody = (x) => String(x).replace(/^(?:\s*(?:\[[^\]]{1,20}\]\s*[—–:·|-]?|(?:v?\d+\.\d+(?:\.\d+)?|\d{4}[-.]\d{1,2}[-.]\d{1,2})\s*[—–:·|-])\s*)+/, "") || x;
  // the one line that says where it stands: your note, the words it stopped on, or the last thing you worked on
  function boardQuote(p) {
    const s = eff(p), c = p.claude || {};
    if (noteOf(p)) return `<span class="b-q note">✎ ${esc(noteOf(p))}</span>`;
    if (s === "blocked" && source(p) === "rules") { const w = (p.why || []).find(([, v]) => v && v.q); if (w) return `<span class="b-q blk">${esc(q(w[1].q))}</span>`; }
    if (p.resume && s !== "active") return `<span class="b-q">↳ ${esc(noteBody(p.resume.text))}</span>`;
    if (c.last_prompt) return `<span class="b-q">${esc(q(lastWork(p)))}</span>`;
    return summaryOf(p) ? `<span class="b-q dim">${esc(summaryOf(p))}</span>` : "";
  }
  function renderBoard() {
    const el = $("#board");
    if (!S.data) { el.innerHTML = ""; return; }
    el.innerHTML = LANES.map(([key, st, keep, order]) => {
      const all = projects().filter(keep).sort(order), top = all.slice(0, BOARD);
      const items = top.map((p) => {
        const c = p.claude || {};
        const meta = [ago(p.activity.last), key === "forgot" && c.meaningful ? t("board.talks", { n: c.meaningful }) : ""].filter(Boolean).join(" · ");
        return `<li data-id="${p.id}" tabindex="0"><span class="b-ttl">${pinned(p) ? `<span class="pinmark">◆</span>` : ""}${esc(titleOf(p))}</span>${boardQuote(p)}<span class="b-meta">${esc(meta)}</span></li>`;
      }).join("");
      return `<section class="lane">
        <header title="${esc(t("board.hint_" + key))}"><i class="sw" style="background:${COLORS[st]}"></i>${esc(t("board." + key))}<span class="n">${all.length}</span><span class="how">${esc(t("board.how_" + key))}</span></header>
        ${items ? `<ol>${items}</ol>` : `<div class="empty-lane">${esc(t("board.none_" + key))}</div>`}
        ${all.length > BOARD ? `<button class="lane-more" data-s="${st}">${esc(t("board.more", { n: all.length }))}</button>` : ""}
      </section>`;
    }).join("");
  }
  function showInList(status) {
    S.f.status = status;
    renderChips(); renderRows(); strata(false);
    $(".toolbar").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderLegend() {
    const el = $("#legend");
    el.hidden = !S.legend;
    $("#legendBtn").setAttribute("aria-pressed", String(S.legend));
    if (!S.legend) return;
    el.innerHTML = `<p class="lg-intro">${esc(t("legend_intro"))}</p>` + GROUPS.map(([g, ss]) => `<div class="lg-group"><h5>${esc(t("group." + g))}</h5>`
      + ss.map((s) => `<div class="lg-row"><span class="stat"><span class="dot" style="background:${COLORS[s]}"></span>${esc(t("state." + s))}</span><span class="lg-what">${esc(t("state_hint." + s))}</span><span class="lg-do">${esc(t("state_do." + s))}</span></div>`).join("") + `</div>`).join("");
  }

  // ── render: strata ───────────────────────────────────────────────────
  const strataEls = new Map();
  let strataLayout = null;
  function strata(animate) {
    const svg = $("#strata");
    const W = svg.clientWidth || 1200, H = 360;
    const L = 8, R = W - 64, TOP = 46, BOT = H - 10;
    const MAXD = 730;
    const th = S.data ? S.data.thresholds : { active: 14, cooling: 45, dormant: 120 };
    const xOf = (d) => L + (R - L) * Math.log1p(Math.min(Math.max(d, 0), MAXD)) / Math.log1p(MAXD);
    const now = Date.now() / 1000;
    const list = projects();
    const matches = new Set(filtered().map((p) => p.id));
    const filtering = S.f.q || S.f.status !== "all" || S.f.reclaim || S.f.unsaved || S.f.pinned;

    const fresh = !strataLayout || strataLayout.W !== W;
    if (fresh) {
      svg.innerHTML = "";
      strataEls.clear();
      svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
      const defs = document.createElementNS(SVGNS, "defs");
      defs.innerHTML = `<pattern id="hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="5" height="5" fill="rgba(241,236,226,.55)"/><line x1="0" y1="0" x2="0" y2="5" stroke="#1D1A15" stroke-width="1.6"/></pattern>`;
      svg.appendChild(defs);
      const bands = [[0, th.active, "b0"], [th.active, th.cooling, "b1"], [th.cooling, th.dormant, "b2"], [th.dormant, MAXD, "b3"]];
      const bg = document.createElementNS(SVGNS, "g");
      let html = "";
      bands.forEach(([a, b, s], i) => {
        const x0 = xOf(a), x1 = i === 3 ? W : xOf(b);
        html += `<rect x="${x0}" y="${TOP}" width="${x1 - x0}" height="${BOT - TOP}" fill="#1D1A15" opacity="${0.012 + i * 0.016}"/>`;
        html += `<line x1="${x0}" x2="${x0}" y1="10" y2="${BOT}" stroke="#1D1A15" stroke-opacity="${i ? 0.35 : 0}" stroke-dasharray="2 3"/>`;
        html += `<text class="band-label" x="${x0 + 8}" y="20" data-band="${s}">${esc(t("band." + s, { a: th.active, c: th.cooling, d: th.dormant }))}</text>`;
      });
      for (let y = TOP + 30; y < BOT; y += 30) html += `<line x1="${L}" x2="${W}" y1="${y}" y2="${y}" stroke="#1D1A15" stroke-opacity=".06"/>`;
      [[0, "today"], [7, "7d"], [30, "30d"], [90, "90d"], [180, "180d"], [365, "1y"], [730, "2y+"]].forEach(([d, lab]) => {
        const x = xOf(d);
        html += `<line x1="${x}" x2="${x}" y1="${TOP - 6}" y2="${TOP}" stroke="#1D1A15"/>`;
        html += `<text class="tick" data-tick="${lab}" x="${x + (d ? -3 : 2)}" y="${TOP - 10}" text-anchor="${d ? "end" : "start"}">${esc(lab === "today" ? t("today") : lab)}</text>`;
      });
      html += `<line x1="${L}" x2="${W}" y1="${TOP}" y2="${TOP}" stroke="#1D1A15" stroke-width="1.5"/>`;
      bg.innerHTML = html;
      svg.appendChild(bg);
      const g = document.createElementNS(SVGNS, "g");
      g.id = "blocks";
      svg.appendChild(g);
      const sc = document.createElementNS(SVGNS, "g");
      sc.id = "scale";
      svg.appendChild(sc);
    } else {
      svg.querySelectorAll("[data-band]").forEach((el) => { el.textContent = t("band." + el.dataset.band, { a: th.active, c: th.cooling, d: th.dormant }); });
      const td = svg.querySelector('[data-tick="today"]');
      if (td) td.textContent = t("today");
    }

    // columns
    const BW = 11, GAP = 2;
    const cols = new Map();
    list.forEach((p) => {
      const d = p.activity.last ? (now - p.activity.last) / DAY : MAXD;
      const c = Math.floor((xOf(d) - L) / (BW + GAP));
      if (!cols.has(c)) cols.set(c, []);
      cols.get(c).push({ p, d });
    });
    let maxSum = 1;
    cols.forEach((arr) => {
      arr.sort((a, b) => a.d - b.d);
      maxSum = Math.max(maxSum, arr.reduce((s, o) => s + Math.sqrt(o.p.bytes), 0));
    });
    const k = (BOT - TOP - 8) / maxSum;
    const target = new Map();
    cols.forEach((arr, c) => {
      let y = TOP;
      const x = L + c * (BW + GAP);
      arr.forEach(({ p }) => {
        const h = Math.max(2, Math.sqrt(p.bytes) * k);
        const hh = p.bytes ? Math.min(h, h * (p.reclaim + p.reclaim_optional * 0) / p.bytes) : 0;
        target.set(p.id, { x, y, h, hh, p });
        y += h;
      });
    });

    const layer = $("#blocks", svg);
    target.forEach((v, id) => {
      let el = strataEls.get(id);
      if (!el) {
        el = document.createElementNS(SVGNS, "g");
        el.setAttribute("class", "blk" + (animate ? " grow" : ""));
        el.dataset.id = id;
        el.innerHTML = `<rect class="fill"/><rect class="hat" fill="url(#hatch)"/><rect class="edge" fill="none" stroke="#F1ECE2" stroke-width="1"/>`;
        if (animate) el.style.animationDelay = (Math.min(v.x / W, 1) * 500 + Math.random() * 120).toFixed(0) + "ms";
        layer.appendChild(el);
        strataEls.set(id, el);
        el._cur = { x: v.x, y: v.y, h: v.h, hh: v.hh };
      }
      el.querySelector(".fill").setAttribute("fill", COLORS[eff(v.p)] || COLORS.paused);
      el.classList.toggle("dimmed", !!filtering && !matches.has(id));
      el.style.opacity = filtering && !matches.has(id) ? 0.18 : "";
      el.classList.toggle("on", S.hover === id || S.sel === id);
      tweenBlock(el, { x: v.x, y: v.y, h: v.h, hh: v.hh }, BW, fresh ? 0 : 750);
    });
    strataEls.forEach((el, id) => { if (!target.has(id)) { el.remove(); strataEls.delete(id); } });

    // depth scale
    const sc = $("#scale", svg);
    const ref = [1 << 30, 10 * (1 << 30)].filter((b) => Math.sqrt(b) * k < BOT - TOP - 20);
    sc.innerHTML = ref.map((b, i) => {
      const h = Math.sqrt(b) * k, x = W - 46 + i * 22;
      return `<rect x="${x}" y="${TOP}" width="8" height="${h}" fill="none" stroke="#1D1A15" stroke-width="1"/><text class="tick" x="${x + 4}" y="${TOP + h + 12}" text-anchor="middle">${bytes(b).replace(".0", "")}</text>`;
    }).join("");
    strataLayout = { W, k };
  }
  function tweenBlock(el, to, bw, ms) {
    const from = el._cur;
    const set = (s) => {
      const f = el.children[0], hat = el.children[1], edge = el.children[2];
      for (const r of [f, edge]) { r.setAttribute("x", s.x); r.setAttribute("y", s.y); r.setAttribute("width", bw); r.setAttribute("height", s.h); }
      hat.setAttribute("x", s.x); hat.setAttribute("y", s.y); hat.setAttribute("width", bw); hat.setAttribute("height", Math.max(0, s.hh));
      el._cur = s;
    };
    if (!ms || (from.x === to.x && from.y === to.y && from.h === to.h && from.hh === to.hh)) return set(to);
    const t0 = performance.now();
    const ease = (u) => 1 - Math.pow(1 - u, 3);
    const step = (now) => {
      const u = Math.min(1, (now - t0) / ms), e = ease(u);
      set({ x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e, h: from.h + (to.h - from.h) * e, hh: from.hh + (to.hh - from.hh) * e });
      if (u < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function bindStrata() {
    const svg = $("#strata"), tip = $("#tip");
    svg.addEventListener("mousemove", (ev) => {
      const g = ev.target.closest(".blk");
      if (!g) { hideTip(); return; }
      const p = projects().find((x) => x.id === g.dataset.id);
      if (!p) return;
      if (S.hover !== p.id) {
        S.hover = p.id;
        svg.classList.add("hovering");
        svg.querySelectorAll(".blk.on").forEach((b) => b.classList.remove("on"));
        g.classList.add("on");
        tip.innerHTML = `<b>${esc(titleOf(p))}</b> <span class="tipstate">${esc(stateLabel(p))}</span><br><span class="mono">${bytes(p.bytes)} · ${esc(ago(p.activity.last))}</span>`
          + (p.reclaim ? `<br>▨ <span class="mono">${bytes(p.reclaim)}</span> ${esc(t("regenerable").toLowerCase())}` : "")
          + (summaryOf(p) ? `<br><span style="opacity:.75">${esc(summaryOf(p).slice(0, 110))}</span>` : "");
        tip.classList.add("on");
      }
      const box = svg.parentElement.getBoundingClientRect();
      let x = ev.clientX - box.left + 14, y = ev.clientY - box.top + 14;
      if (x + 290 > box.width) x -= 310;
      tip.style.left = x + "px";
      tip.style.top = y + "px";
    });
    svg.addEventListener("mouseleave", hideTip);
    svg.addEventListener("click", (ev) => {
      const g = ev.target.closest(".blk");
      if (g) openDrawer(g.dataset.id);
    });
    function hideTip() {
      S.hover = null;
      svg.classList.remove("hovering");
      svg.querySelectorAll(".blk.on").forEach((b) => { if (b.dataset.id !== S.sel) b.classList.remove("on"); });
      tip.classList.remove("on");
    }
  }

  // ── render: table ────────────────────────────────────────────────────
  function filtered() {
    const qq = S.f.q.trim().toLowerCase();
    return projects().filter((p) => {
      if (S.f.status !== "all" && eff(p) !== S.f.status) return false;
      if (S.f.reclaim && !p.reclaim && !(p.byproducts && p.byproducts.bytes)) return false;
      if (S.f.unsaved && !(p.flags && p.flags.unsaved)) return false;
      if (S.f.pinned && !pinned(p)) return false;
      if (qq) {
        const c = p.claude || {};
        const hay = [titleOf(p), summaryOf(p), noteOf(p), p.name, p.desc, p.path, (p.stack || []).join(" "), c.last_prompt, c.first_prompt,
          ...(c.asks || []).map((a) => a[1]), ...(c.recent || []).map((a) => a[1]), p.resume && p.resume.text, stateLabel(p), kindLabel(p),
          ...(((p.deliverables || {}).recent) || [])].join(" ").toLowerCase();
        if (!hay.includes(qq)) return false;
      }
      return true;
    });
  }
  const ORDER = Object.fromEntries(USER_STATES.map((s, i) => [s, i]));
  function sorted(list) {
    const now = Date.now() / 1000;
    const last = (p) => p.activity.last || 0;
    const by = {
      state: (a, b) => (ORDER[eff(a)] - ORDER[eff(b)]) || last(b) - last(a),
      last: (a, b) => last(b) - last(a),
      stale: (a, b) => (now - last(b)) * Math.sqrt(b.bytes) - (now - last(a)) * Math.sqrt(a.bytes),
      bytes: (a, b) => b.bytes - a.bytes,
      reclaim: (a, b) => (b.reclaim + ((b.byproducts || {}).bytes || 0)) - (a.reclaim + ((a.byproducts || {}).bytes || 0)),
      name: (a, b) => titleOf(a).localeCompare(titleOf(b)),
    }[S.sort] || ((a, b) => last(b) - last(a));
    return list.slice().sort((a, b) => (S.sort === "state" ? 0 : (pinned(b) - pinned(a))) || by(a, b));
  }
  function pulseSvg(p) {
    const w = 108, h = 16;
    let s = `<svg class="pulse" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><line x1="0" x2="${w}" y1="${h - 0.5}" y2="${h - 0.5}" stroke="#1D1A15" stroke-opacity=".25"/>`;
    for (const d of p.pulse || []) {
      const x = w - (d + 1) * 1.2;
      s += `<rect x="${x.toFixed(1)}" y="2" width="1.2" height="${h - 3}" fill="${d < 14 ? COLORS.active : "#1D1A15"}" opacity="${d < 14 ? 1 : 0.55}"/>`;
    }
    return s + "</svg>";
  }
  function triad(p) {
    const a = p.activity, win = a.source;
    const part = (k, sym) => a[k] ? `<span class="${k === win ? "win" : ""}" title="${esc(t("src." + k))}">${sym} ${esc(ago(a[k]))}</span>` : "";
    return `<div class="triad"><span class="big">${esc(ago(a.last))}</span>${[part("commit", "⎇"), part("claude", "✳"), part("files", "▤")].filter(Boolean).join(" · ")}</div>`;
  }
  function chipsHtml(p) {
    const out = [], f = p.flags || {};
    if (f.unsaved) out.push(`<span class="chip unsaved" title="${esc(unsavedText(f.unsaved))}">${esc(t(f.unsaved.no_vcs ? "chips.no_backup" : "chips.unsaved"))}</span>`);
    if (p.chips.includes("shipped")) out.push(`<span class="chip shipped">${esc(t("chips.shipped"))}</span>`);
    if (f.registered && f.registered.length) out.push(`<span class="chip registered" title="${esc(f.registered.map(regLabel).join(", "))}">${esc(t("chips.registered"))}</span>`);
    if ((p.links || {}).site) out.push(`<span class="chip live" title="${esc(p.links.site)}">${esc(t("chips.live"))}</span>`);
    if (p.byproducts && p.byproducts.bytes >= 1048576) out.push(`<span class="chip byp" title="${esc(t("byproducts_hint"))}">${esc(t("chips.byproducts", { size: bytes(p.byproducts.bytes) }))}</span>`);
    p.chips.filter((c) => c === "cloud").forEach((c) => out.push(`<span class="chip ${c}">${esc(t("chips." + c))}</span>`));
    if (p.lineage) out.push(`<span class="chip lineage" title="${esc(t("chips.lineage"))}: ${esc(p.lineage)} ×${p.lineage_size}">${p.lineage_latest ? "★ " + esc(t("chips.latest")) : "⟲ " + esc(p.lineage)}</span>`);
    return `<span class="chips">${out.join("")}</span>`;
  }
  function unsavedText(u) {
    if (!u) return "";
    if (u.no_vcs) return t("unsaved.no_vcs");
    return [u.dirty ? t("unsaved.dirty", { n: u.dirty }) : "", u.unpushed ? t("unsaved.unpushed", { n: u.unpushed }) : "", u.no_remote ? t("unsaved.no_remote") : ""].filter(Boolean).join(" · ");
  }
  // the second line: where it stopped, when that matters
  function stopLine(p) {
    const s = eff(p), c = p.claude || {};
    if (noteOf(p)) return `<span class="note-q">✎ ${esc(noteOf(p))}</span>`;
    if (s === "blocked" && source(p) === "rules") { const w = (p.why || []).find(([, v]) => v && v.q); if (w) return `<span class="blk-q">✕ ${esc(q(w[1].q))}</span>`; }
    if (p.resume && (s === "paused" || s === "blocked")) return `<span class="note-q">↳ ${esc(p.resume.text)}</span>`;
    if (s === "paused" && c.last_prompt) return `<span class="last-q">↳ ${esc(q(c.last_prompt))}</span>`;
    if (s === "active" && c.last_prompt) return `<span class="now-q">● ${esc(t("now_doing"))} ${esc(q(c.last_prompt))}</span>`;
    return "";
  }
  function rowHtml(p, i) {
    const s = eff(p), title = titleOf(p), stop = stopLine(p), sum = summaryOf(p);
    return `<tr data-id="${p.id}" class="${S.sel === p.id ? "sel" : ""} ${S.checked.has(p.id) ? "picked" : ""}" style="animation-delay:${Math.min(i, 30) * 12}ms">
        <td class="pick"><input type="checkbox" data-pick="${p.id}" ${S.checked.has(p.id) ? "checked" : ""} aria-label="${esc(title)}"></td>
        <td><div class="pname">${pinned(p) ? `<span class="pinmark">◆</span>` : ""}<span class="ttl">${esc(title)}</span>${title !== p.name ? `<span class="folder">${esc(p.name)}</span>` : ""} ${chipsHtml(p)}</div>
          <div class="pdesc"><span class="kind">${esc(kindLabel(p))}</span>${sum ? " · " + esc(sum) : ""}</div>
          ${stop ? `<div class="pstop">${stop}</div>` : ""}</td>
        <td>${triad(p)}</td>
        <td class="c-pulse">${pulseSvg(p)}</td>
        <td class="num"><div class="sizebar"><span class="v">${bytes(p.bytes)}</span><span class="bar hatch" title="${esc(t("regen_share", { r: bytes(p.reclaim) }))}"><i style="width:${p.bytes ? (100 * p.reclaim / p.bytes).toFixed(1) : 0}%"></i></span></div></td>
        <td class="c-status"><span class="stat ${source(p)}"><span class="dot" style="background:${COLORS[s]}"></span>${esc(stateLabel(p))}${source(p) !== "rules" ? `<sup title="${esc(t("src_" + source(p)))}">${source(p) === "you" ? "✎" : "✳"}</sup>` : ""}</span></td>
      </tr>`;
  }
  function renderRows() {
    const list = sorted(filtered());
    let html = "", i = 0;
    if (S.sort === "state") {
      for (const [g] of GROUPS) {
        const rows = list.filter((p) => groupOf(eff(p)) === g);
        if (!rows.length) continue;
        html += `<tr class="grp-row"><td colspan="6"><span>${esc(t("group." + g))}</span><span class="n">${rows.length}</span><span class="gh">${esc(t("group_hint." + g))}</span></td></tr>`;
        html += rows.map((p) => rowHtml(p, i++)).join("");
      }
    } else {
      html = list.map((p) => rowHtml(p, i++)).join("");
    }
    $("#rows").innerHTML = html;
    const all = $("#pickAll");
    if (all) { all.checked = list.length > 0 && list.every((p) => S.checked.has(p.id)); all.indeterminate = !all.checked && list.some((p) => S.checked.has(p.id)); }
    const empty = $("#empty");
    empty.hidden = list.length > 0 || !S.data;
    empty.textContent = "—";
    renderSelBar();
  }
  function renderChips() {
    const c = counts();
    const keys = ["all", ...STATES, ...(c.closed ? ["closed"] : [])];
    $("#statusChips").innerHTML = keys.map((k) => `<button data-s="${k}" aria-pressed="${S.f.status === k}" title="${k === "all" ? "" : esc(t("state_hint." + k))}">${k === "all" ? esc(t("all")) : `<span class="dot" style="width:8px;height:8px;border-radius:50%;background:${COLORS[k]}"></span>${esc(t("state." + k))}`}<span class="n">${k === "all" ? projects().length : c[k] || 0}</span></button>`).join("");
  }

  // ── selection: pick projects, then act on all of them ────────────────
  function picked() { return [...S.checked].map(byId).filter(Boolean); }
  function renderSelBar() {
    const el = $("#selbar"), ps = picked();
    el.classList.toggle("on", ps.length > 0);
    if (!ps.length) { el.innerHTML = ""; return; }
    const size = ps.reduce((s, p) => s + p.bytes, 0);
    const regen = ps.reduce((s, p) => s + p.reclaim, 0);
    const byp = ps.reduce((s, p) => s + ((p.byproducts || {}).safe_bytes || 0), 0);
    el.innerHTML = `<span class="sum"><b>${esc(t("sel_n", { n: ps.length }))}</b> · ${bytes(size)}</span>
      <span class="grp-btns">
        <button class="btn" data-sel="regen" ${regen ? "" : "disabled"}>${esc(t("sel_regen", { size: bytes(regen) }))}</button>
        <button class="btn" data-sel="byp" ${byp ? "" : "disabled"}>${esc(t("sel_byp", { size: bytes(byp) }))}</button>
        <button class="btn danger" data-sel="whole">${esc(t("sel_whole", { size: bytes(size) }))}</button>
      </span>
      <select class="mini" data-sel="state"><option value="">${esc(t("sel_state"))}</option>${USER_STATES.map((x) => `<option value="${x}">${esc(t("state." + x))}</option>`).join("")}<option value="__auto">${esc(t("auto_state"))}</option></select>
      <button class="btn ghost" data-sel="pin">◇ ${esc(t("pin"))}</button>
      <button class="btn ghost" data-sel="clear">${esc(t("sel_clear"))}</button>`;
  }
  function itemsFor(ps, mode) {
    const out = [], skipped = [];
    ps.forEach((p) => {
      if (mode === "whole") {
        if (projects().some((o) => o.path.startsWith(p.path + "/"))) { skipped.push({ p, why: t("reasons.contains-projects") }); return; }
        out.push({ p, project: p.path, path: p.path, kind: "project", bytes: p.bytes, rel: "" });
      } else if (mode === "regen") {
        p.candidates.filter((c) => c.tier === 1 && c.bytes > 0).forEach((c) => out.push({ p, project: p.path, path: c.path, kind: "candidate", bytes: c.bytes, rel: c.rel }));
      } else if (mode === "byp") {
        const bi = (p.byproducts || {}).items || [];
        bi.filter((b) => b.auto && !b.blocked).forEach((b) => out.push({ p, project: p.path, path: b.path, kind: b.kind, bytes: b.bytes, rel: b.rel }));
        const held = bi.filter((b) => !b.auto && !b.blocked);
        if (held.length) skipped.push({ p, why: t("byp_held_n", { n: held.length }) });
      }
    });
    return { items: out, skipped };
  }
  function warningsFor(p, mode) {
    const w = [], f = p.flags || {}, s = eff(p);
    if (mode !== "whole") return w;
    if (f.unsaved) w.push(f.unsaved.no_vcs ? t("warn.only_copy") : t("warn.unsaved", { what: unsavedText(f.unsaved) }));
    if (f.registered && f.registered.length) w.push(t("warn.registered", { what: f.registered.map(regLabel).join(", ") }));
    if (s === "active" || s === "in-use") w.push(t("warn.state", { s: t("state." + s) }));
    if (pinned(p)) w.push(t("warn.pinned"));
    return w;
  }
  function openRemove(ps, mode) {
    const { items, skipped } = itemsFor(ps, mode);
    const total = items.reduce((s, i) => s + i.bytes, 0);
    const per = new Map();
    items.forEach((i) => { if (!per.has(i.p.id)) per.set(i.p.id, []); per.get(i.p.id).push(i); });
    let warned = false;
    const rows = [...per.values()].map((its) => {
      const p = its[0].p, ws = warningsFor(p, mode);
      if (ws.length) warned = true;
      return `<div class="rm-proj"><div class="rm-head"><b>${esc(titleOf(p))}</b> <span class="mono dim">${esc(p.name)}</span><span class="mono">${bytes(its.reduce((s, i) => s + i.bytes, 0))}</span></div>
        ${mode === "whole" ? "" : `<div class="rm-items mono">${its.map((i) => esc(i.rel)).join(" · ")}</div>`}
        ${ws.map((x) => `<div class="rm-warn">⚠ ${esc(x)}</div>`).join("")}</div>`;
    }).join("");
    $("#modalCard").style.width = "min(720px, 94vw)";
    $("#modalCard").innerHTML = `
      <header><h3>${esc(t("rm_title." + mode, { n: per.size }))}</h3><p>${esc(t("rm_body"))}</p></header>
      <div class="list rm">${rows || `<div>${esc(t("bulk_none"))}</div>`}
        ${skipped.map((s) => `<div class="rm-skip">— ${esc(titleOf(s.p))}: ${esc(s.why)}</div>`).join("")}</div>
      <footer>${warned ? `<label class="toggle" style="margin-right:auto"><input type="checkbox" id="rmAck"><span>${esc(t("rm_ack"))}</span></label>` : `<span style="margin-right:auto"></span>`}
        <button class="btn" data-m="cancel">${esc(t("cancel"))}</button>
        <button class="btn solid danger" data-m="go" ${items.length && !warned ? "" : "disabled"}>${esc(t("rm_go", { size: bytes(total) }))}</button></footer>`;
    const ack = $("#rmAck");
    if (ack) ack.onchange = () => { $('#modalCard [data-m="go"]').disabled = !ack.checked || !items.length; };
    $("#modal").classList.add("on");
    $("#modalCard").onclick = async (ev) => {
      const b = ev.target.closest("button");
      if (!b) return;
      if (b.dataset.m === "cancel") return closeModal();
      if (b.dataset.m !== "go") return;
      b.disabled = true;
      b.textContent = "…";
      await doRemove(items.map(({ project, path, kind, bytes: by }) => ({ project, path, kind, bytes: by })), t("rm_label." + mode));
      closeModal();
    };
  }
  async function doRemove(items, lbl) {
    if (!items.length) return;
    try {
      const j = await api("/api/remove", { items, label: lbl });
      mergeProjects(j.projects || [], j.totals, j.gone);
      if (S.sel && !byId(S.sel)) closeDrawer();
      const moved = (j.op.items || []).filter((i) => i.state === "moved");
      const size = moved.reduce((s, i) => s + (i.bytes || 0), 0);
      let msg = t(j.op.to === "trash" ? "trashed_toast" : "moved_toast", { n: moved.length, size: bytes(size) });
      if (j.op.skipped && j.op.skipped.length) {
        const why = [...new Set(j.op.skipped.map((s) => t("reasons." + s.reason)))].join(", ");
        msg += "  " + t("skipped_toast", { n: j.op.skipped.length, why });
      }
      S.picks.clear();
      await loadOps();
      renderAll(false);
      if (S.sel) renderDrawer();
      toast(msg, moved.length ? { label: t("undo"), fn: () => undoOp(j.op.id) } : null);
    } catch (e) {
      toast(t("error") + ": " + e.message);
    }
  }
  async function undoOp(id) {
    try {
      const j = await api("/api/restore", { id });
      mergeProjects(j.projects || [], j.totals);
      toast(t("restored_toast", { n: j.op.items.filter((i) => i.state === "restored").length }));
      await loadOps();
      renderAll(false);
      if (S.sel) renderDrawer();
    } catch (e) { toast(t("error") + ": " + e.message); }
  }
  async function setState(ids, status) {
    let j;
    for (const id of ids) j = await api("/api/override", { id, status: status || null });
    if (j) S.state = j.state;
  }

  // ── drawer: what it is, where it stands, what you can do ─────────────
  function openDrawer(id) {
    S.sel = id;
    const p = byId(id);
    if (!p) return;
    const plan = planFor(p);
    S.picks = new Set([
      ...(plan.regen ? p.candidates.filter((c) => c.tier === 1).map((c) => c.path) : []),
      ...(plan.byproducts ? ((p.byproducts || {}).items || []).filter((b) => b.auto).map((b) => b.path) : []),
    ]);
    S.editing = false;
    renderDrawer();
    $("#drawer").classList.add("on");
    $("#drawer").setAttribute("aria-hidden", "false");
    $("#scrim").classList.add("on");
    document.querySelectorAll("#rows tr").forEach((tr) => tr.classList.toggle("sel", tr.dataset.id === id));
  }
  function closeDrawer() {
    S.sel = null;
    $("#drawer").classList.remove("on");
    $("#drawer").setAttribute("aria-hidden", "true");
    $("#scrim").classList.remove("on");
    document.querySelectorAll("#rows tr.sel").forEach((tr) => tr.classList.remove("sel"));
  }
  // a few plain sentences: what happened, where it stopped, what's at stake
  function brief(p) {
    const c = p.claude, s = eff(p), f = p.flags || {}, out = [];
    const why = Object.fromEntries((p.why || []).map(([k, v]) => [k, v || {}]));
    if (c && c.meaningful) out.push(t("brief.ai", { start: dateStr(c.p_first || c.first), end: dateStr(c.p_last || c.last), n: c.meaningful, s: c.sessions }));
    else out.push(t("brief.files", { end: dateStr(p.activity.last) }));
    const mine = source(p) !== "rules";
    if (s === "active") out.push(t("brief.active"));
    else if (s === "in-use") out.push(why.tool || why.used ? t(why.tool || (why.used && why.used.src) ? "brief.tool" : "brief.used", { src: regLabel((why.tool || why.used).src || p.tool || "") }) : t("brief.in_use"));
    else if (s === "blocked" && !mine && why.complaint) out.push(t("brief.blocked", { q: q(why.complaint.q) }));
    else if (s === "blocked" && !mine && why.report) out.push(t("brief.blocked", { q: q(why.report.q) }));
    else if (s === "blocked" && !mine && why.wait) out.push(t("brief.wait", { q: q(why.wait.q) }));
    else if (s === "delivered" && !mine && why.handoff) out.push(t("brief.handoff", { q: q(why.handoff.q) }));
    else if (s === "delivered" && !mine && why.shipped) out.push(t("brief.shipped"));
    else if (s === "delivered" && !mine && why["quick-deliverable"]) out.push(t("brief.quick"));
    else if (s === "superseded" && why.sibling) out.push(t("brief.sibling", { n: why.sibling.n }));
    else if (s === "one-shot" && why["one-shot"]) out.push(t("brief.oneshot", { n: why["one-shot"].n }));
    if (["paused", "blocked", "one-shot", "closed"].includes(s)) {
      if (p.resume) out.push(t("brief.note", { f: p.resume.file, text: p.resume.text }));
      else if (c && c.last_prompt && !(s === "blocked" && !mine)) out.push(t("brief.last", { q: q(c.last_prompt) }));
      if (p.days != null) out.push(t(p.stale ? "brief.forgotten" : "brief.idle", { d: Math.round(p.days) }));
    }
    const dl = p.deliverables || {};
    if (dl.count) out.push(t("brief.outputs", { n: dl.count, f: (dl.recent || [""])[0].split("/").pop() }));
    if (f.unsaved) out.push(f.unsaved.no_vcs ? t("brief.no_vcs") : t("brief.unsaved", { what: unsavedText(f.unsaved) }));
    const toolSrc = s === "in-use" && ((why.tool || why.used || {}).src || "");
    const regs = (f.registered || []).filter((x) => x !== toolSrc);
    if (regs.length) out.push(t("brief.registered", { what: regs.map(regLabel).join(", ") }));
    // sessions opened in another folder that worked here, or opened here that worked elsewhere
    const L = p.ai_links || {};
    const folder = (x) => { const o = projects().find((q) => q.path === x); return o ? titleOf(o) : (S.data && S.data.home === x ? "~" : x.split("/").filter(Boolean).pop() || x); };
    const linked = (rows) => ({ names: rows.slice(0, 3).map(([x]) => folder(x)).join(", "), n: rows.reduce((sum, [, k]) => sum + k, 0) });
    if (L.from && L.from.length) { const v = linked(L.from); out.push(t("brief.from", { f: v.names, n: v.n })); }
    if (L.to && L.to.length) { const v = linked(L.to); out.push(t("brief.to", { to: v.names, n: v.n })); }
    const byp = (p.byproducts || {}).bytes || 0;
    out.push(t(p.reclaim ? "brief.size" : "brief.size_only", { t: bytes(p.bytes), r: bytes(p.reclaim) }) + (byp ? t("brief.size_byp", { b: bytes(byp) }) : "") + t("brief.end"));
    return out;
  }
  function primary(p) {
    const s = eff(p), c = p.claude || {}, sess = c.session || {};
    if (["delivered", "superseded", "in-use", "closed"].includes(s) || !c.meaningful) return { how: "claude-new", label: t(c.meaningful ? "act.revisit" : "act.look") };
    if (sess.source === "codex") return { how: "codex", label: t("act.resume_codex") };
    return { how: "claude", label: t("act.resume") };
  }
  function renderDrawer() {
    const p = byId(S.sel);
    if (!p) return closeDrawer();
    const c = p.claude, g = p.git, a = p.activity, f = p.flags || {};
    const s = eff(p), plan = planFor(p), lab = label(p), L = p.links || {};
    const over = USER_STATES.includes((S.state.overrides || {})[p.id]) ? S.state.overrides[p.id] : "";
    const comp = [["source", p.source_bytes], ["git", p.git_bytes], ...Object.entries(p.by_category || {})].filter(([, v]) => v > 0);
    const compTotal = comp.reduce((sum, [, v]) => sum + v, 0) || 1;
    const reasonText = (r) => t("reasons." + r);
    const pr = primary(p);

    const note = p.resume ? `<div class="note"><div class="lbl">${esc(t("resume_note"))} · <span class="mono">${esc(p.resume.file)}</span> · ${esc(ago(p.resume.ts))}</div><p>${esc(p.resume.text)}</p></div>` : "";
    const recent = c && c.recent && c.recent.length > 1 ? `<details class="recent"><summary>${esc(t("recent_prompts", { n: c.recent.length }))}</summary><ol>${c.recent.slice().reverse().map(([ts, txt]) => `<li><span class="mono dim">${esc(ago(ts))}</span> ${esc(txt)}</li>`).join("")}</ol></details>` : "";
    const srcs = c && c.sources ? Object.entries(c.sources).map(([k, v]) => `${k} ${v}`).join(" · ") : "";
    const ask = p.ask || (c && c.first_prompt) || "";
    const left = c ? `
      ${ask ? `<div class="quote"><div class="lbl">${esc(t("first_prompt"))} · ${esc(dateStr(c.p_first || c.first))}</div><p>${esc(q(ask))}</p></div>` : ""}
      ${c.last_prompt && c.last_prompt !== ask ? `<div class="quote last"><div class="lbl">${esc(t("last_prompt"))} · ${esc(dateStr(c.p_last || c.last))}</div><p>${esc(q(c.last_prompt))}</p></div>` : ""}
      ${note}${recent}
      <div class="dim mono">${esc(t("prompts_n", { p: c.prompts, s: c.sessions }))}${srcs ? " · " + esc(srcs) : ""}</div>` : `${note}<div class="dim">${esc(t("no_claude"))}</div>`;

    const gitHtml = g ? `<div class="facts">
        <div><div class="k">${esc(t("branch"))}</div><div class="v">${esc(g.branch || "—")}</div></div>
        <div><div class="k">${esc(t("dirty"))}</div><div class="v ${g.dirty ? "warn" : ""}">${g.dirty}</div></div>
        <div><div class="k">${esc(t("unpushed"))}</div><div class="v ${g.unpushed ? "warn" : ""}">${g.remote ? g.unpushed : esc(t("no_remote"))}</div></div>
        <div><div class="k">${esc(t("commits"))}</div><div class="v">${g.commits}</div></div>
        <div style="grid-column: span 2"><div class="k">⎇ ${esc(ago(g.last_commit))}</div><div class="v" style="font-size:12.5px">${esc(g.last_subject || "—")}</div></div>
      </div>` : `<div class="dim">${esc(t("no_git"))}</div>`;

    const cand = p.candidates.length ? `<ul class="items">${p.candidates.map((x) => {
      const tierCls = x.tier === 1 ? "t1" : x.tier === 2 ? "t2" : "t0";
      const tierLab = x.tier === 1 ? t("tier1") : x.tier === 2 ? t("tier2") : t("blocked");
      return `<li class="${x.tier ? "" : "blocked"}"><input type="checkbox" data-path="${esc(x.path)}" data-kind="candidate" data-bytes="${x.bytes}" ${S.picks.has(x.path) ? "checked" : ""} ${x.tier ? "" : "disabled"}>
        <div><div class="rel">${esc(x.rel)}<span class="tier ${tierCls}">${esc(tierLab)}</span></div><div class="why">${esc(x.tier ? x.proof : reasonText(x.reason))}</div></div>
        <span class="sz">${bytes(x.bytes)}</span></li>`;
    }).join("")}</ul>` : `<div class="dim">—</div>`;

    const bi = (p.byproducts || {}).items || [];
    const bypHint = !bi.length ? "" : plan.byproducts ? t("byp_pre") : f.unsaved ? t("byp_gate", { why: unsavedText(f.unsaved) }) : t("byp_state", { s: t("state." + s) });
    const byp = bi.length ? `<div class="dim" style="font-size:12px;margin-bottom:6px">${esc(bypHint)}</div><ul class="items">${bi.map((b) => `
      <li class="${b.blocked ? "blocked" : ""}"><input type="checkbox" data-path="${esc(b.path)}" data-kind="${esc(b.kind)}" data-bytes="${b.bytes}" ${S.picks.has(b.path) ? "checked" : ""} ${b.blocked ? "disabled" : ""}>
        <div><div class="rel">${esc(b.rel)}${b.kind === "workspace" ? "/" : ""}<span class="tier byp">${esc(t("byp_kind." + b.why))}</span></div>
        <div class="why">${b.blocked ? esc(reasonText(b.blocked)) : b.why === "older-version" ? esc(t("byp_keep", { f: b.keep })) : esc(t("byp_ws", { n: b.files }))}${b.holds ? ` · <b class="warn">${esc(t("byp_holds", { n: b.holds, f: (b.held || []).join(", ") }))}</b>` : ""}${b.code ? ` · <b class="warn">${esc(t("byp_code", { n: b.code }))}</b>` : ""}${b.referenced ? ` · <b class="warn">${esc(t("byp_ref"))}</b>` : ""}</div></div>
        <span class="sz">${bytes(b.bytes)}</span></li>`).join("")}</ul>${bi.some((b) => !b.blocked && !S.picks.has(b.path)) ? `<button class="btn sm" data-act="pickbyp" style="margin-top:8px">${esc(t("byp_select"))}</button>` : ""}` : `<div class="dim">${esc(t("byp_none"))}</div>`;

    const big = p.big.length ? `<div class="dim" style="font-size:12px;margin-bottom:6px">${esc(t("heavy_hint"))}</div><ul class="items">${p.big.map((b) => `
      <li><input type="checkbox" data-path="${esc(b.path)}" data-kind="file" data-bytes="${b.bytes}" ${S.picks.has(b.path) ? "checked" : ""}>
        <div><div class="rel">${esc(b.rel)}</div><div class="why">${esc(b.kind)} · ${esc(ago(b.mtime))}${b.sprawl ? ` · <b>${esc(t("sprawl"))}</b>` : ""}</div></div>
        <span class="sz">${bytes(b.bytes)}</span></li>`).join("")}</ul>` : `<div class="dim">—</div>`;

    const dl = p.deliverables || {};
    const triage = lab.state ? `<div class="infobox triage"><b>✳ ${esc(t("triage_title"))}</b> ${esc(t("state." + (LEGACY[lab.state] || lab.state)))} · ${esc(t("conf." + (lab.confidence || "low")))}${lab.why ? ` — ${esc(lab.why)}` : ""}${lab.resume_note ? `<br><span class="dim">${esc(t("resume_note"))}:</span> ${esc(lab.resume_note)}` : ""}</div>` : "";
    const title = titleOf(p), sum = summaryOf(p), nm = named(p);
    const head = S.editing ? `
        <div class="edit">
          <input class="ttl-in" id="edTitle" maxlength="200" value="${esc(nm.title || title)}" placeholder="${esc(p.title || p.name)}">
          <input class="sum-in" id="edSum" maxlength="400" value="${esc(nm.summary || sum)}" placeholder="${esc(t("summary_ph"))}">
          <div class="row"><button class="btn sm solid" data-act="save-name">${esc(t("save"))}</button>
            ${nm.title || nm.summary ? `<button class="btn sm" data-act="reset-name">${esc(t("auto_name"))}</button>` : ""}
            <button class="btn sm ghost" data-act="cancel-name">${esc(t("cancel"))}</button></div></div>`
      : `<div><h3>${esc(title)} <button class="btn ghost sm edit-btn" data-act="edit-name" title="${esc(t("rename"))}">✎</button></h3>
          ${sum ? `<p class="summary">${esc(sum)}</p>` : ""}
          <div class="path">${esc(p.path)} · ${esc(kindLabel(p))}</div></div>`;

    $("#drawer").innerHTML = `
      <header>
        <div class="top">${head}<button class="btn ghost x" data-act="close" aria-label="close">✕</button></div>
        <div class="stateline"><span class="stat big"><span class="dot" style="background:${COLORS[s]}"></span>${esc(stateLabel(p))}</span>
          <span class="whytext">${esc(whyText(p))}</span></div>
        <div class="row">
          <button class="btn sm solid" data-open="${pr.how}">✳ ${esc(pr.label)}</button>
          <button class="btn sm" data-open="finder">${esc(t("open_finder"))}</button>
          <button class="btn sm" data-open="terminal">${esc(t("open_terminal"))}</button>
          ${dl.count ? `<button class="btn sm" data-file="${esc((dl.recent || [""])[0])}">${esc(t("open_output"))}</button>` : ""}
          ${L.repo ? `<a class="btn sm" href="${esc(L.repo)}" target="_blank" rel="noopener">GitHub ↗</a>` : ""}
          ${L.site ? `<a class="btn sm" href="${esc(L.site)}" target="_blank" rel="noopener">${esc(t("open_site"))} ↗</a>` : ""}
          <span style="flex:1"></span>
          <button class="btn sm ghost" data-act="pin">${pinned(p) ? "◆ " + esc(t("unpin")) : "◇ " + esc(t("pin"))}</button>
          <select class="mini" data-act="override" title="${esc(t("override"))}">
            <option value="">${esc(t("override"))}: ${esc(t("auto"))} (${esc(t("state." + (lab.state ? (LEGACY[lab.state] || lab.state) : p.state || "paused")))})</option>
            ${USER_STATES.map((x) => `<option value="${x}" ${over === x ? "selected" : ""}>${esc(t("state." + x))}</option>`).join("")}
          </select>
        </div>
      </header>
      <div class="body">
        <div class="brief">${brief(p).map((x) => `<p>${esc(x)}</p>`).join("")}</div>
        ${triage}
        <h4><span>${esc(t("memo"))}</span><span class="dim" id="memoSaved"></span></h4>
        <textarea class="memo" id="memo" rows="2" maxlength="2000" placeholder="${esc(t("memo_ph"))}">${esc(noteOf(p))}</textarea>
        <h4><span>${esc(t("left_off"))}</span><span>${esc(t("src." + (a.source || "files")))} · ${esc(ago(a.last))}</span></h4>
        ${left}
        ${dl.count ? `<h4><span>${esc(t("deliverables"))}</span><span>${dl.count} · ${esc(ago(dl.newest))}</span></h4><div class="dl">${(dl.recent || []).map((r) => `<button class="linkish mono" data-file="${esc(r)}">${esc(r)}</button>`).join("")}</div>` : ""}
        <h4><span>${esc(t("git"))}</span></h4>
        ${gitHtml}
        <h4><span>${esc(t("composition"))}</span><span>${bytes(p.bytes)} · ${esc(t("files_n", { n: p.files.toLocaleString() }))}</span></h4>
        <div class="comp">${comp.map(([k, v]) => `<i title="${esc(t("comp." + k))} ${bytes(v)}" style="width:${(100 * v / compTotal).toFixed(2)}%;background:${COMP_COLORS[k] || "#999"}"></i>`).join("")}</div>
        <div class="comp-legend">${comp.map(([k, v]) => `<span><i class="sw" style="background:${COMP_COLORS[k] || "#999"}"></i>${esc(t("comp." + k))} ${bytes(v)}</span>`).join("")}</div>
        <h4><span>${esc(t("regenerable"))}</span><span>${bytes(p.reclaim)} + ${bytes(p.reclaim_optional)}</span></h4>
        ${cand}
        <h4><span>${esc(t("byproducts"))}</span><span>${bytes((p.byproducts || {}).bytes || 0)}</span></h4>
        ${byp}
        <h4><span>${esc(t("heavy_files"))}</span><span>${p.big_count} · ${bytes(p.big_total)}</span></h4>
        ${big}
        <div class="danger-zone"><button class="btn danger" data-act="trash-project">${esc(t("trash_project", { size: bytes(p.bytes) }))}</button>
          <span class="dim">${esc(t("trash_project_hint"))}</span></div>
      </div>
      <div class="actionbar"><span class="sum" id="pickSum"></span><button class="btn solid" data-act="move" id="moveBtn"></button></div>`;
    updatePickSum();
  }
  function updatePickSum() {
    const boxes = [...document.querySelectorAll('#drawer .items input[type="checkbox"]:checked')];
    const total = boxes.reduce((s, b) => s + Number(b.dataset.bytes || 0), 0);
    const btn = $("#moveBtn");
    if (!btn) return;
    btn.disabled = !boxes.length;
    btn.textContent = boxes.length ? t("move_selected", { size: bytes(total) }) : t("nothing_selected");
    $("#pickSum").textContent = boxes.length ? `✓ ${boxes.length}` : "";
  }
  async function saveMemo(p, text) {
    try {
      const j = await api("/api/meta", { id: p.id, note: text });
      S.state = j.state;
      const el = $("#memoSaved");
      if (el) el.textContent = t("saved");
      renderRows();
    } catch (e) { toast(t("error") + ": " + e.message); }
  }
  function bindDrawer() {
    const d = $("#drawer");
    d.addEventListener("change", async (ev) => {
      const el = ev.target;
      if (el.matches('.items input[type="checkbox"]')) {
        el.checked ? S.picks.add(el.dataset.path) : S.picks.delete(el.dataset.path);
        updatePickSum();
      } else if (el.dataset.act === "override") {
        await setState([S.sel], el.value || null);
        renderAll(false);
        renderDrawer();
      } else if (el.id === "memo") {
        const p = byId(S.sel);
        if (p && el.value.trim() !== noteOf(p)) saveMemo(p, el.value.trim());
      }
    });
    d.addEventListener("keydown", (ev) => {
      if (S.editing && ev.key === "Enter" && ev.target.matches(".ttl-in, .sum-in")) { ev.preventDefault(); d.querySelector('[data-act="save-name"]').click(); }
    });
    d.addEventListener("click", async (ev) => {
      const b = ev.target.closest("button");
      if (!b) return;
      const p = byId(S.sel);
      if (b.dataset.act === "close") return closeDrawer();
      if (b.dataset.act === "edit-name") { S.editing = true; renderDrawer(); const i = $("#edTitle"); if (i) { i.focus(); i.select(); } return; }
      if (b.dataset.act === "cancel-name") { S.editing = false; return renderDrawer(); }
      if (b.dataset.act === "save-name" || b.dataset.act === "reset-name") {
        const reset = b.dataset.act === "reset-name";
        const ti = reset ? "" : $("#edTitle").value.trim(), su = reset ? "" : $("#edSum").value.trim();
        try {
          const j = await api("/api/meta", { id: p.id, title: ti === (label(p).title || p.title || p.name) ? "" : ti, summary: su === (label(p).summary || p.summary || "") ? "" : su });
          S.state = j.state;
        } catch (e) { return toast(t("error") + ": " + e.message); }
        S.editing = false;
        renderAll(false);
        return renderDrawer();
      }
      if (b.dataset.act === "pickbyp") {
        ((p.byproducts || {}).items || []).filter((x) => !x.blocked).forEach((x) => S.picks.add(x.path));
        return renderDrawer();
      }
      if (b.dataset.open || b.dataset.file) {
        try { await api("/api/open", { path: p.path, how: b.dataset.file ? "file" : b.dataset.open, rel: b.dataset.file || "", lang: S.lang }); } catch (e) { toast(t("error") + ": " + e.message); }
        return;
      }
      if (b.dataset.act === "pin") {
        const j = await api("/api/pin", { id: p.id, pinned: !pinned(p) });
        S.state = j.state;
        renderAll(false);
        return renderDrawer();
      }
      if (b.dataset.act === "trash-project") return openRemove([p], "whole");
      if (b.dataset.act === "move") {
        const items = [...d.querySelectorAll('.items input[type="checkbox"]:checked')].map((x) => ({ project: p.path, path: x.dataset.path, kind: x.dataset.kind, bytes: Number(x.dataset.bytes) }));
        await doRemove(items, titleOf(p));
      }
    });
    $("#scrim").addEventListener("click", closeDrawer);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") { if (S.editing) { S.editing = false; return renderDrawer(); } closeDrawer(); closeModal(); } });
  }

  // ── share card ───────────────────────────────────────────────────────
  function drawCard(cv, showNames) {
    const W = 1200, H = 630, ctx = cv.getContext("2d");
    cv.width = W * 2; cv.height = H * 2; ctx.setTransform(2, 0, 0, 2, 0, 0);
    const serif = getComputedStyle(document.documentElement).getPropertyValue("--serif");
    const mono = getComputedStyle(document.documentElement).getPropertyValue("--mono");
    const sans = getComputedStyle(document.documentElement).getPropertyValue("--sans");
    const T = S.data.totals, th = S.data.thresholds, list = projects(), now = Date.now() / 1000, c = counts(), m = spanMonths();
    ctx.fillStyle = "#F1ECE2"; ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = "#1D1A15"; ctx.lineWidth = 3; ctx.strokeRect(28, 28, W - 56, H - 56);
    ctx.lineWidth = 1; ctx.strokeRect(36, 36, W - 72, H - 72);
    // headline
    ctx.fillStyle = "#1D1A15";
    ctx.font = `italic 600 30px ${serif}`; ctx.fillText("vibestrata", 64, 92);
    ctx.font = `500 13px ${mono}`; ctx.fillStyle = "#4A443A";
    ctx.fillText(t("card_foot").toUpperCase() + " · " + new Date().toISOString().slice(0, 10), 236, 90);
    ctx.fillStyle = "#1D1A15"; ctx.font = `600 50px ${serif}`;
    ctx.fillText(t(m ? "card_head" : "card_head_nospan", { p: T.projects, m }), 64, 160);
    ctx.font = `400 25px ${serif}`; ctx.fillStyle = "#C2461F";
    ctx.fillText(t("card_sub", { a: c.active || 0, d: (c.delivered || 0) + (c["in-use"] || 0), b: c.blocked || 0, h: c.paused || 0 }), 64, 200);
    // strata
    const L = 64, R = W - 64, TOP = 250, BOT = H - 96, MAXD = 730;
    const xOf = (d) => L + (R - L) * Math.log1p(Math.min(Math.max(d, 0), MAXD)) / Math.log1p(MAXD);
    const bands = [[0, th.active, "b0"], [th.active, th.cooling, "b1"], [th.cooling, th.dormant, "b2"], [th.dormant, MAXD, "b3"]];
    bands.forEach(([a, b, s], i) => {
      const x0 = xOf(a), x1 = i === 3 ? R : xOf(b);
      ctx.globalAlpha = 0.02 + i * 0.02; ctx.fillStyle = "#1D1A15"; ctx.fillRect(x0, TOP, x1 - x0, BOT - TOP); ctx.globalAlpha = 1;
      ctx.fillStyle = "#4A443A"; ctx.font = `500 11px ${mono}`;
      ctx.fillText(t("band." + s, { a: th.active, c: th.cooling, d: th.dormant }).toUpperCase(), x0 + 6, TOP - 10);
    });
    ctx.fillStyle = "#1D1A15"; ctx.fillRect(L, TOP, R - L, 2);
    const hatch = document.createElement("canvas"); hatch.width = hatch.height = 6;
    const hc = hatch.getContext("2d"); hc.fillStyle = "rgba(241,236,226,.6)"; hc.fillRect(0, 0, 6, 6);
    hc.strokeStyle = "#1D1A15"; hc.lineWidth = 1.4; hc.beginPath(); hc.moveTo(0, 6); hc.lineTo(6, 0); hc.moveTo(-1, 1); hc.lineTo(1, -1); hc.moveTo(5, 7); hc.lineTo(7, 5); hc.stroke();
    const pat = ctx.createPattern(hatch, "repeat");
    const BW = 9, GAP = 2, cols = new Map();
    list.forEach((p) => {
      const d = p.activity.last ? (now - p.activity.last) / DAY : MAXD;
      const c = Math.floor((xOf(d) - L) / (BW + GAP));
      if (!cols.has(c)) cols.set(c, []);
      cols.get(c).push({ p, d });
    });
    let maxSum = 1;
    cols.forEach((arr) => { arr.sort((a, b) => a.d - b.d); maxSum = Math.max(maxSum, arr.reduce((s, o) => s + Math.sqrt(o.p.bytes), 0)); });
    const k = (BOT - TOP - 6) / maxSum, labels = [];
    cols.forEach((arr, c) => {
      let y = TOP + 2; const x = L + c * (BW + GAP);
      arr.forEach(({ p }) => {
        const h = Math.max(2, Math.sqrt(p.bytes) * k), hh = p.bytes ? h * p.reclaim / p.bytes : 0;
        ctx.fillStyle = COLORS[eff(p)] || COLORS.paused; ctx.fillRect(x, y, BW, h);
        if (hh > 0.5) { ctx.fillStyle = pat; ctx.fillRect(x, y, BW, hh); }
        ctx.fillStyle = "#F1ECE2"; ctx.fillRect(x, y + h - 1, BW, 1);
        labels.push({ p, x, y, h });
        y += h;
      });
    });
    if (showNames) {
      // label the biggest blocks, below each block, skipping any that would collide
      ctx.font = `500 12px ${mono}`;
      const placed = [];
      labels.sort((a, b) => b.p.bytes - a.p.bytes).slice(0, 10).forEach(({ p, x, y, h }) => {
        const nm = titleOf(p), txt = nm.length > 20 ? nm.slice(0, 19) + "…" : nm;
        const w = ctx.measureText(txt).width + 8;
        const bx = Math.min(Math.max(x - 4, L), R - w), by = Math.min(y + h + 4, BOT - 18);
        const box = { x: bx, y: by, w, h: 18 };
        const hit = placed.some((q) => box.x < q.x + q.w + 4 && q.x < box.x + box.w + 4 && box.y < q.y + q.h && q.y < box.y + box.h);
        if (hit || placed.length >= 6) return;
        placed.push(box);
        ctx.fillStyle = "rgba(241,236,226,.92)"; ctx.fillRect(box.x, box.y, box.w, box.h);
        ctx.strokeStyle = "#1D1A15"; ctx.lineWidth = 0.8; ctx.strokeRect(box.x + 0.5, box.y + 0.5, box.w - 1, box.h - 1);
        ctx.fillStyle = "#1D1A15"; ctx.fillText(txt, box.x + 4, box.y + 13);
      });
    }
    // footer stats

    let fx = 64; const fy = H - 58;
    ctx.font = `500 13px ${mono}`;
    USER_STATES.filter((s) => c[s]).forEach((s) => {
      ctx.fillStyle = COLORS[s]; ctx.fillRect(fx, fy - 11, 12, 12);
      ctx.fillStyle = "#1D1A15"; const txt = `${t("state." + s)} ${c[s] || 0}`;
      ctx.fillText(txt, fx + 17, fy); fx += ctx.measureText(txt).width + 30;
    });
    ctx.fillStyle = "#7C7466"; ctx.font = `500 13px ${mono}`; ctx.textAlign = "right";
    ctx.fillText("github.com/epoko77-ai/vibestrata", W - 64, fy); ctx.textAlign = "left";
  }

  function openShare() {
    if (!S.data) return;
    $("#modalCard").innerHTML = `
      <header><h3>${esc(t("share_title"))}</h3><p>${esc(t("share_body"))}</p></header>
      <div style="padding:14px 24px 0"><canvas id="card" style="width:100%;border:1px solid var(--rule-strong)"></canvas></div>
      <footer><label class="toggle" style="margin-right:auto"><input type="checkbox" id="cardNames"><span>${esc(t("share_names"))}</span></label>
        <button class="btn" data-m="copy">${esc(t("share_copy"))}</button>
        <button class="btn solid" data-m="dl">${esc(t("share_download"))}</button></footer>`;
    $("#modalCard").style.width = "min(880px, 94vw)";
    const cv = $("#card");
    drawCard(cv, false);
    $("#cardNames").onchange = (e) => drawCard(cv, e.target.checked);
    $("#modal").classList.add("on");
    $("#modalCard").onclick = async (ev) => {
      const b = ev.target.closest("button");
      if (!b) return;
      if (b.dataset.m === "dl") {
        const a = document.createElement("a");
        a.download = `vibestrata-${new Date().toISOString().slice(0, 10)}.png`;
        a.href = cv.toDataURL("image/png");
        a.click();
      } else if (b.dataset.m === "copy") {
        try {
          const blob = await new Promise((r) => cv.toBlob(r, "image/png"));
          await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
          toast(t("share_copied"));
        } catch (e) { toast(t("error") + ": " + e.message); }
      }
    };
  }
  function closeModal() { $("#modal").classList.remove("on"); }

  // ── field log ────────────────────────────────────────────────────────
  function renderLog() {
    const el = $("#log");
    if (!S.ops.length) { el.innerHTML = `<div class="empty" style="padding:26px 0;font-size:15px">${esc(t("log_empty"))}</div>`; return; }
    el.innerHTML = S.ops.slice(0, 40).map((op) => {
      const items = op.items || [];
      const size = items.reduce((s, i) => s + (i.bytes || 0), 0);
      const names = [...new Set(items.map((i) => { const p = projects().find((x) => x.path === i.project); return p ? titleOf(p) : i.project.split("/").pop(); }))];
      const canUndo = items.some((i) => i.state === "moved");
      const inTrash = op.to === "trash";
      const st = inTrash ? (t("op_state_trash." + op.state) !== "op_state_trash." + op.state ? t("op_state_trash." + op.state) : t("op_state." + op.state)) : t("op_state." + op.state);
      return `<div class="op"><span class="when">${esc(new Date(op.ts * 1000).toLocaleString(S.lang === "ko" ? "ko-KR" : "en-US", { dateStyle: "short", timeStyle: "short" }))}</span>
        <span class="what">${items.length} · <b>${bytes(size)}</b> <span class="mono">${esc(names.slice(0, 4).join(", "))}${names.length > 4 ? " +" + (names.length - 4) : ""}</span> ${op.label ? `<span class="dim">— ${esc(op.label)}</span>` : ""}</span>
        <span class="st ${esc(op.state)}">${esc(st)}</span>
        <span>${canUndo ? `<button class="btn sm" data-undo="${esc(op.id)}">${esc(t("undo"))}</button>${inTrash ? "" : ` <button class="btn sm ghost" data-trash="${esc(op.id)}">${esc(t("to_trash"))}</button>`}` : ""}</span></div>`;
    }).join("");
  }
  function bindLog() {
    $("#log").addEventListener("click", async (ev) => {
      const b = ev.target.closest("button");
      if (!b) return;
      b.disabled = true;
      try {
        if (b.dataset.undo) {
          await undoOp(b.dataset.undo);
          return;
        } else if (b.dataset.trash) {
          if (!confirm(t("confirm_trash"))) { b.disabled = false; return; }
          await api("/api/trash", { id: b.dataset.trash });
          toast(t("trashed_toast_q"));
        }
        await loadOps();
        renderAll(false);
        if (S.sel) renderDrawer();
      } catch (e) {
        toast(t("error") + ": " + e.message);
        b.disabled = false;
      }
    });
  }

  // ── wiring ───────────────────────────────────────────────────────────
  function renderAll(animate) {
    renderMeta();
    renderLede();
    renderBoard();
    renderLegend();
    renderChips();
    renderRows();
    if (S.data) strata(animate);
    renderLog();
  }
  let first = true;
  function render() {
    renderStatic();
    renderProgress();
    renderAll(first && !!S.data);
    if (S.data) first = false;
  }

  function bind() {
    $("#q").addEventListener("input", (e) => { S.f.q = e.target.value; renderRows(); strata(false); });
    $("#statusChips").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      S.f.status = b.dataset.s;
      renderChips(); renderRows(); strata(false);
    });
    [["fReclaim", "reclaim"], ["fUnsaved", "unsaved"], ["fPinned", "pinned"]].forEach(([id, k]) => {
      $("#" + id).addEventListener("change", (e) => { S.f[k] = e.target.checked; renderRows(); strata(false); });
    });
    $("#sort").addEventListener("change", (e) => { S.sort = e.target.value; localStorage.setItem("vibestrata.sort2", S.sort); renderRows(); });
    $("#rows").addEventListener("click", (e) => {
      if (e.target.closest("td.pick")) return;
      const tr = e.target.closest("tr[data-id]");
      if (tr) openDrawer(tr.dataset.id);
    });
    $("#rows").addEventListener("change", (e) => {
      const id = e.target.dataset.pick;
      if (!id) return;
      e.target.checked ? S.checked.add(id) : S.checked.delete(id);
      e.target.closest("tr").classList.toggle("picked", e.target.checked);
      renderRows();
    });
    $("#pickAll").addEventListener("change", (e) => {
      filtered().forEach((p) => (e.target.checked ? S.checked.add(p.id) : S.checked.delete(p.id)));
      renderRows();
    });
    $("#selbar").addEventListener("click", async (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      const ps = picked();
      const m = b.dataset.sel;
      if (m === "clear") { S.checked.clear(); return renderRows(); }
      if (m === "pin") {
        const allPinned = ps.every(pinned);
        let j;
        for (const p of ps) j = await api("/api/pin", { id: p.id, pinned: !allPinned });
        if (j) S.state = j.state;
        return renderAll(false);
      }
      if (["regen", "byp", "whole"].includes(m)) openRemove(ps, m);
    });
    $("#selbar").addEventListener("change", async (e) => {
      if (e.target.dataset.sel !== "state" || !e.target.value) return;
      const v = e.target.value === "__auto" ? null : e.target.value;
      await setState(picked().map((p) => p.id), v);
      toast(t("state_set", { n: S.checked.size, s: v ? t("state." + v) : t("auto_state") }));
      renderAll(false);
    });
    $("#rows").addEventListener("mouseover", (e) => {
      const tr = e.target.closest("tr[data-id]");
      document.querySelectorAll("#strata .blk").forEach((g) => g.classList.toggle("on", !!tr && g.dataset.id === tr.dataset.id));
      $("#strata").classList.toggle("hovering", !!tr);
    });
    $("#rows").addEventListener("mouseleave", () => { $("#strata").classList.remove("hovering"); });
    $("#board").addEventListener("click", (e) => {
      const more = e.target.closest(".lane-more");
      if (more) return showInList(more.dataset.s);
      const li = e.target.closest("li[data-id]");
      if (li) openDrawer(li.dataset.id);
    });
    $("#board").addEventListener("keydown", (e) => {
      const li = e.target.closest("li[data-id]");
      if (li && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openDrawer(li.dataset.id); }
    });
    $("#board").addEventListener("mouseover", (e) => {
      const li = e.target.closest("li[data-id]");
      document.querySelectorAll("#strata .blk").forEach((g) => g.classList.toggle("on", !!li && g.dataset.id === li.dataset.id));
      $("#strata").classList.toggle("hovering", !!li);
    });
    $("#board").addEventListener("mouseleave", () => { $("#strata").classList.remove("hovering"); });
    $("#rescan").addEventListener("click", async () => {
      const j = await api("/api/scan", {});
      S.progress = j.progress;
      poll();
    });
    $("#lang").addEventListener("click", () => {
      S.lang = S.lang === "ko" ? "en" : "ko";
      localStorage.setItem("vibestrata.lang", S.lang);
      render();
      if (S.sel) renderDrawer();
    });
    $("#legendBtn").addEventListener("click", () => { S.legend = !S.legend; renderLegend(); });
    $("#share").addEventListener("click", openShare);
    $("#quit").addEventListener("click", async () => {
      try { await api("/api/quit", {}); } catch (e) { /* server already gone */ }
      document.body.innerHTML = `<div class="sheet"><p class="empty">${esc(t("bye"))}</p></div>`;
      setTimeout(() => window.close(), 400);
    });
    $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
    let rt;
    window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { strataLayout = null; if (S.data) strata(false); }, 150); });
    bindStrata();
    bindDrawer();
    bindLog();
  }

  bind();
  load().catch((e) => { $("#lede").textContent = t("error") + ": " + e.message; });
})();
