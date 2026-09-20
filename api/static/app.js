const $ = (id) => document.getElementById(id);
const state = { view: "live", selected: null };

const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function ago(iso) {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

async function get(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

function localInput(date) {
  const off = date.getTimezoneOffset();
  const local = new Date(date.getTime() - off * 60000);
  return local.toISOString().slice(0, 16);
}

function isoFromInput(id) {
  const v = $(id).value;
  if (!v) return "";
  return new Date(v).toISOString();
}

function rssiLabel(v) {
  if (v == null) return "—";
  return `${v} dBm`;
}

const ENV_N = { open: "2.0", indoor: "2.7", dense: "3.5" };

function modelParams() {
  const p = new URLSearchParams();
  const env = $("env").value;
  if (env && env !== "custom") p.set("env", env);
  const n = $("path-n").value;
  const tx = $("tx-power").value;
  if (n) p.set("n", n);
  if (tx) p.set("tx_power", tx);
  return p;
}

function distCell(d) {
  if (d.distance_human == null) return "—";
  const src = d.distance_tx_source === "advertised" ? "adv. 1 m" : "cal. 1 m";
  return `<span class="dist">${d.distance_human}<div class="sub">${src}</div></span>`;
}

function visitCell(d) {
  if (d.present) return `<span class="pill on">in range ${d.current_visit_human || ""}</span>`;
  return d.last_visit_human || "—";
}

function rowHtml(d) {
  return `<tr data-addr="${d.address}">
    <td class="addr">${d.address}<div class="sub">${d.address_type || ""}</div></td>
    <td><div class="name">${d.name || "—"}</div><div class="sub">${d.device_type || ""}</div></td>
    <td>${d.manufacturer_name || "—"}</td>
    <td class="rssi">${rssiLabel(d.last_rssi)}</td>
    <td>${distCell(d)}</td>
    <td>${visitCell(d)}</td>
    <td>${d.total_dwell_human || "0s"}</td>
    <td>${d.visit_count ?? "—"}</td>
    <td>${ago(d.last_seen)}<div class="sub">${fmtTime(d.last_seen)}</div></td>
  </tr>`;
}

async function loadKpis() {
  const s = await get("/api/stats?hours=24");
  $("kpis").innerHTML = [
    ["Present", s.present],
    ["Last 24h", s.recently_seen],
    ["All devices", s.devices],
    ["Sightings", s.sightings],
    ["Time seen", s.dwell_human],
  ].map(([k, v]) => `<div class="kpi"><b>${v ?? "—"}</b><span>${k}</span></div>`).join("");
}

async function loadTypes() {
  const { types } = await get("/api/types");
  const sel = $("type-filter");
  const current = sel.value;
  sel.innerHTML = `<option value="">All types</option>` + types.map((t) => `<option>${t}</option>`).join("");
  sel.value = current;
}

async function loadList() {
  const params = new URLSearchParams();
  const q = $("q").value.trim();
  if (q) params.set("q", q);
  params.set("order", $("order").value);
  const type = $("type-filter").value;
  if (type) params.set("device_type", type);
  if (state.view === "live") params.set("present", "true");
  if (state.view === "repeats") params.set("repeats", "true");
  if (state.view === "window") {
    const since = isoFromInput("since");
    const until = isoFromInput("until");
    if (since) params.set("since", since);
    if (until) params.set("until", until);
  }
  const model = modelParams();
  for (const [k, v] of model) params.set(k, v);
  let data;
  if (state.view === "window" && (isoFromInput("since") || isoFromInput("until"))) {
    const p = new URLSearchParams(model);
    if (isoFromInput("since")) p.set("since", isoFromInput("since"));
    if (isoFromInput("until")) p.set("until", isoFromInput("until"));
    data = await get(`/api/seen?${p}`);
  } else {
    data = await get(`/api/devices?${params}`);
  }
  const devices = data.devices || [];
  $("rows").innerHTML = devices.map(rowHtml).join("") || `<tr><td colspan="9" class="hint">No devices in this view.</td></tr>`;
  const hint = {
    live: "Devices heard in the last 30 seconds. Distance is an RSSI estimate.",
    devices: "Every address logged so far. Sort by Closest to rank by estimated range.",
    repeats: "Addresses that came back after a gap of two minutes or more.",
    window: "Devices with at least one advertisement inside the selected window.",
  }[state.view] || "";
  $("list-hint").textContent = `${devices.length} shown. ${hint}`;
}

function heatColor(n, max) {
  if (!n) return "#1e221a";
  const t = Math.min(1, n / max);
  const r = 70 + t * 140;
  const g = 90 + t * 70;
  const b = 40;
  return `rgb(${r|0},${g|0},${b|0})`;
}

async function loadDistance() {
  const p = modelParams();
  p.set("bins", $("dist-bins").value || "1,3,8");
  if ($("dist-present").checked) p.set("present", "true");
  if (isoFromInput("since")) p.set("since", isoFromInput("since"));
  if (isoFromInput("until")) p.set("until", isoFromInput("until"));
  const r = await get(`/api/report/distance?${p}`);
  const max = Math.max(1, ...r.buckets.map((b) => b.count));
  const bars = r.buckets.map((b) => barRow(b.label, b.count, max)).join("");
  const sections = r.buckets.map((b) => `
    <h3>${b.label} <span class="hint">(${b.count})</span></h3>
    <div class="table-wrap"><table><thead><tr>
      <th>Address</th><th>Name</th><th>Type</th><th>RSSI</th><th>~Distance</th><th>Last seen</th>
    </tr></thead>
    <tbody>${(b.devices || []).map((d) => `<tr data-addr="${d.address}">
      <td class="addr">${d.address}</td>
      <td>${d.name || "—"}<div class="sub">${d.device_type || ""}</div></td>
      <td>${d.manufacturer_name || "—"}</td>
      <td class="rssi">${rssiLabel(d.last_rssi)}</td>
      <td>${d.distance_human || "—"}</td>
      <td>${ago(d.last_seen)}</td>
    </tr>`).join("") || `<tr><td colspan="6" class="hint">None in this band.</td></tr>`}</tbody></table></div>
  `).join("");
  $("distance-report").innerHTML = `
    <p class="hint">${r.note} Using n=${r.path_loss_n}, 1 m = ${r.tx_power_1m} dBm. ${r.measured} of ${r.count} devices have RSSI.</p>
    <div class="grid">
      <div><b>${r.measured}</b><span>With a range estimate</span></div>
      <div><b>${r.nearest ? r.nearest.distance_human : "—"}</b><span>Nearest ${r.nearest ? (r.nearest.name || r.nearest.address) : ""}</span></div>
      <div><b>${r.farthest ? r.farthest.distance_human : "—"}</b><span>Farthest ${r.farthest ? (r.farthest.name || r.farthest.address) : ""}</span></div>
      <div><b>${r.unknown}</b><span>No RSSI</span></div>
    </div>
    <div class="bars">${bars}</div>
    ${sections}
  `;
}

async function loadPattern() {
  const data = await get("/api/pattern?days=14");
  const max = Math.max(1, ...data.cells.map((c) => c.sightings));
  const map = new Map(data.cells.map((c) => [`${c.dow}-${c.hour}`, c.sightings]));
  let html = `<div class="lab"></div>`;
  for (let h = 0; h < 24; h++) html += `<div class="lab">${h}</div>`;
  for (let d = 0; d < 7; d++) {
    html += `<div class="lab">${DOW[d]}</div>`;
    for (let h = 0; h < 24; h++) {
      const n = map.get(`${d}-${h}`) || 0;
      html += `<div class="cell" data-n="${n}" title="${DOW[d]} ${h}:00 — ${n} sightings" style="background:${heatColor(n, max)}"></div>`;
    }
  }
  $("heatmap").innerHTML = html;
}

function barRow(label, n, max) {
  const pct = max ? Math.round((n / max) * 100) : 0;
  return `<div class="bar"><span>${label}</span><i style="width:${pct}%"></i><b>${n}</b></div>`;
}

async function loadReport() {
  const p = modelParams();
  if (isoFromInput("since")) p.set("since", isoFromInput("since"));
  if (isoFromInput("until")) p.set("until", isoFromInput("until"));
  const r = await get(`/api/report?${p}`);
  const maxT = Math.max(1, ...r.types.map((x) => x.n));
  const maxM = Math.max(1, ...r.manufacturers.map((x) => x.n));
  $("report").innerHTML = `
    <div class="grid">
      <div><b>${r.devices}</b><span>Devices</span></div>
      <div><b>${r.sightings}</b><span>Sightings</span></div>
      <div><b>${r.visits}</b><span>Visits</span></div>
      <div><b>${r.dwell_human}</b><span>Total time seen</span></div>
    </div>
    <h3>Longest total time seen</h3>
    <div class="table-wrap"><table><thead><tr><th>Address</th><th>Name</th><th>Type</th><th>~Distance</th><th>Total seen</th><th>Visits</th></tr></thead>
    <tbody>${(r.longest_seen || []).map((d) => `<tr data-addr="${d.address}"><td class="addr">${d.address}</td><td>${d.name || "—"}</td><td>${d.device_type || "—"}</td><td>${d.distance_human || "—"}</td><td>${d.total_dwell_human}</td><td>${d.visit_count}</td></tr>`).join("")}</tbody></table></div>
    <h3>Types</h3><div class="bars">${r.types.map((x) => barRow(x.device_type, x.n, maxT)).join("")}</div>
    <h3>Manufacturers</h3><div class="bars">${r.manufacturers.map((x) => barRow(x.manufacturer_name, x.n, maxM)).join("")}</div>
  `;
}

async function openDevice(address) {
  state.selected = address;
  $("drawer").classList.remove("hidden");
  $("detail").innerHTML = "<p class='hint'>Loading…</p>";
  const [d, hist, visits, pattern] = await Promise.all([
    get(`/api/devices/${address}?${modelParams()}`),
    get(`/api/devices/${address}/history?limit=80`),
    get(`/api/devices/${address}/visits?limit=40`),
    get(`/api/devices/${address}/pattern?days=14`),
  ]);
  const max = Math.max(1, ...pattern.cells.map((c) => c.sightings));
  const map = new Map(pattern.cells.map((c) => [`${c.dow}-${c.hour}`, c.sightings]));
  let heat = `<div class="heatmap">`;
  heat += `<div class="lab"></div>`;
  for (let h = 0; h < 24; h++) heat += `<div class="lab">${h}</div>`;
  for (let day = 0; day < 7; day++) {
    heat += `<div class="lab">${DOW[day]}</div>`;
    for (let h = 0; h < 24; h++) {
      const n = map.get(`${day}-${h}`) || 0;
      heat += `<div class="cell" title="${n}" style="background:${heatColor(n, max)}"></div>`;
    }
  }
  heat += `</div>`;
  const fields = (d.last_parsed && d.last_parsed.fields) || [];
  $("detail").innerHTML = `
    <p class="pill ${d.present ? "on" : ""}">${d.present ? "in range" : "away"}</p>
    <h2>${d.name || d.address}</h2>
    <p class="addr">${d.address} · ${d.address_type} · ${d.device_type || "Unknown"}</p>
    <div class="grid">
      <div><b>${d.current_visit_human || d.last_visit_human || "—"}</b><span>${d.present ? "This visit" : "Last visit"}</span></div>
      <div><b>${d.total_dwell_human}</b><span>Total time seen</span></div>
      <div><b>${d.visit_count}</b><span>Visits</span></div>
      <div><b>${d.known_span_human || "—"}</b><span>First → last seen</span></div>
      <div><b>${rssiLabel(d.last_rssi)}</b><span>Last RSSI</span></div>
      <div><b>${d.distance_human || "—"}</b><span>Approx. distance</span></div>
      <div><b>${d.manufacturer_name || "—"}</b><span>Manufacturer</span></div>
    </div>
    <p class="hint">First seen ${fmtTime(d.first_seen)} · Last seen ${fmtTime(d.last_seen)}</p>
    <h3>Latest packet</h3>
    <div class="hex">${d.last_payload_hex || ""}</div>
    <p class="hint">${fields.map((f) => f.type + (f.text ? `: ${f.text}` : "") + (f.company ? `: ${f.company}` : "")).join(" · ")}</p>
    <h3>Visit history</h3>
    ${(visits.visits || []).map((v) => `
      <div class="visit ${v.open ? "open" : ""}">
        <div>${fmtTime(v.start_time)} → ${v.end_time ? fmtTime(v.end_time) : "now"}</div>
        <div class="dur">${v.duration_human}${v.open ? " · open" : ""}</div>
      </div>`).join("") || "<p class='hint'>No visits yet.</p>"}
    <h3>When this device is seen</h3>
    ${heat}
    <h3>Recent sightings</h3>
    ${(hist.sightings || []).slice(0, 25).map((s) => `
      <div class="visit"><div>${fmtTime(s.time)} · ${s.adv_type || ""}</div><div class="rssi">${rssiLabel(s.rssi)}</div></div>
    `).join("")}
  `;
}

async function refresh() {
  await loadKpis();
  $("list-view").classList.add("hidden");
  $("pattern-view").classList.add("hidden");
  $("report-view").classList.add("hidden");
  $("distance-view").classList.add("hidden");
  if (state.view === "patterns") {
    $("pattern-view").classList.remove("hidden");
    await loadPattern();
    return;
  }
  if (state.view === "report") {
    $("report-view").classList.remove("hidden");
    await loadReport();
    return;
  }
  if (state.view === "distance") {
    $("distance-view").classList.remove("hidden");
    await loadDistance();
    return;
  }
  $("list-view").classList.remove("hidden");
  await loadList();
}

$("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  state.view = btn.dataset.view;
  for (const b of $("tabs").querySelectorAll("button")) b.classList.toggle("active", b === btn);
  refresh().catch(console.error);
});

$("rows").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-addr]");
  if (tr) openDevice(tr.dataset.addr).catch(console.error);
});
$("report").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-addr]");
  if (tr) openDevice(tr.dataset.addr).catch(console.error);
});
$("distance-report").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-addr]");
  if (tr) openDevice(tr.dataset.addr).catch(console.error);
});
$("env").addEventListener("change", () => {
  const env = $("env").value;
  if (ENV_N[env]) $("path-n").value = ENV_N[env];
  refresh().catch(console.error);
});
$("path-n").addEventListener("change", () => {
  $("env").value = "custom";
  refresh().catch(console.error);
});
$("tx-power").addEventListener("change", () => refresh().catch(console.error));
$("dist-run").addEventListener("click", () => loadDistance().catch(console.error));
$("close-drawer").addEventListener("click", () => $("drawer").classList.add("hidden"));
$("reload").addEventListener("click", () => refresh().catch(console.error));
$("q").addEventListener("keydown", (e) => { if (e.key === "Enter") refresh().catch(console.error); });
$("order").addEventListener("change", () => refresh().catch(console.error));
$("type-filter").addEventListener("change", () => refresh().catch(console.error));

const now = new Date();
$("until").value = localInput(now);
$("since").value = localInput(new Date(now.getTime() - 24 * 3600 * 1000));

loadTypes().catch(console.error);
refresh().catch(console.error);
setInterval(() => refresh().catch(console.error), 8000);
