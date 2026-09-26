async function loadStats() {
  try {
    var r = await apiFetch("/api/stats");
    if (r.status === 401) {
      window.location = "/login";
      return;
    }
    if (!r.ok) return;
    stats = await r.json();
    var t = stats.today || {};
    var sg = document.getElementById("stat-grid");
    if (sg)
      updateHTML(sg, '<div class="stat-card c-accent"><div class="stat-icon"><i class="ph ph-chart-bar"></i></div><div class="stat-label">Today Total</div><div class="stat-value v-accent">' +
        (t.total || 0) +
        "</div></div>" +
        '<div class="stat-card c-blue"><div class="stat-icon"><i class="ph ph-user-check"></i></div><div class="stat-label">Assigned</div><div class="stat-value v-blue">' +
        (t.assigned || 0) +
        "</div></div>" +
        '<div class="stat-card c-green"><div class="stat-icon"><i class="ph ph-check-circle"></i></div><div class="stat-label">Resolved</div><div class="stat-value v-green">' +
        (t.done || 0) +
        "</div></div>" +
        '<div class="stat-card c-red"><div class="stat-icon"><i class="ph ph-warning-circle"></i></div><div class="stat-label">Missed</div><div class="stat-value v-red">' +
        (t.missed || 0) +
        "</div></div>" +
        '<div class="stat-card c-purple"><div class="stat-icon"><i class="ph ph-arrows-clockwise"></i></div><div class="stat-label">Reassigned · all time</div><div class="stat-value v-purple">' +
        (stats.reassigned_count || 0) +
        "</div></div>" +
        '<div class="stat-card c-yellow"><div class="stat-icon"><i class="ph ph-timer"></i></div><div class="stat-label">Avg response · all time</div><div class="stat-value v-sm v-yellow">' +
        ((stats.all_time || {}).avg_resp || "—") +
        "</div></div>");

    var badge = document.getElementById("missed-badge");
    if (badge) {
      if (t.missed > 0) {
        badge.textContent = t.missed;
        badge.style.display = "";
      } else badge.style.display = "none";
    }

    var lb = (stats.leaderboard_day || []).slice(0, 5);
    var lbo = document.getElementById("lb-overview");
    if (lbo) updateHTML(lbo, listRows(lb, lb[0] ? lb[0].count : 1));

    var grps = stats.top_groups || [];
    var units = stats.top_problem_units || [];
    var uo = document.getElementById("units-overview");
    if (uo) updateHTML(uo, unitProblemRows(units));

    renderLeaderboard();
    renderAnalytics();

    var wc = document.getElementById("word-cloud");
    if (wc)
      updateHTML(wc, (stats.top_words || []).length
        ? '<div class="word-grid">' +
          (stats.top_words || [])
            .map(function (w) {
              return (
                '<span class="word-tag">' +
                h(w.word) +
                " <b>" +
                h(w.count) +
                "</b></span>"
              );
            })
            .join("") +
          "</div>"
        : '<div style="color:var(--muted);font-size:13px">No hashtag keywords yet</div>');

    var ulb = document.getElementById("units-lb");
    if (ulb) updateHTML(ulb, unitProblemRows(units));
  } catch (e) {
    if (e.name === "AbortError") return;
    if (!document.querySelector("#stat-grid .stat-card"))
      updateHTML(document.getElementById("stat-grid"), errorContent(e));
  }
}

function renderLeaderboard() {
  if (!stats.leaderboard_day) return;
  var lb = stats["leaderboard_" + lbPeriod] || [];
  var el = document.getElementById("leaderboard-full");
  if (!el) return;
  updateHTML(el, lb.length
    ? lb
        .map(function (a, i) {
          return (
            '<div class="list-row"><span class="medal">' +
            (medals[i] || i + 1 + ".") +
            '</span><span class="list-name">' +
            h(a.name) +
            '</span><span class="list-count">' +
            h(a.count) +
            " cases</span></div>"
          );
        })
        .join("")
    : '<div style="color:var(--muted);font-size:13px;padding:8px 0">No data</div>');
}

function renderAnalytics() {
  if (!stats.week) return;
  var d = analyticsPeriod === "week" ? stats.week : stats.month;
  var rate = d.total ? Math.round((d.done / d.total) * 100) : 0;
  var el = document.getElementById("analytics-stats");
  if (!el) return;
  updateHTML(el, '<div class="row"><span>Total Cases</span><span class="val">' +
    d.total +
    "</span></div>" +
    '<div class="row"><span>Resolved</span><span class="val" style="color:var(--text)">' +
    d.done +
    "</span></div>" +
    '<div class="row"><span>Missed</span><span class="val" style="color:var(--text)">' +
    d.missed +
    "</span></div>" +
    '<div class="row"><span>Resolution Rate</span><span class="val">' +
    rate +
    "%</span></div>" +
    '<div class="row"><span>All Time Total</span><span class="val">' +
    ((stats.all_time || {}).total || 0) +
    "</span></div>");
}

var caseLists = {
  cases: { rows: [], serial: 0, busy: false },
  missed: { rows: [], serial: 0, busy: false },
};
function loadCases(append, quiet) {
  return loadCaseList("cases", append, quiet);
}
function loadMissed(append, quiet) {
  return loadCaseList("missed", append, quiet);
}
async function loadCaseList(kind, append, quiet) {
  var state = caseLists[kind],
    el = document.getElementById(kind + "-table");
  if (append && state.busy) return;
  var serial = ++state.serial;
  state.busy = true;
  if (!append && !quiet) {
    state.rows = [];
    if (!el.children.length || el.querySelector(":scope > .loading")) updateHTML(el, '<div class="loading">Loading cases…</div>');
  }
  try {
    var params = new URLSearchParams({
      filter: kind === "missed" ? "missed" : currentFilter,
      search: document.getElementById(kind + "-search").value,
    });
    if (kind === "cases") {
      params.set("status", document.getElementById("status-filter").value);
      if (currentDateFilter) params.set("date", currentDateFilter);
    }
    var offset = append ? state.rows.length : 0,
      target = quiet ? Math.max(100, state.rows.length) : 100,
      incoming = [],
      d;
    do {
      params.set("offset", offset + incoming.length);
      params.set("limit", Math.min(500, target - incoming.length));
      var r = await apiFetch("/api/cases?" + params.toString(), kind);
      d = await r.json();
      if (serial !== state.serial) return;
      incoming = incoming.concat(d.cases || []);
    } while (quiet && d.has_more && incoming.length < target);
    state.rows = append ? state.rows.concat(incoming) : incoming;
    updateHTML(el, '<div class="table-count">Showing ' +
      state.rows.length +
      " of " +
      d.total +
      " cases</div>" +
      caseTable(state.rows) +
      (d.has_more
        ? '<div class="load-more"><button class="btn" onclick="loadCaseList(\'' +
          kind +
          "',true)\">Load more ↓</button></div>"
        : ""));
  } catch (e) {
    if (e.name === "AbortError") return;
    if (!state.rows.length) if (!el.children.length || el.querySelector(":scope > .loading")) updateHTML(el, errorContent(e));
  } finally {
    if (serial === state.serial) state.busy = false;
  }
}

async function loadFleet() {
  var el = document.getElementById("fleet-content");
  if (!el) return;
  if (!el.children.length || el.querySelector(":scope > .loading")) updateHTML(el, '<div class="loading">Loading fleet stats...</div>');
  try {
    var r = await apiFetch("/api/fleet");
    if (!r.ok) {
      updateHTML(el, '<div class="loading">Error loading fleet stats.</div>');
      return;
    }
    var d = await r.json();
    window._fleetData = d;
    function unitCard(title, items) {
      if (!items || !items.length)
        return (
          '<div class="card"><div class="card-title">' +
          title +
          '</div><div style="color:var(--muted);font-size:13px">No data yet</div></div>'
        );
      var max = items[0].count || 1;
      return (
        '<div class="card"><div class="card-title">' +
        title +
        "</div>" +
        items
          .map(function (item, i) {
            return (
              '<div class="list-row"><span class="medal">' +
              (medals[i] || i + 1 + ".") +
              "</span>" +
              '<span class="list-name">' +
              h(item.unit) +
              (item.vtype
                ? ' <span style="font-size:10px;color:var(--muted)">' +
                  h(item.vtype) +
                  "</span>"
                : "") +
              "</span>" +
              '<div class="bar-wrap"><div class="bar-fill" style="width:' +
              Math.round((item.count / max) * 100) +
              '%"></div></div>' +
              '<span class="list-count">' +
              item.count +
              "</span></div>"
            );
          })
          .join("") +
        "</div>"
      );
    }
    updateHTML(el, '<div class="stat-grid" style="margin-bottom:20px">' +
      '<div class="stat-card c-accent" style="cursor:pointer" onclick="setFleetStatusFilter(\'all\')" title="Show all cases"><div class="stat-icon"><i class="ph ph-chart-bar"></i></div><div class="stat-label">Total Reports</div><div class="stat-value v-accent">' +
      d.total_reports +
      "</div></div>" +
      '<div class="stat-card c-blue" style="cursor:pointer" onclick="setFleetStatusFilter(\'truck\')" title="Show truck cases"><div class="stat-icon"><i class="ph ph-truck"></i></div><div class="stat-label">Trucks</div><div class="stat-value v-blue">' +
      d.truck_count +
      "</div></div>" +
      '<div class="stat-card c-yellow"><div class="stat-icon"><i class="ph ph-lightning"></i></div><div class="stat-label">Active Units</div><div class="stat-value v-yellow">' +
      d.active_units +
      "</div></div>" +
      '<div class="stat-card c-green"><div class="stat-icon"><i class="ph ph-check-circle"></i></div><div class="stat-label">Repaired Units</div><div class="stat-value v-green">' +
      d.repaired_units +
      "</div></div>" +
      '<div class="stat-card c-yellow" style="cursor:pointer" onclick="setFleetStatusFilter(\'trailer\')" title="Show trailer cases"><div class="stat-icon"><i class="ph ph-package"></i></div><div class="stat-label">Trailers</div><div class="stat-value v-yellow">' +
      d.trailer_count +
      "</div></div>" +
      '<div class="stat-card c-purple" style="cursor:pointer" onclick="setFleetStatusFilter(\'reefer\')" title="Show reefer cases"><div class="stat-icon"><i class="ph ph-snowflake"></i></div><div class="stat-label">Reefers</div><div class="stat-value v-purple">' +
      d.reefer_count +
      "</div></div>" +
      "</div>" +
      '<div id="fleet-status-wrap"></div>' +
      '<div class="two-col" style="margin-bottom:16px">' +
      unitCard(
        '<i class="ph ph-truck"></i> Trucks Breaking Down Most',
        d.top_broken_trucks,
      ) +
      unitCard(
        '<i class="ph ph-user"></i> Most Reported Drivers',
        d.top_drivers,
      ) +
      "</div>" +
      '<div class="two-col">' +
      unitCard(
        '<i class="ph ph-wrench"></i> Most Reported Units',
        d.top_units,
      ) +
      unitCard('<i class="ph ph-warning"></i> Top Issues', d.top_issues) +
      "</div>");
    renderFleetStatus();
  } catch (e) {
    if (e.name === "AbortError") return;
    console.error(e);
    if (!el.children.length || el.querySelector(":scope > .loading")) updateHTML(el, errorContent(e));
  }
}

var fleetStatusState = { vtype: "all", search: "", page: 1, perPage: 20 };

function setFleetStatusFilter(vtype) {
  fleetStatusState.vtype = vtype;
  fleetStatusState.page = 1;
  renderFleetStatus();
  var wrap = document.getElementById("fleet-status-wrap");
  if (wrap) wrap.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderFleetStatus() {
  preserveInput("fleet-status-search", renderFleetStatusContent);
}
function renderFleetStatusContent() {
  var wrap = document.getElementById("fleet-status-wrap");
  if (!wrap || !window._fleetData) return;
  var s = fleetStatusState;
  var items = window._fleetData.fleet_status || [];
  var q = (s.search || "").toLowerCase().trim();
  var filtered = items.filter(function (item) {
    if (s.vtype !== "all" && (item.vtype || "").toLowerCase() !== s.vtype)
      return false;
    if (
      q &&
      (item.unit || "").toLowerCase().indexOf(q) === -1 &&
      (item.issue || "").toLowerCase().indexOf(q) === -1 &&
      (item.driver || "").toLowerCase().indexOf(q) === -1
    )
      return false;
    return true;
  });
  function vbtn(v, label) {
    return (
      '<button class="toggle-btn' +
      (s.vtype === v ? " active" : "") +
      '" onclick="fleetStatusState.vtype=\'' +
      v +
      "';fleetStatusState.page=1;renderFleetStatus()\">" +
      label +
      "</button>"
    );
  }
  var perPage = Number(s.perPage || 20);
  var totalPages = Math.max(1, Math.ceil(filtered.length / perPage));
  s.page = Math.max(1, Math.min(Number(s.page || 1), totalPages));
  var startIndex = (s.page - 1) * perPage;
  var visible = filtered.slice(startIndex, startIndex + perPage);
  var rows = visible
    .map(function (item) {
      var badge =
        item.status === "active"
          ? '<span class="status-badge s-reported">active</span>'
          : '<span class="status-badge s-done">repaired</span>';
      return (
        '<tr style="cursor:pointer" data-unit="' +
        attr(item.unit) +
        '" data-vtype="' +
        attr(item.vtype || "") +
        '" onclick="openUnitModal(this.dataset.unit, this.dataset.vtype)" title="Click to see all cases for unit ' +
        attr(item.unit) +
        '">' +
        "<td><b>" +
        h(item.unit) +
        '</b><div style="font-size:10px;color:var(--muted);text-transform:uppercase">' +
        h(item.vtype) +
        "</div></td>" +
        "<td>" +
        badge +
        "</td>" +
        "<td>" +
        h(item.issue) +
        "</td>" +
        "<td>" +
        h(item.driver) +
        "</td>" +
        "<td>" +
        h(item.opened) +
        "</td>" +
        "</tr>"
      );
    })
    .join("");
  updateHTML(wrap, '<div class="card" style="margin-bottom:16px">' +
    '<div class="card-title"><i class="ph ph-activity"></i> Fleet Status <span style="font-size:10px;font-weight:400;color:var(--muted);margin-left:4px">— click a unit to view history</span></div>' +
    '<div class="toggle-tabs" style="margin-bottom:10px">' +
    vbtn("all", "All") +
    vbtn("truck", "Truck") +
    vbtn("trailer", "Trailer") +
    vbtn("reefer", "Reefer") +
    "</div>" +
    '<div class="search-wrap" style="margin-bottom:12px"><i class="ph ph-magnifying-glass"></i><input type="text" id="fleet-status-search" aria-label="Search fleet units, issues or drivers" placeholder="Search unit, issue, or driver..." value="' +
    attr(s.search || "") +
    '" oninput="fleetStatusState.search=this.value;fleetStatusState.page=1;renderFleetStatus()"></div>' +
    (filtered.length
      ? '<div class="table-wrap"><div class="table-scroll"><table><thead><tr><th>Unit</th><th>Status</th><th>Issue</th><th>Driver</th><th>Opened</th></tr></thead><tbody>' +
        rows +
        "</tbody></table></div></div>" +
        '<div class="fleet-list-footer fleet-pager"><span>Showing ' + (filtered.length ? startIndex + 1 : 0) + '–' + Math.min(startIndex + visible.length, filtered.length) + ' of ' + filtered.length + '</span>' +
        '<div class="fleet-pager-controls"><label class="pager-size"><span>Rows</span><select aria-label="Rows per page" onchange="fleetStatusState.perPage=Number(this.value);fleetStatusState.page=1;renderFleetStatus()">' +
        [10,20,30,50,100].map(function(n){return '<option value="'+n+'"'+(perPage===n?' selected':'')+'>'+n+'</option>';}).join('') +
        '</select></label>' +
        '<button class="btn secondary pager-btn" '+(s.page<=1?'disabled':'')+' onclick="fleetStatusState.page=Math.max(1,fleetStatusState.page-1);renderFleetStatus()"><i class="ph ph-arrow-left"></i> Previous</button>' +
        '<span class="pager-page">'+s.page+' / '+totalPages+'</span>' +
        '<button class="btn secondary pager-btn" '+(s.page>=totalPages?'disabled':'')+' onclick="fleetStatusState.page=Math.min('+totalPages+',fleetStatusState.page+1);renderFleetStatus()">Next <i class="ph ph-arrow-right"></i></button></div></div>'
      : '<div style="color:var(--muted);font-size:13px;padding:20px 0;text-align:center">No units match this filter.</div>') +
    "</div>");
}

async function loadMyProfile() {
  var el = document.getElementById("my-profile-content");
  if (!el) return;
  if (!el.children.length || el.querySelector(":scope > .loading")) updateHTML(el, '<div class="loading">Loading...</div>');
  try {
    var r = await apiFetch("/api/my_profile");
    if (!r.ok) {
      updateHTML(el, '<div class="loading">Error loading profile.</div>');
      return;
    }
    var p = await r.json();
    updateHTML(el, '<div class="two-col profile-grid" style="margin-bottom:16px">' +
      '<div class="card profile-identity">' +
      '<div style="display:flex;align-items:center;gap:14px;margin-bottom:16px">' +
      '<div style="width:52px;height:52px;border-radius:50%;background:var(--accent-bg);display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:700;color:var(--accent);flex-shrink:0">' +
      h((p.name || "?")[0]) +
      "</div>" +
      '<div><div style="font-size:17px;font-weight:700">' +
      h(p.name) +
      '</div><div style="font-size:12px;color:var(--muted)">' +
      (p.username ? "@" + h(p.username) + " · " : "") +
      h(p.role) +
      "</div></div>" +
      "</div>" +
      '<div class="mini-stat-grid">' +
      '<div class="agent-stat"><div class="agent-stat-val" style="color:var(--text)">' +
      p.done +
      '</div><div class="agent-stat-label">Resolved</div></div>' +
      '<div class="agent-stat"><div class="agent-stat-val" style="color:var(--text)">' +
      p.missed +
      '</div><div class="agent-stat-label">Missed</div></div>' +
      '<div class="agent-stat"><div class="agent-stat-val" style="color:var(--text)">' +
      p.rate +
      '%</div><div class="agent-stat-label">Resolution rate</div></div>' +
      "</div></div>" +
      '<div class="card"><div class="card-title">Activity breakdown</div><div class="stats-list">' +
      '<div class="row"><span>Today assigned</span><span class="val">' +
      p.today_total +
      "</span></div>" +
      '<div class="row"><span>Today resolved</span><span class="val" style="color:var(--text)">' +
      p.today_done +
      "</span></div>" +
      '<div class="row"><span>This week assigned</span><span class="val">' +
      p.week_total +
      "</span></div>" +
      '<div class="row"><span>This week resolved</span><span class="val" style="color:var(--text)">' +
      p.week_done +
      "</span></div>" +
      '<div class="row"><span>Avg response</span><span class="val">' +
      p.avg_resp +
      "</span></div>" +
      "</div></div></div>" +
      '<div class="section-title" style="margin-bottom:10px">Recent case activity</div>' +
      '<div class="table-wrap"><div class="table-scroll">' +
      caseTable(p.recent) +
      "</div></div>");
  } catch (e) {
    if (e.name === "AbortError") return;
    console.error(e);
    if (!el.children.length || el.querySelector(":scope > .loading")) updateHTML(el, errorContent(e));
  }
}

var agentsWorkspace={agents:[]};
async function loadAgents(){
 var el=document.getElementById('agents-content');if(!el)return;el.innerHTML='<div class="loading">Loading agents...</div>';
 try{var r=await apiFetch('/api/agents');if(!r.ok){el.innerHTML='<div class="loading">Unable to load agents.</div>';return;}var agents=await r.json();agentsWorkspace.agents=agents;
 if(!agents.length){el.innerHTML='<div class="empty-state">No agents found.</div>';return;}
 el.innerHTML='<div class="agents-page-head agents-cards-head"><div><span class="eyebrow">TEAM</span><h2>Agents</h2><p>Workload, resolution and response activity at a glance.</p></div><div class="agents-summary"><strong>'+agents.length+'</strong><span>agents</span></div></div><div class="agents-toolbar"><div class="agents-search"><i class="ph ph-magnifying-glass"></i><input id="agents-search" placeholder="Search agent..." oninput="filterAgents(this.value)"></div></div><div class="agents-card-grid" id="agents-card-grid"></div>';
 renderAgentCards(agents);
 }catch(e){console.error(e);el.innerHTML='<div class="loading">Unable to load agents.</div>';}}
function filterAgents(q){q=(q||'').toLowerCase();renderAgentCards(agentsWorkspace.agents.filter(function(a){return ((a.name||'')+' '+(a.username||'')).toLowerCase().includes(q)}));}
function renderAgentCards(rows){
 var el=document.getElementById('agents-card-grid');if(!el)return;
 el.innerHTML=rows.map(function(a){
   var rate=Math.max(0,Math.min(100,Number(a.rate||0)));
   return '<button type="button" class="agent-overview-card" data-agent="'+attr(a.name||'')+'" data-username="'+attr(a.username||'')+'" data-agent-id="'+attr(a.id||'')+'" onclick="openAgentModal(this.dataset.agent,this.dataset.username,this.dataset.agentId)">'+
     '<div class="agent-overview-top"><span class="agent-overview-avatar">'+h((a.name||'?')[0].toUpperCase())+'</span><span class="agent-overview-id"><strong>'+h(a.name||'')+'</strong><small>'+(a.username?'@'+h(a.username):'Team member')+'</small></span><i class="ph ph-arrow-up-right"></i></div>'+
     '<div class="agent-overview-metrics"><span><b>'+Number(a.total||0)+'</b><small>Cases</small></span><span><b>'+Number(a.done||0)+'</b><small>Resolved</small></span><span><b>'+Number(a.missed||0)+'</b><small>Missed</small></span></div>'+
     '<div class="agent-overview-rate"><span><small>Resolution</small><b>'+rate+'%</b></span><div class="agent-overview-track"><i style="width:'+rate+'%"></i></div></div>'+
     '<div class="agent-overview-foot"><span><i class="ph ph-timer"></i> Avg response '+h(a.avg_resp||'—')+'</span><span>View activity <i class="ph ph-caret-right"></i></span></div></button>';
 }).join('')||'<div class="empty-state">No matching agents.</div>';
}

// ── Modals ─────────────────────────────────────────────────────────────────
async function openCase(el) {
  var caseId = typeof el === "string" ? el : el.dataset.id;
  document.getElementById("modal-overlay").classList.add("open");
  lockBodyScroll();
  updateHTML(document.getElementById("modal-body"), '<div class="loading">Loading...</div>');
  document.getElementById("modal-title").textContent = "Loading...";
  try {
    var r = await apiFetch(
      "/api/case?id=" + encodeURIComponent(caseId),
      "case-detail",
    );
    if (!r.ok) {
      updateHTML(document.getElementById("modal-body"), '<div class="loading">Case not found.</div>');
      return;
    }
    var c = await r.json();
    document.getElementById("modal-title").textContent =
      (c.driver || "—") + " — " + (c.group || "—");
    var extra = "";
    if (c.vehicle_type) {
      extra +=
        '<div class="detail-grid" style="margin-bottom:14px">' +
        '<div class="detail-item"><div class="detail-label">Vehicle Type</div><div class="detail-val">' +
        h(c.vehicle_type || "—") +
        "</div></div>" +
        '<div class="detail-item"><div class="detail-label">Unit Number</div><div class="detail-val">' +
        h(c.unit_number || "—") +
        "</div></div>" +
        '<div class="detail-item"><div class="detail-label">Priority</div><div class="detail-val">' +
        h(c.priority || "—") +
        "</div></div>" +
        '<div class="detail-item"><div class="detail-label">Load Type</div><div class="detail-val">' +
        h(c.load_type || "—") +
        "</div></div>" +
        "</div>";
    }
    updateHTML(document.getElementById("modal-body"), buildTimeline(c) +
      '<div class="detail-grid">' +
      '<div class="detail-item"><div class="detail-label">Status</div><div class="detail-val">' +
      statusBadge(c.status) +
      "</div></div>" +
      '<div class="detail-item"><div class="detail-label">Assigned To</div><div class="detail-val">' +
      h(c.agent || "—") +
      "</div></div>" +
      '<div class="detail-item"><div class="detail-label">Reported By</div><div class="detail-val">' +
      h(c.driver || "—") +
      "</div></div>" +
      '<div class="detail-item"><div class="detail-label">Group</div><div class="detail-val">' +
      h(c.group || "—") +
      "</div></div>" +
      '<div class="detail-item"><div class="detail-label">Opened</div><div class="detail-val">' +
      h(c.opened || "—") +
      "</div></div>" +
      '<div class="detail-item"><div class="detail-label">Assigned At</div><div class="detail-val">' +
      h(c.assigned_at || "—") +
      "</div></div>" +
      '<div class="detail-item"><div class="detail-label">Response Time</div><div class="detail-val">' +
      h(c.response || "—") +
      "</div></div>" +
      '<div class="detail-item"><div class="detail-label">Resolution Time</div><div class="detail-val">' +
      h(c.resolution_secs || "—") +
      "</div></div>" +
      "</div>" +
      extra +
      (c.full_description
        ? '<div class="desc-box"><span class="box-label">Issue Description</span><p class="box-text">' +
          h(c.full_description) +
          "</p></div>"
        : "") +
      (c.full_notes
        ? '<div class="notes-box"><span class="box-label">Report / Notes</span><p class="box-text">' +
          h(c.full_notes) +
          "</p></div>"
        : "") +
      (c.status === "reported" || c.status === "done"
        ? '<div style="margin-top:14px;text-align:center"><button data-id="' +
          attr(c.full_id) +
          '" onclick="viewFullReport(this.dataset.id)" style="background:var(--accent);color:#fff;border:none;border-radius:10px;padding:10px 24px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:8px"> View Full Report</button></div>'
        : ""));
  } catch (e) {
    if (e.name === "AbortError") return;
    console.error("openCase error:", e);
    updateHTML(document.getElementById("modal-body"), '<div class="loading">Error loading case.</div>');
  }
}
function closeModal() {
  var overlay = document.getElementById("modal-overlay");
  if (overlay.classList.contains("open")) {
    overlay.classList.remove("open");
    unlockBodyScroll();
  }
}

// ── Truck 3D Lab ───────────────────────────────────────────────────────────
var truckLabModels = {
  tractor: {title:'Detailed American semi tractor', note:'High-detail 1.7M-triangle American tractor · exterior orientation and component location', id:'6dbb0eaf08ba4fff94d87fb4ceaa83c7', source:'https://sketchfab.com/3d-models/amarican-semi-truck-6dbb0eaf08ba4fff94d87fb4ceaa83c7', badge:'1.7M tris'},
  suspension: {title:'Semi truck axle / suspension close-up', note:'Dedicated 1.3M-triangle mechanical axle model · use for wheel-end, axle and suspension orientation', id:'ada81260bc904bd88cb801a3c306866f', source:'https://sketchfab.com/3d-models/semi-truck-axle-ada81260bc904bd88cb801a3c306866f', badge:'1.3M tris'},
  engine: {title:'Heavy-duty truck diesel engine', note:'Dedicated mechanical engine model · rotate and zoom for engine-side component orientation', id:'d7ff313ff1e64c4db803e39b600a0fbf', source:'https://sketchfab.com/3d-models/truck-engine-d7ff313ff1e64c4db803e39b600a0fbf', badge:'186k tris'},
  hood: {title:'Open-hood semi / engine bay', note:'Open-hood reference view · use to understand engine-bay location before opening the Parts Manual', id:'ea0c42d238c44a8abc53edc03779ad72', source:'https://sketchfab.com/3d-models/open-hood-semi-truck-ea0c42d238c44a8abc53edc03779ad72', badge:'1.1M tris'},
  trailerchassis: {title:'Trailer chassis / running gear', note:'Detailed 663k-triangle trailer chassis · exposes frame, running gear and structural layout', id:'4179e400ef464b958bcf8416e11f87df', source:'https://sketchfab.com/3d-models/container-trailer-chassis-4179e400ef464b958bcf8416e11f87df', badge:'663k tris'},
  reefer: {title:"53' reefer trailer", note:'Reefer exterior reference · use Parts Manual for refrigeration and running-gear component detail', id:'904c9af9277d410c98c395fd9f8f9250', source:'https://sketchfab.com/3d-models/53-reefer-trailer-904c9af9277d410c98c395fd9f8f9250', badge:'Exterior'}
};
function setTruckLabModel(key, btn){
  var m=truckLabModels[key]; if(!m)return;
  document.querySelectorAll('.truck-lab-tab').forEach(function(b){b.classList.toggle('active',b===btn)});
  document.getElementById('truck-model-title').textContent=m.title;
  document.getElementById('truck-model-note').textContent=m.note; var badge=document.getElementById('truck-model-badge'); if(badge)badge.textContent=m.badge||'';
  var a=document.getElementById('truck-model-source'); a.href=m.source;
  var f=document.getElementById('truck-model-frame'); f.title='Interactive '+m.title+' 3D model';
  f.src='https://sketchfab.com/models/'+m.id+'/embed?autostart=1&ui_theme=dark&ui_infos=0&ui_watermark=0';
}
var truckGuides={
 air:{icon:'ph-waves',kicker:'CHASSIS · AIR SYSTEM',title:'Air suspension / air bags',summary:'Air springs support the chassis and maintain ride height. A leaning tractor or trailer, repeated compressor cycling, or audible hissing can indicate an air leak or ride-height problem.',checks:['Park safely on level ground and secure the vehicle.','Compare ride height left-to-right and inspect bags for cracks, folds or obvious damage.','Listen for leaks around the bag, fittings and height-control valve.','Do not crawl beneath an air-suspended vehicle unless it is mechanically supported.'],action:'If a bag is damaged, the chassis is sitting low, or pressure will not hold, keep the vehicle out of service and have the air-suspension fault repaired. Do not rely on air suspension alone to support the vehicle.'},
 brakes:{icon:'ph-disc',kicker:'SAFETY · AIR BRAKES',title:'Air & brake system',summary:'Low system pressure, slow pressure build or an air leak can affect braking. Brake faults are safety-critical.',checks:['Stop in a safe location if the low-air warning activates.','Check dash air-pressure gauges and whether pressure is building normally.','Listen for obvious leaks without placing yourself beneath an unsupported vehicle.','Visually inspect accessible hoses and brake chambers for obvious damage.'],action:'Do not continue operating a vehicle with inadequate air pressure, a persistent low-air warning, damaged brake components, or uncertain braking performance. Use qualified service.'},
 engine:{icon:'ph-engine',kicker:'POWERTRAIN',title:'Diesel engine',summary:'Use the dedicated engine model to understand component location. Treat warning lamps, abnormal temperature, oil-pressure warnings, smoke and fluid loss as symptoms requiring diagnosis.',checks:['Check dash warnings and gauges before opening the hood.','With the engine safely shut down, look for obvious coolant, oil or fuel leaks.','Check fluid levels only according to the vehicle/engine manufacturer procedure.','Record fault codes and operating symptoms before clearing anything.'],action:'Shut down for oil-pressure loss, severe overheating, major fluid loss, abnormal mechanical noise or any manufacturer stop-engine warning. Escalate to qualified service.'},
 electrical:{icon:'ph-battery-charging',kicker:'ELECTRICAL',title:'Starting & charging',summary:'Slow cranking, clicking, dim lighting or repeated dead batteries can point to battery, connection, starter or charging-system problems.',checks:['Switch off unnecessary loads and inspect accessible battery terminals for looseness or corrosion.','Confirm battery disconnects are in the correct operating position if fitted.','Note whether the starter clicks, cranks slowly, or does nothing.','Use proper electrical test equipment and manufacturer voltage specifications.'],action:'Avoid bypassing protection devices or shorting starter/battery terminals. Damaged high-current cables, overheating connections or repeated charging faults require service.'},
 reefer:{icon:'ph-snowflake',kicker:'TRAILER · TEMPERATURE CONTROL',title:'Reefer unit',summary:'A reefer that will not start or hold setpoint can involve fuel, battery, airflow, door/seal, sensor or refrigeration faults. Alarm codes are manufacturer-specific.',checks:['Confirm setpoint, operating mode and displayed box temperature.','Check reefer fuel level and obvious power/battery warnings.','Confirm doors are closed and seals are not visibly compromised.','Record the exact alarm code before resetting the unit.'],action:'Use the exact Thermo King/Carrier manual for the installed unit and alarm code. Refrigerant-system and electrical repairs should be handled by trained reefer service personnel.'},
 tires:{icon:'ph-circle',kicker:'RUNNING GEAR',title:'Tires & wheels',summary:'Pressure loss, visible damage, abnormal wear, heat or vibration can indicate tire, wheel, hub or bearing problems.',checks:['Inspect for cuts, bulges, exposed cords and obvious pressure loss.','Look for missing/loose hardware or signs of wheel movement.','Compare suspicious hub/wheel heat cautiously; do not touch an overheated assembly.','Use the fleet/manufacturer pressure specification rather than guessing pressure.'],action:'Do not run a tire with structural damage, severe pressure loss, loose wheel hardware or suspected bearing/hub failure.'},
 fifth:{icon:'ph-link',kicker:'COUPLING',title:'Fifth wheel / kingpin',summary:'The fifth wheel secures the trailer kingpin. A correct coupling check is essential before movement.',checks:['Visually verify the jaws are closed around the kingpin, not merely touching it.','Confirm the release handle is in the locked position.','Check trailer apron/fifth-wheel contact and obvious component damage.','Perform the fleet-approved tug and coupling inspection procedure.'],action:'If lock engagement is uncertain, do not move the combination. Re-couple using the fleet/manufacturer procedure or request qualified assistance.'},
 dash:{icon:'ph-gauge',kicker:'CAB · INSTRUMENTS',title:'Dashboard warnings',summary:'Warning lamps are a starting point, not a diagnosis. ABS, engine, emissions and stop-engine warnings have different urgency and vehicle-specific meanings.',checks:['Record the exact warning lamp/message and any displayed fault code.','Note engine temperature, oil pressure, air pressure and DEF level where applicable.','Do not clear codes before they are recorded.','Match the warning to the exact tractor/engine manufacturer manual.'],action:'Red stop-engine, oil-pressure, severe-temperature and braking/air-pressure warnings require immediate attention according to the manufacturer procedure.'}
};
function openTruckGuide(key){var g=truckGuides[key];if(!g)return;document.querySelector('#truck-manual-icon i');var ic=document.querySelector('.truck-manual-icon i');ic.className='ph '+g.icon;document.getElementById('truck-manual-kicker').textContent=g.kicker;document.getElementById('truck-manual-title').textContent=g.title;document.getElementById('truck-manual-summary').textContent=g.summary;document.getElementById('truck-manual-checks').innerHTML=g.checks.map(function(x){return '<li>'+h(x)+'</li>'}).join('');document.getElementById('truck-manual-action').textContent=g.action;document.getElementById('truck-manual-panel').scrollIntoView({behavior:'smooth',block:'nearest'});}
function filterTruckGuide(q){q=(q||'').trim().toLowerCase();var shown=0;document.querySelectorAll('.truck-guide-item').forEach(function(el){var ok=!q||(el.dataset.search+' '+el.textContent).toLowerCase().indexOf(q)>=0;el.style.display=ok?'':'none';if(ok)shown++});document.getElementById('truck-guide-count').textContent=shown+' system'+(shown===1?'':'s');}

// ── Truck common-issue overlay + Parts Manual ─────────────────────────────
var truckIssueFilter='all', truckIssuesEnabled=false, currentTruckModel='tractor';
var truckIssues={
 overheat:{title:'Engine overheating',part:'thermostat',text:'High coolant temperature can involve coolant loss, airflow restriction, fan-drive, thermostat or other cooling-system faults.',severity:'Stop / diagnose',icon:'ph-thermometer-hot'},
 nostart:{title:'No crank / weak start',part:'battery',text:'Check the symptom first: no response, clicking, slow crank, or normal crank/no start. Battery condition and high-current connections are common starting points.',severity:'Check before dispatch',icon:'ph-battery-warning'},
 lowair:{title:'Low air pressure',part:'airdryer',text:'Slow pressure build or a low-air warning may involve leaks, compressor/governor, air dryer, valves or reservoirs. Braking capability is safety-critical.',severity:'Do not operate if pressure is unsafe',icon:'ph-warning'},
 airbag:{title:'Air spring / air bag leak',part:'airspring',text:'A sagging corner, audible leak or repeated compressor cycling can indicate a damaged air spring, fitting, line or height-control issue.',severity:'Inspect / service',icon:'ph-waves'},
 brakeheat:{title:'Brake / wheel-end heat',part:'brakechamber',text:'Abnormal heat can indicate dragging brakes, adjustment problems or a wheel-end issue. Do not touch an overheated assembly.',severity:'Stop and inspect',icon:'ph-disc'},
 reefercool:{title:'Reefer not cooling',part:'reeferunit',text:'Confirm setpoint, mode, fuel/power, doors and exact alarm code before reset. Refrigeration repairs require trained service.',severity:'Protect cargo / diagnose',icon:'ph-snowflake'}
};
function toggleTruckIssues(on){truckIssuesEnabled=!!on;var l=document.getElementById('truck-issue-layer'),f=document.getElementById('truck-issue-filters');if(l)l.classList.toggle('off',!on);if(f)f.classList.toggle('disabled',!on);}
function filterTruckIssues(cat,btn){truckIssueFilter=cat;document.querySelectorAll('#truck-issue-filters button').forEach(function(b){b.classList.toggle('active',b===btn)});updateTruckIssuePins();}
function updateTruckIssuePins(){document.querySelectorAll('.truck-issue-pin').forEach(function(p){var cats=(p.dataset.cat||'').split(' '),models=(p.dataset.models||'').split(' ');p.style.display=(truckIssuesEnabled&&(truckIssueFilter==='all'||cats.indexOf(truckIssueFilter)>=0)&&models.indexOf(currentTruckModel)>=0)?'':'none';});}
function openTruckIssue(key){var x=truckIssues[key],d=document.getElementById('truck-issue-detail');if(!x||!d)return;d.innerHTML='<i class="ph '+x.icon+'"></i><div><strong>'+h(x.title)+'</strong><span>'+h(x.text)+'</span><div class="issue-detail-actions"><b>'+h(x.severity)+'</b><button onclick="openPartFromTruck(\''+x.part+'\')">Open in Parts Manual <i class="ph ph-arrow-right"></i></button></div></div>';}
function openPartFromTruck(id){showPage('parts_manual');setTimeout(function(){selectPart(id)},0);}
var _setTruckLabModel=setTruckLabModel;setTruckLabModel=function(key,btn){currentTruckModel=key;_setTruckLabModel(key,btn);updateTruckIssuePins();};

var partsCategory='all', selectedPart='airspring';
var partsDB=[
{id:'airspring',name:'Air Spring / Air Bag',cat:'air',loc:'Drive or trailer suspension',icon:'ph-waves',keywords:'lean sag ride height leak suspension airbag',what:'Flexible air spring that supports vehicle weight and maintains suspension ride height using compressed air.',works:'Air pressure expands the flexible member between the frame and axle/suspension. A height-control valve adds or exhausts air as ride height changes.',issues:['Air leak or puncture','Cracking, rubbing or abrasion','Vehicle leaning / incorrect ride height','Operating on internal bumper with insufficient air'],checks:['Park and secure the vehicle on level ground.','Compare ride height side-to-side.','Inspect the flexible member, fittings and nearby air lines.','Listen for an obvious leak without placing yourself beneath an unsupported air suspension.'],fix:'Damaged air springs, leaking fittings/lines or ride-height valve faults require the correct replacement/repair and ride-height verification. Do not work under a vehicle supported only by air suspension.',source:'Hendrickson air-suspension maintenance',url:'https://www.hendrickson-intl.com/getattachment/40318750-1106-4506-8e0c-f9fd72c6a359/T42001-HSDS-Trailer-Suspension-Maintenance-Procedures-Rev-B.pdf'},
{id:'airdryer',name:'Air Dryer',cat:'air',loc:'Between compressor and air reservoirs',icon:'ph-wind',keywords:'wet tanks purge freeze low air compressor moisture',what:'Removes liquid, vapor and solid contamination from compressed air before it reaches reservoirs and brake-system components.',works:'Compressed air passes through desiccant/filtration media. During the purge cycle, collected moisture and contaminants are expelled.',issues:['Excessive moisture in reservoirs','Frequent or abnormal purge behavior','Air-system freeze-up symptoms','Slow air build caused by system faults'],checks:['Record air-build behavior and low-air warnings.','Listen for abnormal continuous leakage around the dryer/purge area.','Check maintenance history and exact dryer model.','Drain/check reservoirs only per fleet/OEM procedure.'],fix:'Service intervals and cartridges are model-specific. Persistent leakage, contamination or air-build problems require diagnosis of the dryer, compressor, governor and connected air system.',source:'Bendix Air Treatment',url:'https://www.bendix.com/en/products/air-supply-and-air-management-solutions/air-treatment/'},
{id:'brakechamber',name:'Brake Chamber',cat:'brakes',loc:'At tractor/trailer wheel brake assemblies',icon:'ph-disc',keywords:'brake chamber air leak dragging brake pushrod spring brake',what:'Converts air pressure into mechanical force used to apply foundation brakes; combination chambers can also contain a powerful spring brake.',works:'Service air pressure moves a diaphragm/pushrod. In spring-brake sections, a large mechanical spring provides parking/emergency force.',issues:['Air leak at chamber','Brake not releasing','Dragging / overheating brake','Damaged chamber or mounting'],checks:['Treat low-air warnings as safety-critical.','Look/listen for obvious leaks from a safe position.','Note wheel-end heat or a brake that will not release.','Do not disassemble a spring-brake chamber.'],fix:'Brake chamber and spring-brake work is technician-level. A leaking/damaged chamber or dragging brake should be placed out of service until repaired and brake operation verified.',source:'Bendix commercial vehicle braking resources',url:'https://www.bendix.com/'},
{id:'fifthwheel',name:'Fifth Wheel',cat:'trailer',loc:'Top of tractor frame behind cab',icon:'ph-link',keywords:'kingpin coupling jaws lock release trailer',what:'Coupling assembly that captures the trailer kingpin and transfers trailer load into the tractor frame.',works:'The trailer kingpin enters the fifth-wheel throat and locking mechanism closes around it. Correct lock position and contact must be verified before movement.',issues:['Incomplete coupling / high hook','Lock jaws not fully engaged','Release mechanism problem','Wear, damage or poor lubrication'],checks:['Visually verify lock engagement around the kingpin.','Confirm release handle is in the locked position.','Check proper trailer/fifth-wheel contact.','Use the fleet-approved coupling/tug-check procedure.'],fix:'Do not move an uncertain coupling. Re-couple using the manufacturer/fleet procedure; worn, damaged or malfunctioning locking components require qualified inspection/service.',source:'SAF-HOLLAND fifth-wheel manuals',url:'https://safholland.com/us/en/service/download-center'},
{id:'battery',name:'Truck Batteries',cat:'electrical',loc:'Battery box / chassis location varies',icon:'ph-battery-charging',keywords:'no start no crank slow crank voltage corrosion alternator',what:'Stores electrical energy for starting and powers vehicle electrical loads when required.',works:'Multiple heavy-duty batteries are commonly connected to provide the voltage/current needed by the starter and vehicle systems.',issues:['Low state of charge','Loose/corroded connections','Internal battery failure','Repeated discharge from charging or parasitic-load problem'],checks:['Identify whether the symptom is no crank, click, slow crank or normal crank.','Inspect accessible terminals/cables for obvious looseness, heat damage or corrosion.','Check battery disconnect position if equipped.','Use proper test equipment rather than shorting terminals.'],fix:'Clean/tighten connections only under the correct fleet procedure. Repeated low voltage needs battery/charging-system testing. Damaged or hot high-current cables require service.',source:'General heavy-duty electrical reference',url:'https://www.cummins.com/'},
{id:'thermostat',name:'Engine Cooling / Thermostat',cat:'engine',loc:'Engine cooling circuit',icon:'ph-thermometer-hot',keywords:'overheat coolant thermostat radiator fan hot engine',what:'The cooling system removes engine heat; the thermostat regulates coolant flow as the engine warms and operates.',works:'Coolant circulates through the engine and radiator. The thermostat changes flow based on temperature while the radiator/fan rejects heat.',issues:['Engine overheating','Coolant loss','Restricted airflow/radiator','Thermostat, pump or fan-drive fault'],checks:['Read temperature and warning messages before shutdown.','After safe cooldown, look for obvious coolant loss/leaks.','Check for visible airflow obstruction.','Never open a hot pressurized cooling system.'],fix:'Severe overheating or coolant loss requires shutdown and diagnosis. Component replacement and coolant service must follow the exact engine/vehicle procedure.',source:'Cummins service/support reference',url:'https://www.cummins.com/support'},
{id:'turbo',name:'Turbocharger',cat:'engine',loc:'Engine exhaust/intake side',icon:'ph-engine',keywords:'turbo boost smoke power whistle oil intake exhaust',what:'Uses exhaust energy to compress intake air, allowing the engine to make power efficiently.',works:'Exhaust spins the turbine, which shares a shaft with the compressor wheel that pressurizes intake air.',issues:['Low power / low boost','Abnormal whistle or mechanical noise','Oil leakage symptoms','Damaged charge-air plumbing'],checks:['Record engine fault codes and symptoms.','Inspect accessible intake/charge-air hoses for obvious disconnection or damage.','Note smoke, oil loss or abnormal turbo noise.','Do not touch hot exhaust/turbo components.'],fix:'Turbo diagnosis requires checking the full air/exhaust/control system. Mechanical turbo damage, oil leakage or abnormal noise requires qualified engine service.',source:'Cummins engine technology/support',url:'https://www.cummins.com/'},
{id:'dpf',name:'DPF / Aftertreatment',cat:'aftertreatment',loc:'Exhaust aftertreatment assembly',icon:'ph-cloud',keywords:'dpf regen soot ash derate check engine exhaust',what:'The diesel particulate filter captures particulate matter from exhaust and periodically requires regeneration; accumulated ash eventually requires service.',works:'Exhaust passes through DOC/DPF components. Regeneration raises temperature to oxidize collected soot, while non-combustible ash remains for later service.',issues:['Frequent regen requests','High soot load / derate','Sensor or temperature faults','Ash accumulation'],checks:['Record all dash messages and fault codes.','Check whether a parked regen is being requested/allowed by the exact vehicle procedure.','Do not clear codes before recording them.','Inspect only externally accessible wiring/components when safe.'],fix:'Follow the exact engine/OEM regeneration procedure. Persistent faults, high soot load or service-required DPF conditions need diagnostic software and qualified service.',source:'Cummins aftertreatment overview',url:'https://www.cummins.com/components/aftertreatment/how-it-works'},
{id:'def',name:'DEF / SCR System',cat:'aftertreatment',loc:'DEF tank, dosing system and SCR aftertreatment',icon:'ph-drop',keywords:'def scr emissions derate nox dosing fluid',what:'DEF is dosed into the exhaust so the SCR catalyst can reduce nitrogen oxides (NOx).',works:'The dosing system meters DEF into hot exhaust; it forms ammonia, which reacts with NOx across the SCR catalyst to form nitrogen and water.',issues:['Low/contaminated DEF','Dosing-system fault','NOx/sensor fault','Emissions derate'],checks:['Confirm DEF level and displayed warnings.','Record exact codes/messages.','Do not add anything except specification-correct DEF.','Do not clear emissions codes before diagnosis.'],fix:'Incorrect fluid/contamination and persistent SCR faults require the exact engine/OEM diagnostic procedure. Dosing and sensor faults generally require trained service.',source:'Cummins aftertreatment fundamentals',url:'https://www.cummins.com/en-eu/components/aftertreatment/system-fundamentals'},
{id:'reeferunit',name:'Reefer Unit',cat:'reefer',loc:'Front of refrigerated trailer',icon:'ph-snowflake',keywords:'reefer not cooling temperature alarm fuel battery thermoking carrier',what:'Independent temperature-control system that removes heat from the cargo space and maintains the selected operating temperature.',works:'A controller operates the refrigeration system and airflow according to setpoint, mode and sensor inputs. Exact operation varies by unit.',issues:['Unit will not start','Will not reach/hold setpoint','Low battery / fuel issue','High/low pressure or other alarm'],checks:['Record setpoint, box temperature, mode and exact alarm code.','Confirm reefer fuel/power status.','Check that doors are closed and seals are visibly intact.','Avoid repeated resets before recording the alarm.'],fix:'Use the exact Thermo King/Carrier model manual and alarm code. Refrigerant, compressor and high-current electrical work requires trained reefer service.',source:'Thermo King V-Series operating instructions',url:'https://www.thermoking.com/content/dam/thermoking/documents/products/v-series-direct-smart-reefer-operator-manual-61651-18.pdf'},
{id:'doorseal',name:'Reefer Door Seal',cat:'reefer',loc:'Rear/side cargo doors',icon:'ph-door',keywords:'door seal temp warm cargo air leak reefer gasket',what:'Flexible perimeter seal limits outside-air infiltration when the refrigerated trailer door is closed.',works:'The gasket compresses against the door/frame to create a continuous seal, reducing warm/moist air entering the cargo space.',issues:['Torn or missing gasket section','Door not seating evenly','Visible gap/light','Ice/moisture near leak area'],checks:['Inspect the full gasket perimeter.','Confirm latches pull the door closed evenly.','Look for damaged hinges/frame that prevent sealing.','Keep doors closed as much as practical during temperature-control troubleshooting.'],fix:'Minor debris can be removed; damaged seals, latch alignment or door/frame damage should be repaired before relying on the trailer for temperature-sensitive cargo.',source:'General reefer inspection; verify trailer OEM procedure',url:'https://www.thermoking.com/'},
{id:'tire',name:'Tire / Wheel End',cat:'trailer',loc:'Steer, drive and trailer axles',icon:'ph-circle',keywords:'tire flat pressure tread wheel hub heat vibration lug bearing',what:'Tires carry load and provide traction; the wheel-end includes wheel, hub and bearing components that must rotate freely and remain secure.',works:'Inflated tires support load through the casing while hub/bearing assemblies allow the wheel to rotate on the axle.',issues:['Pressure loss / flat','Cuts, bulges or exposed cords','Abnormal wear','Wheel-end heat or looseness'],checks:['Inspect visible tire condition and inflation.','Look for missing/loose wheel hardware or movement signs.','Treat abnormal heat cautiously; do not touch an overheated wheel end.','Use fleet/OEM pressure specifications.'],fix:'Structural tire damage, major pressure loss, loose wheel hardware or suspected bearing/hub failure requires tire/roadside/shop service before continued operation.',source:'Fleet/OEM tire and wheel-end procedures',url:'https://www.safholland.com/us/en/service/download-center'},
{id:'landinggear',name:'Trailer Landing Gear',cat:'trailer',loc:'Under trailer front section',icon:'ph-arrows-down-up',keywords:'landing gear crank trailer legs stuck support',what:'Mechanical support legs hold the front of an uncoupled semitrailer and raise/lower it for coupling height.',works:'A crank and gearbox drive telescoping legs. High/low gear arrangements vary by manufacturer.',issues:['Hard or seized cranking','Bent leg/foot','Gearbox damage','Uneven support'],checks:['Keep clear of pinch/crush zones.','Inspect legs, feet and bracing for obvious damage.','Do not force a mechanism that is binding.','Confirm trailer is on stable ground before uncoupling.'],fix:'Bent, damaged or binding landing gear needs manufacturer-specific service. Never work beneath or around an inadequately supported trailer.',source:'SAF-HOLLAND landing gear literature',url:'https://safholland.com/us/en/service/download-center'}
];
// Expanded high-value fleet reference catalog. Detailed OEM-specific procedures remain linked per entry.
var extraParts=[
['heightcontrol','Height Control Valve','air','Suspension / frame','ph-arrows-vertical','lean ride height suspension wont raise air bags','Maintains designed suspension ride height by adding or exhausting air.',['Incorrect ride height','Trailer leaning','Air springs not filling','Continuous air flow/leak'],'Check linkage for obvious damage; compare ride height; listen for leakage.','Repair/adjust to the exact suspension specification; verify ride height after service.','Hendrickson height control valve','https://www.hendrickson-intl.com/products/aftermarket-parts/trailer-height-control-valve'],
['airtank','Air Reservoir / Tank','air','Chassis','ph-cylinder','air tank reservoir drain moisture low air','Stores compressed air for vehicle pneumatic systems.',['Air leak','Excess moisture','Corrosion/damage','Pressure loss'],'Look for visible damage/leaks and record air-build behavior.','Damaged/leaking reservoirs require qualified air-system service.','Bendix air systems','https://www.bendix.com/'],
['airline','Air Line / Fitting','air','Throughout chassis','ph-path','air hose fitting leak hiss low pressure','Carries compressed air between valves, reservoirs, suspension and brakes.',['Chafed line','Loose fitting','Crack/leak','Kink/restriction'],'Listen for leaks and visually inspect accessible routing/fittings.','Repair with approved line/fittings; brake-related leaks are safety critical.','Bendix air systems','https://www.bendix.com/'],
['slackadjuster','Automatic Slack Adjuster','brakes','Wheel brake linkage','ph-gear','slack adjuster brake stroke dragging out of adjustment','Maintains brake running clearance as lining wears.',['Excess pushrod stroke','Not self-adjusting','Dragging brake','Uneven braking'],'Record brake symptom and visible linkage condition; do not repeatedly manually adjust an automatic adjuster.','Find and repair the root cause; brake inspection by a qualified technician.','Bendix brake troubleshooting','https://www.bendixvrc.com/itemDisplay.asp?documentID=7270'],
['brakedrum','Brake Drum / Rotor','brakes','Wheel end','ph-disc','brake drum rotor hot crack wear vibration','Provides the friction surface used to slow the wheel.',['Overheating','Cracks','Scoring/wear','Vibration/pulsation'],'Check for abnormal heat, visible damage and related brake symptoms from a safe position.','Out-of-limit or damaged friction components require brake service.','Bendix braking resources','https://www.bendix.com/'],
['wheelbearing','Wheel Bearing / Hub','trailer','Wheel end','ph-circle','bearing hub wheel end hot loose noise seal','Supports the rotating hub/wheel on the axle.',['Overheating','Noise','Looseness','Lubricant leak'],'Note heat, noise, visible leakage or wheel-end movement signs.','Suspected bearing/hub failure requires immediate wheel-end inspection.','SAF-HOLLAND service literature','https://safholland.com/us/en/service/download-center'],
['wheelseal','Wheel Seal','trailer','Wheel end','ph-drop','wheel seal hub oil leak grease brake contamination','Retains wheel-end lubricant and helps keep contamination out.',['Oil/grease leak','Brake contamination','Low lubricant','Seal damage'],'Look for lubricant around hub, wheel or brake area.','Replace failed seal and inspect bearing/brake contamination before operation.','SAF-HOLLAND service literature','https://safholland.com/us/en/service/download-center'],
['shock','Shock Absorber','air','Suspension','ph-wave-sine','shock absorber bounce leak suspension tire wear','Controls suspension oscillation and helps maintain tire contact.',['Fluid leak','Broken mount','Excess bouncing','Irregular tire wear'],'Inspect body and mounts for obvious leakage/damage.','Replace damaged/worn shocks in accordance with suspension procedure.','Hendrickson parts & service','https://www.hendrickson-intl.com/parts-and-service'],
['compressor','Air Compressor','air','Engine / air supply','ph-wind','air compressor slow build low air oil air system','Supplies compressed air used by brakes and other pneumatic systems.',['Slow pressure build','No pressure build','Oil carryover','Abnormal cycling'],'Record build time/pressure behavior and check for major downstream leaks first.','Compressor/governor diagnosis is system-level service work.','Bendix air systems','https://www.bendix.com/'],
['governor','Air Governor','air','Compressor control','ph-gauge','governor cut in cut out compressor purge pressure','Controls compressor loading/unloading around system pressure thresholds.',['Abnormal cut-in/cut-out','Continuous compressor loading','Purge complaints'],'Record pressure behavior rather than guessing the component.','Test against the exact air-system specification before replacement.','Bendix air systems','https://www.bendix.com/'],
['starter','Starter Motor','electrical','Engine / flywheel housing','ph-lightning','starter click no crank slow crank','Cranks the engine using high battery current.',['Click/no crank','Intermittent crank','Slow crank','Overheated cable/connection'],'Separate battery/connection symptoms from starter symptoms; inspect accessible connections.','High-current starting-system diagnosis should use proper test equipment.','Cummins support','https://www.cummins.com/support'],
['alternator','Alternator','electrical','Engine accessory drive','ph-lightning','alternator charging battery light low voltage belt','Charges batteries and powers electrical loads while the engine runs.',['Low charging voltage','Battery warning','Bearing noise','Belt/pulley problem'],'Check warning messages, belt condition and charging-system history.','Test charging output and connections before replacing components.','Cummins support','https://www.cummins.com/support'],
['serpbelt','Serpentine / Accessory Belt','engine','Front of engine','ph-arrows-clockwise','belt squeal broken belt alternator water pump fan','Drives engine accessories depending on configuration.',['Cracks/fraying','Squeal','Thrown/broken belt','Tensioner issue'],'With engine off, inspect visible belt condition/routing.','Replace damaged belt and diagnose pulley/tensioner cause before return to service.','Cummins support','https://www.cummins.com/support'],
['waterpump','Water Pump','engine','Cooling circuit','ph-drop','water pump coolant leak overheat bearing','Circulates coolant through engine and radiator.',['Coolant leak','Overheating','Bearing noise','Poor circulation'],'After safe cooldown, inspect for obvious coolant leakage and correlate with temperature symptoms.','Cooling-system pressure testing/component service should follow engine procedure.','Cummins support','https://www.cummins.com/support'],
['radiator','Radiator','engine','Front cooling package','ph-grid-four','radiator coolant leak clogged fins overheat','Transfers engine heat from coolant to airflow.',['External blockage','Coolant leak','Damaged fins/core','Overheating'],'Inspect accessible face for debris/damage and never open a hot pressurized system.','Clean/repair using vehicle/OEM procedure; leaks or overheating require diagnosis.','Cummins support','https://www.cummins.com/support'],
['fanclutch','Fan Clutch / Fan Drive','engine','Cooling package','ph-fan','fan clutch overheat fan not engaging noise','Controls cooling-fan engagement on many heavy trucks.',['Fan not engaging','Fan locked on','Noise/vibration','Overheating under load'],'Record when overheating occurs and whether fan behavior changes as expected.','Fan-drive diagnosis/repair is model-specific.','Cummins support','https://www.cummins.com/support'],
['egr','EGR Valve / System','engine','Engine air/exhaust system','ph-arrows-left-right','egr fault derate soot power check engine','Routes controlled exhaust gas back into intake to reduce NOx under applicable conditions.',['Check-engine/derate','Soot buildup','Sticking valve','Performance complaint'],'Capture exact fault codes and operating symptoms before parts replacement.','Use engine diagnostic procedure; avoid parts-cannon diagnosis.','Cummins support','https://www.cummins.com/support'],
['nox','NOx Sensor','aftertreatment','Exhaust aftertreatment','ph-scan','nox sensor scr def derate check engine','Measures exhaust NOx for aftertreatment control/monitoring.',['Fault code','Derate','SCR efficiency complaint','Wiring/sensor issue'],'Capture exact codes; inspect accessible harness/connectors for obvious damage.','Confirm sensor/system diagnosis with OEM software/procedure.','Cummins aftertreatment fundamentals','https://selfscreening.cummins.com/components/aftertreatment/system-fundamentals'],
['doserval','DEF Doser','aftertreatment','SCR exhaust system','ph-drop','def doser crystallization scr derate dosing','Meters DEF into the exhaust stream for SCR operation.',['Dosing fault','Crystallization/deposit','Derate','DEF consumption complaint'],'Capture codes and inspect for obvious external damage/deposits.','Dosing-system testing/service requires the exact aftertreatment procedure.','Cummins aftertreatment fundamentals','https://selfscreening.cummins.com/components/aftertreatment/system-fundamentals'],
['fuelwater','Fuel / Water Separator','engine','Fuel system','ph-funnel','fuel filter water separator no start low power','Filters fuel and separates water before fuel reaches sensitive components.',['Restriction','Water contamination','Leak','Hard start/low power'],'Check service indicator/history and visible contamination only per fleet procedure.','Service filters/drain water according to engine/fleet procedure and prime correctly.','Cummins support','https://www.cummins.com/support'],
['kingpin','Trailer Kingpin','trailer','Trailer nose underside','ph-push-pin','kingpin fifth wheel coupling wear damage','Structural pin captured by the tractor fifth wheel.',['Wear/damage','Coupling difficulty','Improper lock','Bent mounting area'],'Visually inspect accessible kingpin/coupling area during coupling checks.','Damaged/worn kingpin or mounting structure requires qualified inspection.','SAF-HOLLAND service literature','https://safholland.com/us/en/service/download-center'],
['gladhand','Gladhand / Air Coupling','trailer','Tractor-trailer air connections','ph-plugs-connected','gladhand seal trailer air leak red blue line','Connects tractor service/emergency air lines to the trailer.',['Seal leak','Damaged coupling','Loose connection','Contamination'],'Inspect seals/couplings and listen for leakage after connection.','Replace damaged seals/couplings with approved parts; verify no leak.','Bendix air systems','https://www.bendix.com/'],
['pigtail','7-Way Electrical Pigtail','electrical','Tractor-trailer connection','ph-plug','trailer lights abs power pigtail connector','Carries electrical power/signals between tractor and trailer.',['No trailer lights','ABS power fault','Loose/corroded pins','Damaged cable'],'Inspect connector seating, pins and cable for obvious damage.','Repair approved wiring/connectors; diagnose circuit before replacing modules.','Fleet electrical reference','https://www.bendix.com/'],
['abs','ABS Wheel Speed Sensor','brakes','Wheel end','ph-speedometer','abs light wheel speed sensor trailer abs fault','Provides wheel-speed information to the ABS controller.',['ABS warning lamp','Sensor gap/damage','Harness damage','Tone-ring issue'],'Record ABS lamp behavior/code and inspect accessible harness/sensor area.','Use ABS diagnostic procedure; braking remains safety-critical.','Bendix braking resources','https://www.bendix.com/'],
['reeferbelt','Reefer Belt / Drive','reefer','Reefer unit','ph-arrows-clockwise','reefer belt broken squeal compressor alternator not cooling','Transfers mechanical drive to reefer accessories depending on unit design.',['Broken/worn belt','Squeal','Poor accessory drive','Belt debris'],'Shut unit down before visual inspection; note alarm code and belt condition.','Replace/service only per reefer model procedure with guards restored.','Thermo King support','https://www.thermoking.com/'],
['reeferbattery','Reefer Battery','reefer','Reefer unit','ph-battery-charging','reefer no start battery low voltage','Supplies starting/control power to independent reefer units.',['No start','Low voltage','Corroded connection','Repeated discharge'],'Record display/alarm behavior and inspect accessible terminals safely.','Test battery/charging system before replacement.','Thermo King support','https://www.thermoking.com/'],
['reeferfuel','Reefer Fuel System','reefer','Reefer unit / tank','ph-gas-pump','reefer fuel no start shuts down fuel level','Supplies fuel to diesel-powered reefer units.',['Out of fuel','Restriction','Leak','Air in fuel/no start'],'Confirm actual fuel level and alarms; look for obvious external leakage.','Leaks or persistent fuel faults require reefer service.','Thermo King support','https://www.thermoking.com/'],
['reefercondenser','Reefer Condenser / Airflow','reefer','Front reefer unit','ph-snowflake','reefer high pressure condenser dirty not cooling airflow','Rejects heat from the refrigeration system to ambient air.',['Restricted airflow','Dirty/damaged coil','Fan problem','Poor cooling/high pressure'],'Check for visible airflow blockage/damage with unit safely stopped.','Cleaning/fan/refrigeration diagnosis follows exact reefer procedure.','Thermo King support','https://www.thermoking.com/'],
['reeferprobe','Reefer Temperature Sensor','reefer','Cargo box / reefer unit','ph-thermometer','reefer temp sensor probe wrong temperature alarm','Measures temperature used for control and monitoring.',['Incorrect reading','Sensor fault alarm','Intermittent reading','Harness damage'],'Compare displayed temperature with trusted reference when available and capture alarm code.','Sensor/harness diagnosis is model-specific.','Thermo King support','https://www.thermoking.com/'],
['mudflap','Mudflap / Bracket','trailer','Behind wheels','ph-square','mudflap bracket broken dot roadside','Controls spray/debris and is required equipment in many operations.',['Torn/missing flap','Bent bracket','Loose hardware'],'Visual walk-around inspection.','Replace/secure damaged hardware before operation where required.','Fleet inspection reference','https://www.fmcsa.dot.gov/'],
['markerlight','Trailer Marker / Tail Light','electrical','Trailer exterior','ph-lightbulb','trailer light marker tail turn brake lamp out','Provides required vehicle visibility/signaling.',['Lamp out','Water intrusion','Connector corrosion','Wiring fault'],'Check which function is missing and inspect lamp/connector for visible damage.','Repair circuit/connector or replace approved lamp; verify operation.','Fleet electrical reference','https://www.fmcsa.dot.gov/']
];
extraParts.forEach(function(x){partsDB.push({id:x[0],name:x[1],cat:x[2],loc:x[3],icon:x[4],keywords:x[5],what:x[6],works:x[6],issues:x[7],checks:[x[8]],fix:x[9],source:x[10],url:x[11]});});
var commonPartIds=['airspring','heightcontrol','airdryer','brakechamber','slackadjuster','battery','starter','thermostat','dpf','def','tire','wheelseal','reeferunit','reeferbelt'];
function renderCommonParts(){var box=document.getElementById('parts-common-list');if(!box)return;box.innerHTML=commonPartIds.map(function(id){var p=partsDB.find(function(x){return x.id===id});return p?'<button onclick="selectPart(\''+p.id+'\')"><i class="ph '+p.icon+'"></i><span>'+h(p.name)+'</span></button>':''}).join('');var total=document.getElementById('parts-total');if(total)total.textContent=partsDB.length;}

var partPhotoLibrary={
  airspring:['Semi-trailer Groenewegen, twin tires, suspension IMG 20211110 153847.jpg','Trailer suspension / wheel-end reference','https://commons.wikimedia.org/wiki/File:Semi-trailer_Groenewegen,_twin_tires,_suspension_IMG_20211110_153847.jpg'],
  heightcontrol:['Semi-trailer Groenewegen, twin tires, suspension IMG 20211110 153847.jpg','Trailer suspension reference','https://commons.wikimedia.org/wiki/File:Semi-trailer_Groenewegen,_twin_tires,_suspension_IMG_20211110_153847.jpg'],
  airbag:['Semi-trailer Groenewegen, twin tires, suspension IMG 20211110 153847.jpg','Trailer suspension reference','https://commons.wikimedia.org/wiki/File:Semi-trailer_Groenewegen,_twin_tires,_suspension_IMG_20211110_153847.jpg'],
  brakechamber:['Spring brake air cylinder.jpg','Heavy-truck spring brake chamber','https://commons.wikimedia.org/wiki/File:Spring_brake_air_cylinder.jpg'],
  slackadjuster:['Air disc brake.jpg','Truck air-brake assembly reference','https://commons.wikimedia.org/wiki/File:Air_disc_brake.jpg'],
  fifthwheel:['Saf Holland turntable, fifth wheel 02.jpg','Heavy-duty fifth wheel coupling','https://commons.wikimedia.org/wiki/File:Saf_Holland_turntable,_fifth_wheel_02.jpg'],
  kingpin:['Fifth wheel IHC truck MD2.jpg','Tractor fifth-wheel / coupling area','https://commons.wikimedia.org/wiki/File:Fifth_wheel_IHC_truck_MD2.jpg'],
  gladhand:['Glad Hand.jpg','Trailer gladhand air coupling','https://commons.wikimedia.org/wiki/File:Glad_Hand.jpg'],
  battery:['Battery Box.jpg','Heavy vehicle battery-box reference','https://commons.wikimedia.org/wiki/File:Battery_Box.jpg'],
  starter:['MOTOR STARTER.jpg','Starter motor reference','https://commons.wikimedia.org/wiki/File:MOTOR_STARTER.jpg'],
  alternator:['Alternator.jpg','Alternator reference','https://commons.wikimedia.org/wiki/File:Alternator.jpg'],
  thermostat:['Carthermostat.jpg','Internal-combustion engine thermostat','https://commons.wikimedia.org/wiki/File:Carthermostat.jpg'],
  turbo:['Turbocharger assembly.jpg','Truck-engine turbocharger assembly','https://commons.wikimedia.org/wiki/File:Turbocharger_assembly.jpg'],
  radiator:['Motor Vehicles - Motor Trucks - Parts - Radiators - Motor truck radiator, made for Class B. Trucks - NARA - 45506611.jpg','Truck radiator reference','https://commons.wikimedia.org/wiki/File:Motor_Vehicles_-_Motor_Trucks_-_Parts_-_Radiators_-_Motor_truck_radiator,_made_for_Class_B._Trucks_-_NARA_-_45506611.jpg'],
  reeferunit:['Thermo King semi-trailer refrigeration unit.jpg','Semi-trailer refrigeration unit','https://commons.wikimedia.org/wiki/File:Thermo_King_semi-trailer_refrigeration_unit.jpg'],
  reeferbelt:['Thermo King semi-trailer refrigeration unit.jpg','Reefer unit exterior reference','https://commons.wikimedia.org/wiki/File:Thermo_King_semi-trailer_refrigeration_unit.jpg'],
  reeferbattery:['Thermo King semi-trailer refrigeration unit.jpg','Reefer unit exterior reference','https://commons.wikimedia.org/wiki/File:Thermo_King_semi-trailer_refrigeration_unit.jpg'],
  reeferfuel:['Carrier reefer.jpg','Carrier reefer unit reference','https://commons.wikimedia.org/wiki/File:Carrier_reefer.jpg'],
  reefercondenser:['Carrier reefer.jpg','Carrier reefer unit / condenser area','https://commons.wikimedia.org/wiki/File:Carrier_reefer.jpg'],
  reeferprobe:['Carrier reefer and trailer.jpg','Refrigerated trailer system reference','https://commons.wikimedia.org/wiki/File:Carrier_reefer_and_trailer.jpg'],
  leafspring:['Blattfeder eines LKW.JPG','Truck leaf spring','https://commons.wikimedia.org/wiki/File:Blattfeder_eines_LKW.JPG']
};
var partCategoryPhotos={
  air:['Paineilmajarrut.svg','Commercial-vehicle compressed-air system diagram','https://commons.wikimedia.org/wiki/File:Paineilmajarrut.svg'],
  brakes:['Air disc brake.jpg','Truck air-brake assembly','https://commons.wikimedia.org/wiki/File:Air_disc_brake.jpg'],
  suspension:['Semi-trailer Groenewegen, twin tires, suspension IMG 20211110 153847.jpg','Heavy-trailer suspension reference','https://commons.wikimedia.org/wiki/File:Semi-trailer_Groenewegen,_twin_tires,_suspension_IMG_20211110_153847.jpg'],
  electrical:['Alternator.jpg','Heavy-vehicle electrical component reference','https://commons.wikimedia.org/wiki/File:Alternator.jpg'],
  engine:['Turbocharger assembly.jpg','Truck diesel-engine component reference','https://commons.wikimedia.org/wiki/File:Turbocharger_assembly.jpg'],
  aftertreatment:['Turbocharger assembly.jpg','Diesel exhaust/engine hardware reference','https://commons.wikimedia.org/wiki/File:Turbocharger_assembly.jpg'],
  trailer:['Fifth wheel IHC truck MD2.jpg','Tractor/trailer running-gear reference','https://commons.wikimedia.org/wiki/File:Fifth_wheel_IHC_truck_MD2.jpg'],
  reefer:['Thermo King semi-trailer refrigeration unit.jpg','Semi-trailer refrigeration unit','https://commons.wikimedia.org/wiki/File:Thermo_King_semi-trailer_refrigeration_unit.jpg']
};
function partPhoto(p){return '<section class="part-live-photos" id="part-photo-'+h(p.id)+'"><div class="part-photo-status"><i class="ph ph-spinner-gap"></i><span>Finding verified live photos of '+h(p.name)+'…</span></div><div class="part-photo-grid"></div></section>';}
var partImageRequest=0;
function loadLivePartPhoto(p){
  var requestId=++partImageRequest,root=document.getElementById('part-photo-'+p.id);if(!root)return;
  var params=new URLSearchParams({q:p.name,cat:p.cat||'',keywords:p.keywords||''});
  fetch('/api/part_image?'+params.toString()).then(function(r){if(!r.ok)throw new Error('lookup failed');return r.json()}).then(function(data){
    if(requestId!==partImageRequest)return;var current=document.getElementById('part-photo-'+p.id);if(!current)return;
    var status=current.querySelector('.part-photo-status'),grid=current.querySelector('.part-photo-grid'),items=(data&&data.results)||[];
    if(!items.length){status.innerHTML='<i class="ph ph-image-broken"></i><span>No relevant live truck-part photo found. Try another part name or the OEM/source reference below.</span>';grid.innerHTML='';return;}
    status.innerHTML='<i class="ph ph-check-circle"></i><span>'+items.length+' live heavy-duty reference photo'+(items.length===1?'':'s')+' found via Serper · actual installed model may vary</span>';
    grid.innerHTML=items.map(function(x,i){return '<figure class="part-live-photo"><a href="'+h(x.source_url)+'" target="_blank" rel="noopener" title="Open original photo and license"><img src="'+h(x.image_url)+'" alt="'+h(p.name)+' real-world reference photo '+(i+1)+'" loading="lazy" referrerpolicy="no-referrer"></a><figcaption><span>'+h(x.title||p.name)+'</span><a href="'+h(x.source_url)+'" target="_blank" rel="noopener">Source'+(x.license?' · '+h(x.license):'')+' <i class="ph ph-arrow-square-out"></i></a></figcaption></figure>';}).join('');
  }).catch(function(){var current=document.getElementById('part-photo-'+p.id);if(!current)return;current.querySelector('.part-photo-status').innerHTML='<i class="ph ph-warning-circle"></i><span>Live photo search is temporarily unavailable.</span>';});
}

var partsWebSearchRequest=0;
function looksLikePartNumber(q){q=(q||'').trim();return q.length>=5&&/[a-z]/i.test(q)&&/\d/.test(q)&&/^[a-z0-9._\-/]+$/i.test(q);}
function partsSearchInput(value){renderPartsManual(value);var b=document.getElementById('parts-web-search-btn');if(b)b.classList.toggle('suggested',looksLikePartNumber(value));}
function partsSearchKey(e){if(e.key==='Enter'){e.preventDefault();searchPartsWeb();}}
function clearPartsSearch(){var i=document.getElementById('parts-search');if(i){i.value='';i.focus();}var b=document.getElementById('parts-web-search-btn');if(b)b.classList.remove('suggested');var d=document.getElementById('part-detail');if(d)d.dataset.ready='';renderPartsManual('');}
function searchPartsWeb(){
  var input=document.getElementById('parts-search'),q=(input&&input.value||'').trim(),d=document.getElementById('part-detail'),btn=document.getElementById('parts-web-search-btn');
  if(!q||!d)return;
  var requestId=++partsWebSearchRequest;
  if(btn){btn.disabled=true;btn.innerHTML='<i class="ph ph-spinner-gap parts-spin"></i><span>Searching...</span>';}
  d.dataset.ready='1';d.innerHTML='<div class="web-part-search-state"><i class="ph ph-spinner-gap parts-spin"></i><div><span class="part-label">LIVE PART SEARCH</span><h2>Searching for '+h(q)+'</h2><p>Checking live heavy-duty truck part references and photos...</p></div></div>';
  fetch('/api/part_search?q='+encodeURIComponent(q)).then(function(r){return r.json().then(function(x){if(!r.ok)throw new Error(x.error||'search failed');return x;});}).then(function(data){
    if(requestId!==partsWebSearchRequest)return;var items=(data&&data.results)||[];
    var exact=data&&data.part_number_like?'<span class="web-search-badge"><i class="ph ph-barcode"></i> Part number search</span>':'';
    d.innerHTML='<div class="part-hero web-result-hero"><div class="part-hero-copy"><div class="part-meta"><span>LIVE WEB RESULT</span>'+exact+'</div><h2>'+h(q)+'</h2><p>Real-time heavy-duty part references from Serper. Verify the exact application/OEM number before ordering or repair.</p></div></div><div class="part-section part-photo-section"><div class="part-section-head"><div><span class="part-label">REAL PART PHOTOS</span><h3>Web matches</h3></div><small>'+items.length+' result'+(items.length===1?'':'s')+'</small></div><section class="part-live-photos"><div class="part-photo-grid">'+(items.length?items.map(function(x,i){return '<figure class="part-live-photo"><a href="'+h(x.source_url)+'" target="_blank" rel="noopener"><img src="'+h(x.image_url)+'" alt="'+h(q)+' reference photo '+(i+1)+'" loading="lazy" referrerpolicy="no-referrer"></a><figcaption><span>'+h(x.title||q)+'</span><small>'+h(x.source||'Web source')+'</small><a href="'+h(x.source_url)+'" target="_blank" rel="noopener">Open source <i class="ph ph-arrow-square-out"></i></a></figcaption></figure>';}).join(''):'<div class="web-no-results"><i class="ph ph-image-broken"></i><strong>No confident heavy-duty image matches found</strong><span>Try the exact OEM number, manufacturer + number, or component name.</span></div>')+'</div></section></div>';
  }).catch(function(err){if(requestId!==partsWebSearchRequest)return;d.innerHTML='<div class="web-no-results"><i class="ph ph-warning-circle"></i><strong>Live search unavailable</strong><span>'+h(err.message||'Please try again.')+'</span></div>';}).finally(function(){if(btn){btn.disabled=false;btn.innerHTML='<i class="ph ph-globe-hemisphere-west"></i><span>Search web</span>';}});
}
function renderPartsManual(q){q=(q||document.getElementById('parts-search')&&document.getElementById('parts-search').value||'').trim().toLowerCase();var list=document.getElementById('parts-list');if(!list)return;var rows=partsDB.filter(function(p){return(partsCategory==='all'||p.cat===partsCategory)&&(!q||(p.name+' '+p.keywords+' '+p.what+' '+p.issues.join(' ')).toLowerCase().indexOf(q)>=0)});document.getElementById('parts-count').textContent=rows.length+' part'+(rows.length===1?'':'s');list.innerHTML=rows.map(function(p){return '<button class="part-row '+(p.id===selectedPart?'active':'')+'" onclick="selectPart(\''+p.id+'\')"><i class="ph '+p.icon+'"></i><span><strong>'+h(p.name)+'</strong><small>'+h(p.loc)+'</small></span><i class="ph ph-caret-right"></i></button>'}).join('')||'<div class="parts-empty">No matching parts.</div>';if(!partsDB.some(function(p){return p.id===selectedPart})&&rows[0])selectedPart=rows[0].id;if(!document.getElementById('part-detail').dataset.ready)selectPart(selectedPart);}
function setPartsCategory(cat,btn){partsCategory=cat;document.querySelectorAll('#parts-cats button').forEach(function(b){b.classList.toggle('active',b===btn)});renderPartsManual();}
function selectPart(id){var p=partsDB.find(function(x){return x.id===id});if(!p)return;selectedPart=id;var d=document.getElementById('part-detail');if(!d)return;d.dataset.ready='1';d.innerHTML='<div class="part-hero"><div class="part-hero-copy"><div class="part-meta"><span>'+h(p.cat)+'</span><span><i class="ph ph-map-pin"></i> '+h(p.loc)+'</span></div><h2>'+h(p.name)+'</h2><p>'+h(p.what)+'</p></div></div><div class="part-section part-photo-section"><div class="part-section-head"><div><span class="part-label">REAL PART PHOTOS</span><h3>What it looks like</h3></div><small>Live web references · actual installed model may vary</small></div>'+partPhoto(p)+'</div><div class="part-section"><div class="part-section-head"><div><span class="part-label">TROUBLESHOOTING</span><h3>Understand the part</h3></div></div><div class="part-info-grid"><article class="part-how"><div class="part-card-icon"><i class="ph ph-gear-six"></i></div><span class="part-label">HOW IT WORKS</span><p>'+h(p.works)+'</p></article><article><div class="part-card-icon"><i class="ph ph-warning"></i></div><span class="part-label">COMMON ISSUES</span><ul>'+p.issues.map(function(x){return '<li>'+h(x)+'</li>'}).join('')+'</ul></article><article><div class="part-card-icon"><i class="ph ph-magnifying-glass"></i></div><span class="part-label">QUICK CHECKS</span><ol>'+p.checks.map(function(x){return '<li>'+h(x)+'</li>'}).join('')+'</ol></article><article class="part-fix"><div class="part-card-icon"><i class="ph ph-wrench"></i></div><span class="part-label">FIX / NEXT ACTION</span><p>'+h(p.fix)+'</p></article></div></div><a class="part-source" href="'+p.url+'" target="_blank" rel="noopener"><i class="ph ph-book-open-text"></i><span><strong>OEM / technical reference</strong><small>'+h(p.source)+'</small></span><i class="ph ph-arrow-square-out part-source-arrow"></i></a><div class="part-intel-grid"><section class="part-intel-card"><div class="part-section-head"><div><span class="part-label">KURTEX AI</span><h3>AI related cases</h3><small class="ai-double-check"><i class="ph ph-warning-circle"></i> AI-selected history - double-check before use</small></div></div><div id="part-case-history" class="intel-mini-list"><div class="loading">Finding related cases...</div></div></section><section class="part-intel-card"><div class="part-section-head"><div><span class="part-label">KNOWLEDGE NOTES</span><h3>Team knowledge</h3></div></div><div id="part-knowledge-list" class="intel-mini-list"></div><div class="knowledge-add"><textarea id="knowledge-note-input" placeholder="Add a useful troubleshooting note..."></textarea><button class="btn secondary" onclick="saveKnowledgeNote()">Save note</button></div></section></div>';loadLivePartPhoto(p);loadPartCases(p);loadKnowledgeNotes(p);renderPartsManual(document.getElementById('parts-search')?document.getElementById('parts-search').value:'');}
setTimeout(function(){renderCommonParts();renderPartsManual('');updateTruckIssuePins();},0);

async function loadPartCases(p){
 var el=document.getElementById('part-case-history');if(!el)return;
 el.innerHTML='<div class="loading">Kurtex AI is checking historical cases...</div>';
 try{
  var r=await apiFetch('/api/ai/related_cases',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({part:{name:p.name,cat:p.cat,keywords:p.keywords,issues:p.issues,works:p.works}})});
  var x=await r.json(),items=x.items||[];if(!r.ok)throw new Error(x.error||'AI unavailable');
  el.innerHTML=items.length?items.map(function(x){var c=x.case,driver=c.report_driver||c.driver||'Driver unavailable',unit=c.unit_number||'Unit unavailable',type=(c.vehicle_type||'').toLowerCase(),typeLabel=type?type.charAt(0).toUpperCase()+type.slice(1):'Unit';return '<button class="intel-case-link part-case-link ai-case-match" data-id="'+attr(c.full_id||c.id)+'" onclick="openCase(this)"><span>'+statusBadge(c.status)+'</span><span class="part-case-copy"><strong>'+h(driver)+' <em>'+h(String(x.score))+'% match</em></strong><small><b>'+h(typeLabel)+' '+h(unit)+'</b><span> · '+h(c.description||'No issue description')+'</span></small><small class="ai-match-reason">'+h(x.reason||'AI relevance match')+'</small></span><i class="ph ph-arrow-right"></i></button>'}).join(''):'<div class="intel-empty ai-empty"><i class="ph ph-shield-check"></i><strong>No strong related history found</strong><span>Kurtex AI searched the case history but did not find a useful component-level match.</span></div>';
 }catch(e){el.innerHTML='<div class="intel-empty ai-empty"><i class="ph ph-shield-check"></i><strong>Related-case search unavailable</strong><span>The issue was sent to Notifications. No unverified cases are being shown.</span></div>';}
}
async function loadKnowledgeNotes(p){var el=document.getElementById('part-knowledge-list');if(!el)return;try{var r=await apiFetch('/api/knowledge_notes?part='+encodeURIComponent(p.id));if(!r.ok)throw new Error('notes');var x=await r.json(),items=x.items||[];el.innerHTML=items.length?items.slice(0,20).map(function(n){return '<div class="knowledge-note" data-note-id="'+attr(n.id)+'"><div class="knowledge-note-copy"><p>'+h(n.note)+'</p><small>'+h(n.author||'Agent')+' · '+h(n.updated||n.created||'')+'</small></div><div class="knowledge-note-actions"><button type="button" title="Edit note" onclick="editKnowledgeNote('+JSON.stringify(n.id)+')"><i class="ph ph-pencil-simple"></i></button><button type="button" class="danger" title="Delete note" onclick="deleteKnowledgeNote('+JSON.stringify(n.id)+')"><i class="ph ph-trash"></i></button></div></div>'}).join(''):'<div class="intel-empty">No team notes yet.</div>';}catch(e){el.innerHTML='<div class="intel-empty">Notes unavailable.</div>';}}
async function saveKnowledgeNote(){var p=partsDB.find(function(x){return x.id===selectedPart}),i=document.getElementById('knowledge-note-input');if(!p||!i||!i.value.trim())return;var b=i.parentElement.querySelector('button');if(b)b.disabled=true;try{var r=await apiFetch('/api/knowledge_notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({part:p.id,note:i.value.trim()})});if(!r.ok)throw new Error('save');i.value='';await loadKnowledgeNotes(p);}catch(e){alert('Could not save the note. Please try again.');}finally{if(b)b.disabled=false;}}
function editKnowledgeNote(id){var row=document.querySelector('.knowledge-note[data-note-id="'+CSS.escape(id)+'"]');if(!row)return;var p=row.querySelector('p'),copy=row.querySelector('.knowledge-note-copy'),actions=row.querySelector('.knowledge-note-actions');var current=p?p.textContent:'';copy.innerHTML='<textarea class="knowledge-edit-input"></textarea>';copy.querySelector('textarea').value=current;actions.innerHTML='<button type="button" class="knowledge-save-edit" onclick="saveKnowledgeEdit('+JSON.stringify(id)+')"><i class="ph ph-check"></i><span>Save</span></button><button type="button" onclick="loadKnowledgeNotes(partsDB.find(function(x){return x.id===selectedPart}))"><i class="ph ph-x"></i><span>Cancel</span></button>';copy.querySelector('textarea').focus();}
async function saveKnowledgeEdit(id){var row=document.querySelector('.knowledge-note[data-note-id="'+CSS.escape(id)+'"]'),i=row&&row.querySelector('textarea');if(!i||!i.value.trim())return;var r=await apiFetch('/api/knowledge_notes/'+encodeURIComponent(id),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({note:i.value.trim()})});if(!r.ok){alert('Could not update the note.');return;}var p=partsDB.find(function(x){return x.id===selectedPart});if(p)loadKnowledgeNotes(p);}
async function deleteKnowledgeNote(id){if(!confirm('Delete this knowledge note? This cannot be undone.'))return;var r=await apiFetch('/api/knowledge_notes/'+encodeURIComponent(id),{method:'DELETE'});if(!r.ok){alert('Could not delete the note.');return;}var p=partsDB.find(function(x){return x.id===selectedPart});if(p)loadKnowledgeNotes(p);}
async function loadSimilarCases(caseId){var el=document.getElementById('case-similarity');if(!el)return;try{var r=await apiFetch('/api/similar_cases?id='+encodeURIComponent(caseId)),x=await r.json(),items=x.items||[];el.innerHTML=items.length?items.map(function(x){var c=x.case;return '<button class="intel-case-link" data-id="'+attr(c.full_id||c.id)+'" onclick="openCase(this)"><span>'+statusBadge(c.status)+'</span><span><strong>'+h(c.driver||c.group||'Case')+'</strong><small>'+h(c.description||'')+'</small></span><span class="similarity-match">'+h((x.matched||[]).slice(0,3).join(' · '))+'</span></button>'}).join(''):'<div class="intel-empty">No similar historical cases found.</div>';}catch(e){el.innerHTML='<div class="intel-empty">Similarity unavailable.</div>';}}
async function loadRecurringProblems(){
  var el=document.getElementById('recurring-problems-content'); if(!el)return;
  try{
    var r=await apiFetch('/api/recurring_problems'),x=await r.json(),items=x.items||[],grouped={};
    items.forEach(function(v){
      var unit=String(v.unit||'Unknown');
      if(!grouped[unit]) grouped[unit]={unit:unit,total:0,latest:'',issues:[]};
      grouped[unit].total+=Number(v.count||0);
      grouped[unit].issues.push({problem:v.problem||'Unknown issue',count:Number(v.count||0),latest:v.latest||''});
      if(!grouped[unit].latest) grouped[unit].latest=v.latest||'';
    });
    var units=Object.keys(grouped).map(function(k){return grouped[k];}).sort(function(a,b){return b.total-a.total;}).slice(0,12);
    el.innerHTML=units.length?units.map(function(u,i){
      var issues=u.issues.sort(function(a,b){return b.count-a.count;});
      return '<details class="recurring-unit-tree" '+(i<3?'open':'')+'><summary><span class="recurring-unit-main"><i class="ph ph-truck"></i><strong>'+h(u.unit)+'</strong><small>'+u.total+' recurring reports'+(u.latest?' · latest '+h(u.latest):'')+'</small></span><span class="recurring-unit-count">'+u.total+'</span><i class="ph ph-caret-down recurring-caret"></i></summary><div class="recurring-issue-tree">'+issues.map(function(z){return '<div class="recurring-issue-line"><span><i class="ph ph-corner-down-right"></i>'+h(z.problem)+'</span><b>'+z.count+'×</b></div>';}).join('')+'</div></details>';
    }).join(''):'<div class="intel-empty">No repeated unit/problem patterns detected yet.</div>';
  }catch(e){el.innerHTML='<div class="intel-empty">Recurring analysis unavailable.</div>';}
}
