/* Ledgerline · Invoice Desk: front end (no build step). */
(() => {
  "use strict";

  // ------------------------------------------------------------------ icons
  const P = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  const ICONS = {
    inbox: P('<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>'),
    list: P('<path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>'),
    book: P('<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>'),
    moon: P('<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>'),
    sun: P('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>'),
    reset: P('<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>'),
    x: P('<path d="M18 6 6 18M6 6l12 12"/>'),
    upload: P('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>'),
    check: P('<path d="M20 6 9 17l-5-5"/>'),
    pause: P('<path d="M9 6.5v11M15 6.5v11"/>'),
    alert: P('<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/>'),
    circleCheck: P('<circle cx="12" cy="12" r="9.5"/><path d="m8.5 12.2 2.4 2.4 4.6-4.9"/>'),
    circleX: P('<circle cx="12" cy="12" r="9.5"/><path d="m15 9-6 6M9 9l6 6"/>'),
    circlePause: P('<circle cx="12" cy="12" r="9.5"/><path d="M10 8.5v7M14 8.5v7"/>'),
    circleDash: P('<circle cx="12" cy="12" r="9.5"/><path d="M8.5 12h7"/>'),
    file: P('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h5"/>'),
    chevron: P('<path d="m9 18 6-6-6-6"/>'),
    search: P('<circle cx="11" cy="11" r="7.5"/><path d="m21 21-4.3-4.3"/>'),
    play: P('<path d="M7 4.5v15l12-7.5z"/>'),
    copy: P('<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>'),
    minus: P('<path d="M6 12h12"/>'),
    spark: P('<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6.3 6.3l2.5 2.5M15.2 15.2l2.5 2.5M6.3 17.7l2.5-2.5M15.2 8.8l2.5-2.5"/>'),
    message: P('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'),
    library: P('<path d="M4 4h4v16H4zM10 4h4v16h-4z"/><path d="m16.5 4.5 3.8 1-3.9 14.5-3.8-1z"/>'),
    send: P('<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>'),
  };
  const icon = (n, cls = "") => `<span class="ico ${cls}">${ICONS[n] || ""}</span>`;
  const hydrateIcons = (root = document) => root.querySelectorAll("[data-icon]").forEach((el) => { el.innerHTML = ICONS[el.dataset.icon] || ""; });

  // ------------------------------------------------------------------ utils
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const inr = (x) => {
    if (x == null || Number.isNaN(x)) return "—";
    const frac = Math.abs(x % 1) > 0.001;
    return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: frac ? 2 : 0, maximumFractionDigits: frac ? 2 : 0 }).format(x);
  };
  const inrShort = (x) => {
    if (x == null) return "—";
    if (x >= 1e7) return `₹${(x / 1e7).toFixed(2)} Cr`;
    if (x >= 1e5) return `₹${(x / 1e5).toFixed(2)} L`;
    return inr(Math.round(x));
  };
  const fmtTime = (iso) => new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const fmtDay = (iso) => new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
  const fmtDate = (d) => {
    if (!d) return "—";
    const t = new Date(d + "T00:00:00");
    return Number.isNaN(t.getTime()) ? d : t.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
  };
  const secs = (ms) => (ms == null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);
  const mask = (acct) => (acct ? `••••${String(acct).slice(-4)}` : "—");
  const effective = (r) => (r.review ? (r.review.action === "approve" ? "approved" : "rejected") : r.result?.decision);
  const LABEL = { approved: "Approved", hold: "On hold", rejected: "Rejected", running: "Running", queued: "Queued", error: "Error" };
  const chip = (d, text) => `<span class="chip ${esc(d)}"><span class="dot"></span>${esc(text || LABEL[d] || d)}</span>`;
  const art = (run, name) => `/api/runs/${encodeURIComponent(run.id)}/artifacts/${encodeURIComponent(name)}`;
  const SRC = { text: "Digital", scan: "Scan", photo: "Photo" };
  const GRADE = { digital: "approved", good: "approved", fair: "hold", poor: "hold", unreadable: "rejected" };
  const REGION_COLOR = { vendor_block: "#3465a4", invoice_meta: "#4e9a06", bill_to: "#75507b", line_items: "#c4a000", totals: "#cc0000",
    bank_details: "#06989a", stamp: "#dc3278", signature: "#8f5902", handwriting: "#f57900" };
  const FSTATUS = { verified: ["approved", "Proven"], agreed: ["approved", "Two reads agree"], read: ["", "Read"], repaired: ["hold", "Repaired"],
    disputed: ["rejected", "Reads disagree"], low: ["hold", "Low confidence"], missing: ["", "Missing"] };
  const FNAME = { vendor_name: "Vendor", vendor_gstin: "GSTIN", invoice_number: "Invoice no.", invoice_date: "Invoice date", due_date: "Due date",
    po_reference: "PO reference", line_items: "Line items", subtotal: "Subtotal", tax_total: "Tax", total: "Total", bank_account: "Bank account", bank_ifsc: "IFSC" };

  let toastTimer;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, opts);
    if (!res.ok) {
      let msg = `${res.status} ${res.statusText}`;
      try { const j = await res.json(); if (j.detail) msg = j.detail; } catch (e) { /* not json */ }
      throw new Error(msg);
    }
    return res.json();
  }

  // ------------------------------------------------------------------ state
  const S = {
    config: null, runs: [], engine: "auto", current: null, es: null, mounted: null,
    open: new Set(), revealed: new Set(), answers: {}, autoOpened: new Set(), detailCache: {}, filter: "all", q: "", batch: false, enders: {}, ticker: null,
  };
  const view = $("#view");

  // ------------------------------------------------------------------ theme
  function effectiveTheme() {
    const t = document.documentElement.dataset.theme;
    if (t) return t;
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function paintThemeButton() {
    const dark = effectiveTheme() === "dark";
    const b = $("#theme-toggle");
    b.querySelector(".ico").innerHTML = dark ? ICONS.sun : ICONS.moon;
    b.querySelector(".theme-label").textContent = dark ? "Light" : "Dark";
  }
  $("#theme-toggle").addEventListener("click", () => {
    const next = effectiveTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("ll-theme", next); } catch (e) { /* storage blocked */ }
    paintThemeButton();
  });

  // ------------------------------------------------------------------ chrome
  function paintEngine() {
    const c = S.config;
    $("#engine-status").innerHTML = (c.claude_available
      ? `<b><span class="dot"></span>Claude connected</b>${esc(c.model)} reads every invoice. Rules make the decision.`
      : `<b><span class="dot warn"></span>Offline mode</b>No API key set, so invoices are read by local OCR. Set ANTHROPIC_API_KEY to use Claude.`)
      + `<span class="engine-row"><span class="dot ${c.layout_model ? "" : "warn"}"></span>Vision: OpenCV clean-up${c.layout_model ? " + YOLO layout" : " (no layout model)"}</span>`;
  }
  function paintNav() {
    const route = (location.hash || "#/process").split("/")[1];
    $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === (route === "run" ? "process" : route)));
    const waiting = S.runs.filter((r) => r.decision === "hold").length;
    const nc = $("#nav-count");
    nc.textContent = waiting ? `${waiting} to review` : S.runs.length || "";
    nc.classList.toggle("attn", waiting > 0);
  }
  async function refreshRuns() {
    try { S.runs = await api("/api/runs"); } catch (e) { /* keep last */ }
    paintNav();
  }

  // ------------------------------------------------------------------ router
  async function router() {
    const parts = (location.hash || "#/process").slice(2).split("/");
    paintNav();
    if (parts[0] === "runs") return renderRuns();
    if (parts[0] === "data") return renderData();
    if (parts[0] === "knowledge") return renderKnowledge();
    if (parts[0] === "run" && parts[1]) return renderProcess(parts[1]);
    return renderProcess(null);
  }
  window.addEventListener("hashchange", router);

  // ------------------------------------------------------------------ process view
  function intakeHTML() {
    const c = S.config;
    const seg = ["auto", "claude", "local"].map((e) => {
      const dis = e === "claude" && !c.claude_available;
      const lbl = { auto: "Auto", claude: "Claude", local: "Local OCR" }[e];
      return `<button type="button" data-engine="${e}" aria-pressed="${S.engine === e}" ${dis ? 'disabled title="Set ANTHROPIC_API_KEY to enable"' : ""}>${lbl}</button>`;
    }).join("");
    const samples = c.samples.map((s, i) => `
      <button class="sample" type="button" data-sample="${esc(s.file)}">
        <span class="sample-title">${i + 1}. ${esc(s.title)}</span>
        ${chip(s.expect, s.tag)}
        <span class="sample-meta">${esc(s.vendor)} · ${esc(s.why)}</span>
      </button>`).join("");
    return `
      <div class="panel panel-pad">
        <h2>New invoice</h2>
        <input type="file" id="file-input" accept="application/pdf,.pdf,image/jpeg,image/png,image/tiff,image/webp,.jpg,.jpeg,.png,.tif,.tiff,.webp" hidden>
        <label class="drop" for="file-input" id="drop" tabindex="0">
          ${icon("upload")}
          <strong>Drop an invoice PDF or photo</strong>
          <span>or click to choose a file. Digital PDFs, scans and phone photos (JPG, PNG) all work.</span>
        </label>
        <div class="field-row" style="margin-top:14px">
          <span class="label" id="engine-lbl">Reader</span>
          <div class="seg" role="group" aria-labelledby="engine-lbl">${seg}</div>
        </div>
      </div>
      <div class="panel panel-pad">
        <h2>Demo scenarios</h2>
        <div class="samples">${samples}</div>
        <div class="sample-foot">
          <button class="btn-ghost sm" type="button" id="run-all">${icon("play")}Run all ${c.samples.length} in order</button>
        </div>
      </div>`;
  }

  function emptyHTML() {
    return `<div class="empty"><div>
      <svg class="empty-art" viewBox="0 0 260 150" aria-hidden="true">
        <rect x="18" y="12" width="104" height="128" rx="8" fill="var(--raised)" stroke="var(--line-strong)"/>
        <rect x="32" y="28" width="52" height="8" rx="3" fill="var(--ink)" opacity=".85"/>
        <rect x="32" y="44" width="36" height="5" rx="2.5" fill="var(--ink-3)" opacity=".5"/>
        <rect x="32" y="64" width="76" height="4" rx="2" fill="var(--line-strong)"/>
        <rect x="32" y="76" width="76" height="4" rx="2" fill="var(--line-strong)"/>
        <rect x="32" y="88" width="60" height="4" rx="2" fill="var(--line-strong)"/>
        <rect x="70" y="110" width="38" height="8" rx="3" fill="var(--sage-mid)" opacity=".8"/>
        <path d="M134 76h22" stroke="var(--line-strong)" stroke-width="2" stroke-dasharray="3 4"/>
        <path d="M184 22v104" stroke="var(--line)" stroke-width="2"/>
        <path d="M184 22v72" stroke="var(--sage-mid)" stroke-width="2"/>
        <circle cx="184" cy="22" r="8" fill="var(--sage)"/><circle cx="184" cy="58" r="8" fill="var(--sage)"/>
        <circle cx="184" cy="94" r="8" fill="var(--panel)" stroke="var(--accent)" stroke-width="2"/>
        <circle cx="184" cy="126" r="8" fill="var(--panel)" stroke="var(--line-strong)" stroke-width="1.5"/>
        <rect x="200" y="18" width="44" height="7" rx="3" fill="var(--ink-2)" opacity=".7"/>
        <rect x="200" y="54" width="36" height="7" rx="3" fill="var(--ink-2)" opacity=".7"/>
        <rect x="200" y="90" width="48" height="7" rx="3" fill="var(--accent)" opacity=".7"/>
        <rect x="200" y="122" width="30" height="7" rx="3" fill="var(--line-strong)"/>
      </svg>
      <h3>Pick a scenario or drop a PDF</h3>
      <p>Each invoice runs through ten stages: cleaning up the image, finding its regions, reading and proving every field, then the payment checks. You'll see every stage as it runs and the reasons behind the decision.</p>
    </div></div>`;
  }

  function renderProcess(runId) {
    if (S.mounted && S.mounted === runId && $("#run-area")) return; // already showing this run
    view.innerHTML = `
      <div class="page-head">
        <div>
          <h1 class="page-title">Process an invoice</h1>
          <p class="page-lede">From PDF or photo to a payment decision. Vision cleans the page, AI reads it, every field is proven, and fixed, auditable rules make the call.</p>
        </div>
      </div>
      <div class="process">
        <section class="intake" aria-label="Choose an invoice">${intakeHTML()}</section>
        <section id="run-area" aria-live="polite"></section>
      </div>`;
    bindIntake();
    S.mounted = null;
    const area = $("#run-area");
    const id = runId || S.current?.id;
    if (!id) { area.innerHTML = emptyHTML(); return; }
    if (S.current && S.current.id === id) {
      mountRun(S.current);
      if (!["done", "error"].includes(S.current.status)) subscribe(id);
    } else {
      area.innerHTML = `<div class="panel panel-pad"><div class="skeleton" style="width:40%"></div><div class="skeleton"></div><div class="skeleton" style="width:70%"></div></div>`;
      api(`/api/runs/${encodeURIComponent(id)}`).then((run) => {
        S.current = run;
        mountRun(run);
        if (!["done", "error"].includes(run.status)) subscribe(id);
      }).catch(() => { area.innerHTML = emptyHTML(); toast(`Run ${id} not found`); });
    }
  }

  function bindIntake() {
    $$("[data-engine]").forEach((b) => b.addEventListener("click", () => {
      S.engine = b.dataset.engine;
      $$("[data-engine]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    }));
    $$("[data-sample]").forEach((b) => b.addEventListener("click", () => startRun({ sample: b.dataset.sample })));
    const input = $("#file-input"), drop = $("#drop");
    input.addEventListener("change", () => { if (input.files[0]) startRun({ file: input.files[0] }); input.value = ""; });
    drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
    ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) startRun({ file: f }); });
    $("#run-all").addEventListener("click", runAll);
    setIntakeBusy(S.batch);
  }
  function setIntakeBusy(busy) {
    $$("[data-sample], #run-all").forEach((b) => { b.disabled = busy; });
    const ra = $("#run-all");
    if (ra) ra.innerHTML = busy ? `${icon("play")}Running scenarios…` : `${icon("play")}Run all ${S.config.samples.length} in order`;
  }

  async function startRun({ file, sample }) {
    const fd = new FormData();
    if (file) {
      if (!/\.(pdf|jpe?g|png|tiff?|webp)$/i.test(file.name) && !/^(application\/pdf|image\/)/.test(file.type)) { toast("Send the invoice as a PDF, JPG or PNG."); return null; }
      fd.append("file", file);
    }
    if (sample) fd.append("sample", sample);
    fd.append("engine", S.engine);
    let id;
    try { ({ id } = await api("/api/runs", { method: "POST", body: fd })); } catch (e) { toast(e.message); return null; }
    const done = new Promise((res) => { S.enders[id] = res; });
    S.current = null;
    S.scrollToRun = true;
    location.hash = `#/run/${id}`;
    return done;
  }

  async function runAll() {
    if (S.batch) return;
    S.batch = true;
    setIntakeBusy(true);
    try {
      for (const s of S.config.samples) {
        const p = await startRun({ sample: s.file });
        if (!p) break;
        await p;
        await sleep(900);
      }
      toast("All scenarios processed. See them on the Runs page.");
    } finally {
      S.batch = false;
      setIntakeBusy(false);
    }
  }

  function subscribe(id) {
    if (S.es) S.es.close();
    const es = new EventSource(`/api/runs/${encodeURIComponent(id)}/stream`);
    S.es = es;
    es.onmessage = (e) => {
      const run = JSON.parse(e.data);
      S.current = run;
      if (S.mounted === run.id) updateRun(run);
    };
    es.addEventListener("end", async () => {
      es.close();
      if (S.es === es) S.es = null;
      await refreshRuns();
      if (S.enders[id]) { S.enders[id](); delete S.enders[id]; }
    });
    es.onerror = () => {
      es.close();
      // fall back to one fetch, then resolve so batches don't hang
      api(`/api/runs/${encodeURIComponent(id)}`).then((run) => { S.current = run; if (S.mounted === id) updateRun(run); }).finally(() => {
        if (S.enders[id]) { S.enders[id](); delete S.enders[id]; }
      });
    };
  }

  // ------------------------------------------------------------------ run panel
  function mountRun(run) {
    S.mounted = run.id;
    S.detailCache = {};
    S.open = new Set();
    S.autoOpened.delete(run.id); // reopening a finished run shows its exceptions again
    const area = $("#run-area");
    area.innerHTML = `
      <div class="run">
        <div class="run-head">
          <div>
            <div class="run-id"><span class="mono">${esc(run.id)}</span><span id="run-chip"></span><span id="run-engine"></span></div>
            <h2 class="run-file">${esc(run.filename)}</h2>
          </div>
          <div class="run-tools">
            <span class="elapsed" id="elapsed"></span>
            <button class="btn-ghost sm" type="button" id="view-doc">${icon("file")}View document</button>
          </div>
        </div>
        <div class="run-grid">
          <div class="panel" style="padding:10px 18px 4px 8px">
            <ol class="timeline" id="timeline">
              ${run.stages.map((s) => `
                <li class="stage s-pending" data-key="${s.key}">
                  <div class="stage-rail"><span class="node"></span></div>
                  <div class="stage-body">
                    <button class="stage-top" type="button" aria-expanded="false">
                      <span class="stage-label">${esc(s.label)}${icon("chevron", "caret")}</span>
                      <span class="stage-ms"></span>
                    </button>
                    <div class="stage-summary"></div>
                    <div class="stage-detail"></div>
                  </div>
                </li>`).join("")}
            </ol>
          </div>
          <aside class="panel decision" id="decision" aria-live="polite"></aside>
        </div>
      </div>`;
    $$("#timeline .stage").forEach((li) => {
      $(".stage-top", li).addEventListener("click", () => {
        if (!li.classList.contains("has-detail")) return;
        const k = li.dataset.key;
        S.open.has(k) ? S.open.delete(k) : S.open.add(k);
        li.classList.toggle("open", S.open.has(k));
        $(".stage-top", li).setAttribute("aria-expanded", String(S.open.has(k)));
      });
    });
    $("#view-doc").addEventListener("click", () => openDoc(S.current));
    updateRun(run);
    if (S.scrollToRun) {
      S.scrollToRun = false;
      if (matchMedia("(max-width: 960px)").matches) area.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  const NODE_ICON = { done: "check", warn: "check", hold: "pause", fail: "x", error: "x", skipped: "minus" };

  function updateRun(run) {
    // when a run finishes, open the stages that explain a hold or rejection
    if (run.status === "done" && !S.autoOpened.has(run.id)) {
      S.autoOpened.add(run.id);
      run.stages.forEach((s) => { if (s.status === "hold" || s.status === "fail") S.open.add(s.key); });
    }
    // header
    const eff = effective(run);
    $("#run-chip").innerHTML = run.status === "done" ? chip(eff) : run.status === "error" ? chip("rejected", "Error") : chip("running", "Running");
    $("#run-engine").innerHTML = run.engine ? `<span class="chip">${run.engine === "claude" ? esc(run.engine_meta?.model || "Claude") : "Local OCR"}</span>` : "";
    tick(run);

    // stages
    run.stages.forEach((s) => {
      const li = $(`#timeline [data-key="${s.key}"]`);
      if (!li) return;
      const detail = stageDetail(run, s);
      li.className = `stage s-${s.status}${detail ? " has-detail" : ""}${S.open.has(s.key) && detail ? " open" : ""}`;
      $(".node", li).innerHTML = NODE_ICON[s.status] ? icon(NODE_ICON[s.status]) : "";
      $(".stage-ms", li).textContent = s.ms != null ? secs(s.ms) : s.status === "running" ? "…" : "";
      $(".stage-summary", li).textContent = s.status === "running" ? runningText(s.key, run) : s.summary || " ";
      const key = s.key + ":" + (detail ? detail.length : 0) + ":" + s.status;
      if (S.detailCache[s.key] !== key) {
        $(".stage-detail", li).innerHTML = detail || "";
        S.detailCache[s.key] = key;
        bindCopy(li);
        $$("[data-doc]", li).forEach((b) => b.addEventListener("click", () => openDoc(S.current, b.dataset.doc)));
      }
    });

    renderDecision(run);
  }

  function runningText(key, run) {
    return {
      intake: "Checking the file and fingerprinting it…",
      vision: "Measuring the image and cleaning it up…",
      layout: "Finding regions, stamps and signatures…",
      verify: "Proving fields with checksums and arithmetic…",
      extract: run.engine_requested === "local" || !S.config.claude_available ? "Reading the document with OCR…" : "Claude is reading the document…",
      vendor: "Looking up the vendor master…",
      validate: "Checking fields, arithmetic, tax and bank details…",
      match: "Finding the purchase order…",
      controls: "Checking duplicates, prices and PO balance…",
      decide: "Applying the decision rules…",
    }[key];
  }

  function tick(run) {
    clearInterval(S.ticker);
    const el = $("#elapsed");
    if (!el) return;
    if (run.status === "done" || run.status === "error") {
      el.textContent = run.duration_ms != null ? `Finished in ${secs(run.duration_ms)}` : "";
      return;
    }
    const t0 = Date.parse(run.created_at);
    const paint = () => { el.textContent = `${((Date.now() - t0) / 1000).toFixed(1)} s`; };
    paint();
    S.ticker = setInterval(paint, 100);
  }

  // ---------- stage detail renderers
  function checksHTML(run, ids) {
    const list = run.checks.filter((c) => ids.includes(c.id));
    if (!list.length) return "";
    return `<ul class="checks">${list.map((c) => {
      const ic = c.status === "pass" ? "circleCheck" : c.status === "warn" ? "circleDash" : c.severity === "reject" ? "circleX" : "circlePause";
      const pol = c.policy && c.status !== "pass" ? `<details class="cite"><summary>${esc(c.policy.doc)} ${esc(c.policy.ref)} · ${esc(c.policy.title)}</summary><p>${esc(c.policy.text)}</p></details>` : "";
      return `<li class="check ${c.status} sev-${c.severity}">${icon(ic)}<div><div class="check-label">${esc(c.label)}</div><div class="check-detail">${esc(c.detail)}</div>${pol}</div><span class="check-id">${esc(c.id)}</span></li>`;
    }).join("")}</ul>`;
  }
  const logHTML = (s) => (s.log?.length ? `<ul class="stage-log">${s.log.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : "");

  function stageDetail(run, s) {
    if (!["done", "warn", "hold", "fail", "error"].includes(s.status)) return "";
    const d = s.detail || {};
    switch (s.key) {
      case "intake":
        return `${logHTML(s)}<dl class="kv"><dt>Type</dt><dd>${esc(d.kind)}</dd><dt>Pages</dt><dd>${esc(d.pages)}</dd><dt>Size</dt><dd>${esc(d.size_kb)} KB</dd><dt>Fingerprint</dt><dd class="mono">${esc((run.file_hash || "").slice(0, 24))}…</dd></dl>`;
      case "vision":
        return visionHTML(run, s, d);
      case "layout":
        return layoutHTML(run, s, d);
      case "verify":
        return verifyHTML(run, s, d);
      case "extract": {
        const f = d.raw || {};
        const low = new Set(f.low_confidence_fields || []);
        const v = (k, val) => `<dd class="${low.has(k) ? "low" : ""}">${val ?? "—"}${low.has(k) ? " · low confidence" : ""}</dd>`;
        const items = (f.line_items || []).map((li) => `<tr><td>${esc(li.description)}</td><td class="num">${esc(li.quantity ?? "—")}</td><td class="num">${inr(li.unit_price)}</td><td class="num">${inr(li.amount)}</td></tr>`).join("");
        return `${logHTML(s)}
          <dl class="kv">
            <dt>Vendor</dt>${v("vendor_name", esc(f.vendor_name))}
            <dt>GSTIN</dt>${v("vendor_gstin", f.vendor_gstin ? `<span class="mono">${esc(f.vendor_gstin)}</span>` : null)}
            <dt>Invoice no.</dt>${v("invoice_number", f.invoice_number ? `<span class="mono">${esc(f.invoice_number)}</span>` : null)}
            <dt>Invoice date</dt>${v("invoice_date", esc(fmtDate(f.invoice_date)))}
            <dt>Due</dt>${v("due_date", esc(fmtDate(f.due_date)))}
            <dt>PO reference</dt>${v("po_reference", f.po_reference ? esc(f.po_reference) : '<span class="low">Not printed</span>')}
            <dt>Subtotal</dt>${v("subtotal", inr(f.subtotal))}
            <dt>Tax</dt><dd>${(f.tax_lines || []).map((t) => `${esc(t.label)} ${inr(t.amount)}`).join(" · ") || "—"}</dd>
            <dt>Total</dt>${v("total", `<b>${inr(f.total)}</b>`)}
            <dt>Pay to</dt>${v("bank_account", f.bank_account ? `${esc(f.bank_name || "")} ${mask(f.bank_account)} · ${esc(f.bank_ifsc || "")}` : null)}
            ${(f.notes || []).length ? `<dt>Notes</dt><dd>${f.notes.map(esc).join("<br>")}</dd>` : ""}
          </dl>
          ${items ? `<div class="table-wrap" style="margin-top:12px"><table><thead><tr><th>Line item</th><th class="num">Qty</th><th class="num">Rate</th><th class="num">Amount</th></tr></thead><tbody>${items}</tbody></table></div>` : ""}`;
      }
      case "vendor": {
        const vd = run.vendor;
        if (!vd) return `${logHTML(s)}<p class="check-detail">No vendor in the master matches the name or GSTIN on this invoice.</p>`;
        return `${logHTML(s)}<dl class="kv"><dt>Vendor</dt><dd>${esc(vd.name)} <span class="mono">${esc(vd.id)}</span></dd><dt>Status</dt><dd>${esc(vd.status)}</dd><dt>GSTIN on file</dt><dd class="mono">${esc(vd.gstin)}</dd><dt>Bank on file</dt><dd>${esc(vd.bank.bank)} ${mask(vd.bank.account)}</dd><dt>Terms</dt><dd>Net ${esc(vd.payment_terms_days)} days · GST ${esc(vd.gst_rate)}%</dd><dt>Contact</dt><dd>${esc(vd.contact.name)} · ${esc(vd.contact.phone)}</dd></dl>`;
      }
      case "validate":
        return checksHTML(run, s.checks || []);
      case "match": {
        let html = checksHTML(run, s.checks || []);
        const c = d.candidates || [];
        if (d.method !== "explicit" && c.length) {
          html += `<div class="table-wrap" style="margin-top:12px"><table><thead><tr><th>Open PO</th><th class="num">Lines</th><th class="num">Amount</th><th class="num">Score</th></tr></thead><tbody>
            ${c.map((x, i) => `<tr${i === 0 ? ' style="font-weight:550"' : ""}><td>${esc(x.po_id)}<span class="sub">${esc(x.po_description)}</span></td><td class="num">${Math.round(x.description_similarity * 100)}%</td><td class="num">${Math.round(x.amount_fit * 100)}%</td><td class="num">${Math.round(x.confidence * 100)}%</td></tr>`).join("")}
          </tbody></table></div>`;
        }
        return html;
      }
      case "controls": {
        let html = checksHTML(run, s.checks || []);
        const lc = d.line_comparison || [];
        if (lc.length) {
          html += `<div class="table-wrap" style="margin-top:12px"><table><thead><tr><th>Invoice line</th><th class="num">Qty</th><th class="num">Rate vs PO</th></tr></thead><tbody>
            ${lc.map((r) => `<tr class="${r.status !== "ok" ? "bad" : ""}"><td>${esc(r.description)}${r.po_line ? (r.match === "semantic" ? `<span class="sub"><span class="sem">matched by meaning ${Math.round(r.match_score * 100)}%</span> → ${esc(r.po_line)}</span>` : "") : '<span class="sub">Not on the PO</span>'}</td>
              <td class="num">${esc(r.qty ?? "—")}${r.po_qty != null ? `<span class="sub">${r.qty_billed_before ? `+${r.qty_billed_before} earlier, ` : ""}${r.po_qty} ordered</span>` : ""}</td>
              <td class="num">${inr(r.unit_price)}${r.po_unit_price != null ? `<span class="sub">PO ${inr(r.po_unit_price)}${Math.abs(r.variance_pct) > 0.05 ? ` · ${r.variance_pct > 0 ? "+" : ""}${r.variance_pct.toFixed(1)}%` : ""}</span>` : ""}</td></tr>`).join("")}
          </tbody></table></div>`;
        }
        if (d.po) html += meterHTML(d.po);
        return html;
      }
      case "decide":
        return logHTML(s);
      default:
        return "";
    }
  }

  // ---------- clean-up stage
  function visionHTML(run, s, d) {
    if (d.source === "text") {
      const p = (d.pages || [])[0];
      return `${logHTML(s)}${p ? `<div class="compare one"><button type="button" class="fig" data-doc="original"><img src="${art(run, p.original)}" alt="Page 1 as received" loading="lazy"><span>Page as received</span></button></div>` : ""}`;
    }
    const p = (d.pages || [])[0] || {};
    const b = p.before || {}, a = p.after || {};
    const row = (lbl, x, y, fmt, better) => {
      const good = better === "up" ? y > x * 1.05 : better === "down" ? Math.abs(y) < Math.abs(x) * 0.95 : false;
      return `<tr><td>${lbl}</td><td class="num">${fmt(x)}</td><td class="num ${good ? "better" : ""}">${fmt(y)}</td></tr>`;
    };
    const f1 = (v) => (v == null ? "—" : Number(v).toFixed(1));
    const f0 = (v) => (v == null ? "—" : Math.round(v).toLocaleString("en-IN"));
    const deg = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(1)}°`);
    const pct = Math.max(0, Math.min(100, d.score || 0));
    return `
      <div class="compare">
        <button type="button" class="fig" data-doc="original"><img src="${art(run, p.original)}" alt="Page as received" loading="lazy"><span>As received</span></button>
        <button type="button" class="fig" data-doc="clean"><img src="${art(run, p.cleaned)}" alt="Page after clean-up" loading="lazy"><span>After clean-up</span></button>
      </div>
      <div class="legib">
        <div class="legib-top"><span class="label">Legibility after clean-up</span>${chip(GRADE[d.grade] || "", `${d.score}/100 · ${d.grade}`)}</div>
        <div class="gauge g-${esc(d.grade)}" role="img" aria-label="Legibility ${d.score} out of 100"><span style="width:${pct}%"></span><i style="left:50%"></i><i style="left:80%"></i></div>
        <div class="sub">Measured, not guessed: OCR confidence ${esc(d.ocr_conf)}% across ${esc(d.words)} words. Below 50 the vendor is asked for a clearer copy.</div>
      </div>
      <h4 class="mini">What was fixed</h4>
      <ol class="steps">${(p.steps || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ol>
      <div class="table-wrap" style="margin-top:12px"><table><thead><tr><th>Measure</th><th class="num">Before</th><th class="num">After</th></tr></thead><tbody>
        ${row("Sharpness (Laplacian variance)", b.sharpness, a.sharpness, f0, "up")}
        ${row("Uneven lighting / shadow", b.uneven, a.uneven, f1, "down")}
        ${row("Noise", b.noise, a.noise, f1, "down")}
        ${row("Text skew", b.skew_deg, a.skew_deg, deg, "down")}
        ${row("Resolution (dpi, est.)", b.dpi_est, a.dpi_est, f0, "up")}
      </tbody></table></div>
      ${(d.pages || []).length > 1 ? `<p class="sub" style="margin-top:8px">${d.pages.length} pages cleaned; page 1 shown.</p>` : ""}`;
  }

  // ---------- regions stage
  function layoutHTML(run, s, d) {
    if (!d.available) return `<p class="check-detail">The layout model isn't installed (models/layout-yolo.onnx), so the whole page is read in one pass.</p>`;
    const dets = d.detections || [];
    const counts = {};
    dets.forEach((x) => { counts[x.cls] = (counts[x.cls] || 0) + 1; });
    const legend = Object.entries(counts).map(([k, n]) => `<span class="reg"><i style="background:${REGION_COLOR[k]}"></i>${esc(dets.find((x) => x.cls === k).label)}${n > 1 ? ` ×${n}` : ""}</span>`).join("");
    const marks = dets.filter((x) => ["stamp", "handwriting", "signature"].includes(x.cls));
    const ov = (d.pages || [])[0];
    return `${logHTML(s)}
      <div class="layout-grid">
        ${ov ? `<button type="button" class="fig tall" data-doc="layout"><img src="${art(run, ov.overlay)}" alt="Detected regions" loading="lazy"><span>Detected regions</span></button>` : ""}
        <div>
          <div class="regs">${legend || '<span class="sub">No regions found</span>'}</div>
          <p class="sub" style="margin:8px 0 0">YOLO layout model (trained on synthetic invoices) · ONNX on CPU · ${esc(d.ms_model)} ms for ${dets.length} region${dets.length !== 1 ? "s" : ""}.
          ${d.regions_read?.length ? `Re-read on their own: ${d.regions_read.map((k) => esc(k.replace("_", " "))).join(", ")}.` : ""}</p>
          ${marks.length ? `<h4 class="mini">Ink on the page</h4><div class="marks">${marks.map((m) => `
            <div class="mark">${m.crop ? `<img src="${art(run, m.crop)}" alt="${esc(m.label)}" loading="lazy">` : ""}
              <div><b>${esc(m.label)}</b> <span class="sub-inline">${Math.round(m.conf * 100)}%</span>
              ${m.cls === "stamp" ? `<span class="sub">Reads “${esc(m.read?.text || "?")}”${m.read?.keyword ? ` → <b class="${["PAID", "CANCELLED", "CANCELED", "VOID"].includes(m.read.keyword) ? "warn-text" : ""}">${esc(m.read.keyword)}</b>` : ""}</span>` : ""}
              ${m.cls === "handwriting" ? `<span class="sub">${m.near ? `On the <b class="warn-text">${esc(m.near)}</b>` : "Away from amounts"}</span>` : ""}
              ${m.cls === "signature" ? `<span class="sub">Supplier signature</span>` : ""}</div></div>`).join("")}</div>` : ""}
        </div>
      </div>`;
  }

  // ---------- verification stage
  function verifyHTML(run, s, d) {
    const fs = d.fields || {};
    const rows = Object.entries(fs).filter(([k, v]) => v.status !== "missing" || ["invoice_number", "total", "vendor_gstin"].includes(k)).map(([k, v]) => {
      const [cls, lbl] = FSTATUS[v.status] || ["", v.status];
      const val = ["subtotal", "tax_total", "total"].includes(k) ? inr(v.value) : k === "bank_account" ? mask(v.value) : esc(v.value ?? "—");
      const c = Math.max(0, Math.min(100, v.conf || 0));
      return `<tr><td>${esc(FNAME[k] || k)}</td><td><span class="${["vendor_gstin", "invoice_number", "bank_ifsc"].includes(k) ? "mono" : ""}">${val}</span>
        <span class="sub">${esc(v.evidence || (v.source ? `From ${v.source}` : ""))}</span></td>
        <td>${chip(cls, lbl)}<span class="sub nowrap"><span class="cbar"><span style="width:${c}%" class="${c < 70 ? "lo" : ""}"></span></span>${c}%</span></td></tr>`;
    }).join("");
    const reps = (d.repairs || []).map((r) => `<li><b>${esc(FNAME[r.field] || r.field)}</b> <span class="mono">${esc(r.from)}</span> → <span class="mono">${esc(r.to)}</span><span class="sub">${esc(r.why)}</span></li>`).join("");
    return `${checksHTML(run, s.checks || [])}
      ${reps ? `<h4 class="mini">OCR slips repaired</h4><ul class="repairs">${reps}</ul>` : ""}
      <div class="table-wrap" style="margin-top:12px"><table class="ftable"><thead><tr><th>Field</th><th>Value and evidence</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${logHTML(s)}`;
  }

  function meterHTML(po) {
    const before = po.invoiced_before || 0, now = po.this_invoice || 0, ordered = po.ordered || 0;
    const scale = Math.max(ordered, before + now) || 1;
    const fit = Math.max(0, Math.min(now, ordered - before));
    const over = Math.max(0, before + now - ordered);
    const pct = (x) => `${(x / scale) * 100}%`;
    return `<div class="meter">
      <div class="label">${esc(po.id)} balance · ${inr(ordered)} ordered</div>
      <div class="meter-bar" style="margin-top:6px" role="img" aria-label="PO balance: ${inr(before)} invoiced earlier, ${inr(now)} on this invoice, ${inr(ordered)} ordered">
        <span class="m-before" style="width:${pct(before)}"></span><span class="m-now" style="width:${pct(fit)}"></span><span class="m-over" style="width:${pct(over)}"></span>
      </div>
      <div class="meter-legend">
        <span><i class="m-before"></i>Invoiced earlier ${inr(before)}</span>
        <span><i class="m-now"></i>This invoice ${inr(now)}</span>
        ${over ? `<span><i class="m-over"></i>Over the PO by ${inr(over)}</span>` : `<span>Left after this ${inr(po.remaining_after)}</span>`}
      </div></div>`;
  }

  // ---------- decision panel
  function renderDecision(run) {
    const el = $("#decision");
    if (run.status === "error") {
      el.innerHTML = `<div class="dec-section"><div class="error-box"><b>The run stopped.</b> ${esc(run.error)}</div><p class="check-detail" style="margin-top:10px">Nothing was posted. Fix the input and run it again.</p></div>`;
      return;
    }
    if (!run.result) {
      el.innerHTML = `<div class="verdict pending"><div class="verdict-meta">Decision</div><div class="verdict-word">Working…</div><div class="skeleton" style="width:85%"></div><div class="skeleton" style="width:60%"></div></div>
        <div class="dec-section"><div class="check-detail">The decision appears when every stage has run. Nothing is posted until then.</div></div>`;
      return;
    }
    const r = run.result;
    const eff = effective(run);
    const first = !S.revealed.has(run.id);
    S.revealed.add(run.id);
    const ic = { approved: "circleCheck", hold: "circlePause", rejected: "circleX" }[r.decision];
    const rv = run.review;
    const email = r.vendor_email;
    el.innerHTML = `
      <div class="verdict ${r.decision} ${first ? "reveal" : ""}">
        <div class="verdict-meta"><span>Decision</span><span>Routed to ${esc(r.queue)}</span></div>
        <div class="verdict-word">${icon(ic)}${esc(r.label)}</div>
        <p class="verdict-summary">${esc(r.summary)}</p>
      </div>
      <div class="dec-section">
        <div class="tally"><span><b>${r.counts.passed}</b> passed</span><span><b>${r.counts.warnings}</b> warnings</span><span><b>${r.counts.failed}</b> failed</span></div>
      </div>
      <div class="dec-section">
        <h3 class="section-title">Next steps</h3>
        <ol class="actions-list">${r.actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ol>
      </div>
      ${email ? `<div class="dec-section">
        <h3 class="section-title">Draft to vendor</h3>
        <div class="email">
          <div class="email-head"><span>To <b>${esc(email.to)}</b><br>${esc(email.subject)}</span><button class="btn-quiet sm" type="button" data-copy="${esc(email.body)}">${icon("copy")}Copy</button></div>
          <pre>${esc(email.body)}</pre>
        </div>
        <p class="check-detail" style="margin:8px 0 0">Drafted, not sent. A person reviews it first.</p>
      </div>` : ""}
      ${(r.similar || []).length ? `<div class="dec-section">
        <h3 class="section-title">Similar past decisions</h3>
        <ul class="cases">${r.similar.map((c) => `<li>
          <div class="case-top"><span class="mono">${esc(c.ref)}</span>${chip(c.decision === "approved" ? "approved" : c.decision === "rejected" ? "rejected" : "hold", c.decision === "held" ? "Held" : c.decision === "approved" ? "Approved" : "Rejected")}<span class="sub-inline">${esc(c.vendor)} · ${esc(c.check)}${c.date ? ` · ${fmtDate(c.date)}` : ""}</span></div>
          <div class="case-facts">${esc(c.facts)}</div><div class="case-note">“${esc(c.note)}”</div></li>`).join("")}</ul>
        <p class="check-detail" style="margin:8px 0 0">Found by hybrid search over past reviewer notes. Precedent informs the reviewer; it never decides.</p>
      </div>` : ""}
      ${r.decision === "hold" ? `<div class="dec-section review">
        <h3 class="section-title">Reviewer decision</h3>
        ${rv ? `<p class="reviewed"><b>${rv.action === "approve" ? "Approved and posted" : "Rejected"}</b> by ${esc(rv.by)} at ${fmtTime(rv.at)}${rv.note ? `: “${esc(rv.note)}”` : "."}</p>`
            : `<label class="label" for="review-note">Note for the audit trail</label>
               <textarea id="review-note" placeholder="e.g. Confirmed PO-1004 with Aman in IT"></textarea>
               <div class="review-btns">
                 <button class="btn sage sm" type="button" data-review="approve">${icon("check")}Approve and post</button>
                 <button class="btn-ghost sm danger" type="button" data-review="reject">${icon("x")}Reject</button>
               </div>`}
      </div>` : ""}
      <div class="dec-section ask">
        <h3 class="section-title">${icon("message")} Ask about this invoice</h3>
        <div class="ask-sugg">${["Why is this " + (r.decision === "approved" ? "approved" : r.decision === "hold" ? "on hold" : "rejected") + "?", "What must I check before approving?", "What does the policy say?"].map((q) => `<button type="button" class="pill" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <form class="ask-form" id="ask-form"><input id="ask-q" type="text" maxlength="600" placeholder="Ask about policy, this run or past cases" aria-label="Question"><button class="btn sm" type="submit" aria-label="Ask">${icon("send")}</button></form>
        <div id="ask-out" aria-live="polite">${S.answers[run.id] || ""}</div>
      </div>`;
    bindCopy(el);
    bindAsk(el, run);
    $$("[data-review]", el).forEach((b) => b.addEventListener("click", async () => {
      const note = $("#review-note")?.value || "";
      if (b.dataset.review === "approve" && !note.trim()) { toast("Add a short note before approving an exception."); $("#review-note").focus(); return; }
      $$("[data-review]", el).forEach((x) => { x.disabled = true; });
      try {
        const updated = await api(`/api/runs/${run.id}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: b.dataset.review, note }) });
        S.current = updated;
        updateRun(updated);
        await refreshRuns();
        toast(b.dataset.review === "approve" ? "Approved and posted to the ledger." : "Rejected. Nothing was posted.");
      } catch (e) {
        toast(e.message);
        $$("[data-review]", el).forEach((x) => { x.disabled = false; });
      }
    }));
    void eff;
  }

  function bindCopy(root) {
    $$("[data-copy]", root).forEach((b) => {
      if (b.dataset.bound) return;
      b.dataset.bound = "1";
      b.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(b.dataset.copy); toast("Copied to clipboard"); } catch (e) { toast("Couldn't copy. Select the text instead."); }
      });
    });
  }

  // ---------- ask (retrieval-augmented)
  function answerHTML(res) {
    const cite = (t) => esc(t).replace(/\[((?:§|P-|run)[^\]]{0,24})\]/g, '<span class="cite-tag">$1</span>');
    const src = (res.sources || []).slice(0, 6).map((x) => `<li><span class="mono">${esc(x.ref)}</span> ${esc(x.title)}<span class="sub">${esc(x.doc)} · ${x.bm25_rank ? `BM25 #${x.bm25_rank}` : "BM25 —"}${x.dense_rank ? ` · dense #${x.dense_rank}` : ""}</span>${res.answer ? "" : `<p>${esc(x.text)}</p>`}</li>`).join("");
    return `${res.answer ? `<div class="answer">${res.answer.split(/\n+/).map((p) => `<p>${cite(p)}</p>`).join("")}</div>` : `<p class="check-detail">${esc(res.note || "")}</p>`}
      <details class="sources" ${res.answer ? "" : "open"}><summary>${res.answer ? "Sources" : "Most relevant passages"} · ${esc(res.retrieval || "")}</summary><ul>${src}</ul></details>`;
  }
  function bindAsk(el, run) {
    const form = $("#ask-form", el);
    if (!form) return;
    const go = async (q) => {
      if (!q.trim()) return;
      const out = $("#ask-out", el);
      out.innerHTML = `<div class="skeleton" style="width:90%"></div><div class="skeleton" style="width:70%"></div>`;
      try {
        const res = await api("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q, run_id: run.id }) });
        S.answers[run.id] = `<p class="q">${esc(q)}</p>${answerHTML(res)}`;
        out.innerHTML = S.answers[run.id];
      } catch (e) { out.innerHTML = `<div class="error-box">${esc(e.message)}</div>`; }
    };
    form.addEventListener("submit", (e) => { e.preventDefault(); go($("#ask-q", el).value); });
    $$("[data-q]", el).forEach((b) => b.addEventListener("click", () => { $("#ask-q", el).value = b.dataset.q; go(b.dataset.q); }));
  }

  // ---------- document drawer
  function openDoc(run, tab) {
    if (!run) return;
    const arts = run.artifacts || [];
    const tabs = [["original", "As received"]];
    if (arts.includes("p1-clean.jpg")) tabs.push(["clean", "Cleaned"]);
    if (arts.includes("p1-layout.jpg")) tabs.push(["layout", "Regions"]);
    tab = tab && tabs.some((t) => t[0] === tab) ? tab : "original";
    $("#doc-title").textContent = run.filename;
    $("#doc-sub").textContent = `${run.id} · ${tabs.find((t) => t[0] === tab)[1].toLowerCase()}`;
    $("#doc-tabs").innerHTML = tabs.length > 1 ? `<div class="seg" role="tablist">${tabs.map(([k, l]) => `<button type="button" role="tab" data-tab="${k}" aria-pressed="${k === tab}">${l}</button>`).join("")}</div>` : "";
    const body = $("#doc-body");
    if (tab === "original" && (run.kind || "pdf") === "pdf") body.innerHTML = `<iframe title="Invoice document" src="/api/runs/${encodeURIComponent(run.id)}/document#view=FitH"></iframe>`;
    else if (tab === "original") body.innerHTML = `<div class="doc-img"><img alt="Invoice as received" src="/api/runs/${encodeURIComponent(run.id)}/document"></div>`;
    else body.innerHTML = `<div class="doc-img"><img alt="Invoice ${tab}" src="${art(run, tab === "clean" ? "p1-clean.jpg" : "p1-layout.jpg")}"></div>`;
    $$("[data-tab]").forEach((b) => b.addEventListener("click", () => openDoc(run, b.dataset.tab)));
    const d = $("#doc-drawer");
    d.classList.add("open");
    d.setAttribute("aria-hidden", "false");
    $("#doc-close").focus();
  }
  function closeDoc() {
    const d = $("#doc-drawer");
    d.classList.remove("open");
    d.setAttribute("aria-hidden", "true");
  }
  $("#doc-close").addEventListener("click", closeDoc);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDoc(); });

  // ------------------------------------------------------------------ runs dashboard
  async function renderRuns() {
    S.mounted = null;
    await refreshRuns();
    const runs = S.runs.filter((r) => r.status === "done");
    const head = `<div class="page-head"><div><h1 class="page-title">Runs</h1><p class="page-lede">Every invoice processed, its decision and where it went. Select a run to replay its trail.</p></div></div>`;
    if (!S.runs.length) {
      view.innerHTML = `${head}<div class="empty"><div><h3>No runs yet</h3><p>Process an invoice or run the demo scenarios to fill this page.</p><div style="margin-top:16px"><a class="btn" href="#/process">Go to Process</a></div></div></div>`;
      return;
    }
    const count = (d) => runs.filter((r) => r.decision === d).length;
    const total = runs.reduce((a, r) => a + (r.total || 0), 0);
    const held = runs.filter((r) => r.decision === "hold");
    const auto = runs.filter((r) => r.auto_decision === "approved").length;
    const caught = runs.filter((r) => r.auto_decision !== "approved");
    const durs = runs.map((r) => r.duration_ms).filter((x) => x != null).sort((a, b) => a - b);
    const median = durs.length ? durs[Math.floor(durs.length / 2)] : null;
    const n = runs.length || 1;
    const mix = ["approved", "hold", "rejected"].map((d) => ({ d, c: count(d) }));
    const messy = runs.filter((r) => r.source && r.source !== "text");
    const repaired = runs.reduce((a, r) => a + (r.repairs || 0), 0);
    const illegible = runs.filter((r) => r.grade === "unreadable").length;

    view.innerHTML = `${head}
      <section class="strip" aria-label="Summary">
        <div class="stat"><div class="stat-label">Invoices processed</div><div class="stat-value">${runs.length}</div><div class="stat-note">${inrShort(total)} in value</div></div>
        <div class="stat"><div class="stat-label">Straight-through</div><div class="stat-value">${Math.round((auto / n) * 100)}%</div><div class="stat-note">${auto} approved with no touch</div></div>
        <div class="stat"><div class="stat-label">Waiting on review</div><div class="stat-value">${held.length}</div><div class="stat-note">${inrShort(held.reduce((a, r) => a + (r.total || 0), 0))} held</div></div>
        <div class="stat"><div class="stat-label">Exceptions caught</div><div class="stat-value">${caught.length}</div><div class="stat-note">${inrShort(caught.reduce((a, r) => a + (r.total || 0), 0))} not paid automatically</div></div>
        <div class="stat"><div class="stat-label">Scans &amp; photos</div><div class="stat-value">${messy.length}</div><div class="stat-note">${repaired} field${repaired !== 1 ? "s" : ""} repaired · ${illegible} sent back</div></div>
        <div class="stat"><div class="stat-label">Median time</div><div class="stat-value">${secs(median)}</div><div class="stat-note">document to decision</div></div>
        <div class="mix">
          <div class="mix-bar" role="img" aria-label="${mix.map((m) => `${m.c} ${LABEL[m.d]}`).join(", ")}">${mix.map((m) => `<span class="bg-${m.d}" style="flex-grow:${m.c}"></span>`).join("")}</div>
          <div class="mix-legend">${mix.map((m) => `<span><i class="bg-${m.d}"></i>${LABEL[m.d]} ${m.c}</span>`).join("")}</div>
        </div>
      </section>
      <div class="toolbar">
        <div class="seg" role="group" aria-label="Filter by decision">
          ${["all", "approved", "hold", "rejected"].map((f) => `<button type="button" data-filter="${f}" aria-pressed="${S.filter === f}">${f === "all" ? "All" : LABEL[f]}</button>`).join("")}
        </div>
        <label class="search">${icon("search")}<input id="q" type="search" placeholder="Search vendor, invoice or PO" value="${esc(S.q)}" aria-label="Search runs"></label>
      </div>
      <div class="table-wrap"><table class="runs-table">
        <thead><tr><th>Run</th><th>Vendor · invoice</th><th class="num">Amount</th><th>PO</th><th>Decision</th><th>Routed to</th><th>Input</th><th>Reader</th><th class="num">Time</th></tr></thead>
        <tbody id="runs-body"></tbody>
      </table></div>`;
    paintRunsBody();
    $$("[data-filter]").forEach((b) => b.addEventListener("click", () => {
      S.filter = b.dataset.filter;
      $$("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      paintRunsBody();
    }));
    $("#q").addEventListener("input", (e) => { S.q = e.target.value; paintRunsBody(); });
  }

  function paintRunsBody() {
    const q = S.q.trim().toLowerCase();
    const rows = S.runs.filter((r) => (S.filter === "all" || r.decision === S.filter) &&
      (!q || [r.vendor, r.invoice_number, r.po_id, r.id, r.filename].some((x) => (x || "").toLowerCase().includes(q))));
    const body = $("#runs-body");
    if (!rows.length) { body.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--ink-3);padding:28px">No runs match this filter.</td></tr>`; return; }
    body.innerHTML = rows.map((r) => {
      const dec = r.status === "done" ? chip(r.decision) : r.status === "error" ? chip("rejected", "Error") : chip("running", "Running");
      const sub = r.decision === "hold" ? '<span class="needs">Needs review</span>' : r.reviewed ? '<span class="sub">Reviewed</span>' : "";
      return `<tr data-run="${esc(r.id)}" tabindex="0">
        <td><span class="mono">${esc(r.id)}</span><span class="sub">${fmtDay(r.created_at)}, ${fmtTime(r.created_at)}</span></td>
        <td>${esc(r.vendor || "Unknown vendor")}<span class="sub mono">${esc(r.invoice_number || r.filename)}</span></td>
        <td class="num">${inr(r.total)}</td>
        <td>${r.po_id ? esc(r.po_id) : "—"}${r.po_method === "inferred" ? '<span class="inferred">inferred</span>' : ""}</td>
        <td>${dec}${sub}</td>
        <td>${esc(r.queue || "—")}</td>
        <td>${r.source ? `${esc(SRC[r.source] || r.source)}${r.source !== "text" && r.score != null ? `<span class="sub">legibility ${r.score}</span>` : ""}` : "—"}</td>
        <td>${r.engine === "claude" ? "Claude" : r.engine === "local" ? "Local OCR" : "—"}</td>
        <td class="num">${secs(r.duration_ms)}</td></tr>`;
    }).join("");
    $$("tr[data-run]", body).forEach((tr) => {
      const go = () => { S.current = null; location.hash = `#/run/${tr.dataset.run}`; };
      tr.addEventListener("click", go);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    });
  }

  // ------------------------------------------------------------------ data page
  async function renderData() {
    S.mounted = null;
    const m = await api("/api/master");
    const p = m.policy;
    view.innerHTML = `
      <div class="page-head"><div><h1 class="page-title">Purchase orders &amp; vendors</h1><p class="page-lede">The reference data every invoice is checked against. Balances update as invoices are approved.</p></div></div>
      <div class="data-grid">
        <section>
          <h2 class="section-title">Matching policy</h2>
          <div class="policy">
            <div><b>±${p.line_price_tolerance_pct}%</b><span>Unit price tolerance per line against the PO</span></div>
            <div><b>${p.po_overrun_tolerance_pct}%</b><span>Allowed overrun on the PO balance</span></div>
            <div><b>${p.duplicate_window_days} days</b><span>Window for same-vendor, same-amount duplicates</span></div>
            <div><b>${Math.round(p.inferred_po_min_confidence * 100)}%</b><span>Minimum confidence to suggest an inferred PO (always reviewed)</span></div>
            <div><b>Bank match</b><span>Payment details must equal the vendor master</span></div>
          </div>
        </section>
        <section>
          <h2 class="section-title">Purchase orders</h2>
          <div class="table-wrap"><table>
            <thead><tr><th>PO</th><th>Vendor</th><th>For</th><th class="num">Ordered</th><th>Invoiced</th><th class="num">Remaining</th></tr></thead>
            <tbody>${m.purchase_orders.map((po) => {
              const pct = po.ordered ? Math.min(100, (po.invoiced / po.ordered) * 100) : 0;
              return `<tr><td class="mono">${esc(po.id)}</td><td>${esc(po.vendor_name)}</td><td>${esc(po.description)}<span class="sub">${po.lines.map((l) => `${l.quantity} × ${esc(l.description)} @ ${inr(l.unit_price)}`).join("; ")}</span></td>
                <td class="num">${inr(po.ordered)}</td>
                <td><div class="bal"><div class="bal-bar"><span class="${pct >= 99.9 ? "full" : ""}" style="width:${pct}%"></span></div><span class="tnum" style="font-size:12px;color:var(--ink-2)">${Math.round(pct)}%</span></div></td>
                <td class="num">${inr(po.remaining)}</td></tr>`;
            }).join("")}</tbody>
          </table></div>
        </section>
        <section>
          <h2 class="section-title">AP ledger</h2>
          <div class="table-wrap"><table>
            <thead><tr><th>Invoice</th><th>Vendor</th><th>PO</th><th class="num">Subtotal</th><th class="num">Total</th><th>Source</th></tr></thead>
            <tbody>${m.ledger.map((e) => `<tr><td class="mono">${esc(e.invoice_number)}<span class="sub">${fmtDate(e.invoice_date)}</span></td><td>${esc(e.vendor_name)}</td><td>${esc(e.po_id || "—")}</td><td class="num">${inr(e.subtotal)}</td><td class="num">${inr(e.total)}</td><td>${e.run_id ? `<a href="#/run/${esc(e.run_id)}">${esc(e.source)}</a>` : esc(e.source)}</td></tr>`).join("")}</tbody>
          </table></div>
        </section>
        <section>
          <h2 class="section-title">Vendor master</h2>
          <div class="table-wrap"><table>
            <thead><tr><th>Vendor</th><th>GSTIN</th><th>Status</th><th>Bank on file</th><th>Terms</th><th>Contact</th></tr></thead>
            <tbody>${m.vendors.map((v) => `<tr><td>${esc(v.name)}<span class="sub mono">${esc(v.id)}</span></td><td class="mono">${esc(v.gstin)}</td>
              <td>${v.status === "approved" ? chip("approved", "Approved") : chip("rejected", v.status)}${v.status_reason ? `<span class="sub">${esc(v.status_reason)}</span>` : ""}</td>
              <td>${esc(v.bank.bank)} ${mask(v.bank.account)}<span class="sub mono">${esc(v.bank.ifsc)}</span></td><td>Net ${v.payment_terms_days} · GST ${v.gst_rate}%</td>
              <td>${esc(v.contact.name)}<span class="sub">${esc(v.contact.phone)}</span></td></tr>`).join("")}</tbody>
          </table></div>
        </section>
      </div>`;
  }

  // ------------------------------------------------------------------ knowledge page
  async function renderKnowledge() {
    S.mounted = null;
    view.innerHTML = `<div class="page-head"><div><h1 class="page-title">Knowledge</h1><p class="page-lede">The AP policy, GST notes and past reviewer decisions the desk cites. Every check points at its clause; exceptions are matched to precedent by hybrid search.</p></div></div><div class="panel panel-pad"><div class="skeleton"></div><div class="skeleton" style="width:70%"></div></div>`;
    const k = await api("/api/knowledge");
    const pol = k.docs["ap-policy"] || [], gst = k.docs["gst-notes"] || [], pre = k.docs["precedent"] || [];
    const bySection = {};
    [...pol, ...gst].forEach((c) => { (bySection[c.section] = bySection[c.section] || []).push(c); });
    const refCheck = {};
    Object.entries(k.policy_ref).forEach(([cid, ref]) => { (refCheck[ref] = refCheck[ref] || []).push(cid); });
    view.innerHTML = `
      <div class="page-head"><div><h1 class="page-title">Knowledge</h1><p class="page-lede">The AP policy, GST notes and past reviewer decisions the desk cites. Every check points at its clause; exceptions are matched to precedent by hybrid search.</p></div>
        <span class="chip">${icon("spark")}${esc(k.retrieval)}</span></div>
      <div class="kb-search panel panel-pad">
        <label class="search big">${icon("search")}<input id="kq" type="search" placeholder="Try: supplier changed bank account · photo is blurry · split invoices over PO" aria-label="Search the knowledge base"></label>
        <div id="kres" class="kres"></div>
      </div>
      <div class="kb-grid">
        <section>
          <h2 class="section-title">Policy and GST notes · ${pol.length + gst.length} clauses</h2>
          <div class="kb-list">${Object.entries(bySection).map(([sec, cs]) => `<details class="kb-sec"><summary>${esc(sec)}<span class="sub-inline">${cs.length}</span></summary>
            ${cs.map((c) => `<div class="kb-item"><div><span class="mono">${esc(c.ref)}</span> <b>${esc(c.title)}</b>${(refCheck[c.ref] || []).map((x) => ` <span class="chip">${esc(x)}</span>`).join("")}</div><p>${esc(c.text)}</p></div>`).join("")}</details>`).join("")}</div>
        </section>
        <section>
          <h2 class="section-title">Past decisions · ${pre.length}</h2>
          <ul class="cases">${pre.slice().reverse().map((c) => `<li><div class="case-top"><span class="mono">${esc(c.ref)}</span>${chip(c.decision === "approved" ? "approved" : c.decision === "rejected" ? "rejected" : "hold", c.decision)}<span class="sub-inline">${esc(c.vendor)} · ${esc(c.check)}${c.run_id ? ` · <a href="#/run/${esc(c.run_id)}">from ${esc(c.run_id)}</a>` : ""}</span></div><div class="case-facts">${esc(c.text)}</div></li>`).join("")}</ul>
        </section>
      </div>`;
    let t;
    $("#kq").addEventListener("input", (e) => {
      clearTimeout(t);
      const q = e.target.value;
      t = setTimeout(async () => {
        if (!q.trim()) { $("#kres").innerHTML = ""; return; }
        const r = await api(`/api/knowledge/search?q=${encodeURIComponent(q)}&k=5`);
        $("#kres").innerHTML = r.hits.length ? `<ol class="hits">${r.hits.map((h) => `<li><div><span class="mono">${esc(h.ref)}</span> <b>${esc(h.title)}</b><span class="rank">${h.bm25_rank ? `BM25 #${h.bm25_rank}` : "BM25 —"}${h.dense_rank ? ` · dense #${h.dense_rank} (${h.dense_sim})` : ""} · fused ${h.score}</span></div><p>${esc(h.text)}</p></li>`).join("")}</ol>` : `<p class="sub">No matches.</p>`;
      }, 220);
    });
  }

  // ------------------------------------------------------------------ reset
  $("#reset-btn").addEventListener("click", async () => {
    if (S.batch) { toast("Wait for the scenarios to finish first."); return; }
    if (!confirm("Reset the demo? This clears every run and restores the ledger and PO balances.")) return;
    await api("/api/reset", { method: "POST" });
    S.current = null; S.mounted = null; S.revealed.clear(); S.open.clear(); S.answers = {};
    await refreshRuns();
    toast("Demo reset. Ledger and PO balances restored.");
    if (location.hash === "#/process") router(); else location.hash = "#/process";
  });

  // ------------------------------------------------------------------ boot
  (async function boot() {
    hydrateIcons();
    paintThemeButton();
    S.config = await api("/api/config");
    paintEngine();
    await refreshRuns();
    router();
  })();
})();
