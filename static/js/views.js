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
      "</div>" +
      '<div class="stat-grid" style="margin-bottom:20px">' +
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

var fleetStatusState = { vtype: "all", search: "" };

function setFleetStatusFilter(vtype) {
  fleetStatusState.vtype = vtype;
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
      "';renderFleetStatus()\">" +
      label +
      "</button>"
    );
  }
  var rows = filtered
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
    '" oninput="fleetStatusState.search=this.value;renderFleetStatus()"></div>' +
    (filtered.length
      ? '<div class="table-wrap"><div class="table-scroll"><table><thead><tr><th>Unit</th><th>Status</th><th>Issue</th><th>Driver</th><th>Opened</th></tr></thead><tbody>' +
        rows +
        "</tbody></table></div></div>"
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

async function loadAgents() {
  var el = document.getElementById("agents-content");
  if (!el) return;
  if (!el.children.length || el.querySelector(":scope > .loading")) updateHTML(el, '<div class="loading">Loading...</div>');
  try {
    var r = await apiFetch("/api/agents");
    if (r.status === 403) {
      updateHTML(el, '<div class="loading">Access denied.</div>');
      return;
    }
    if (!r.ok) {
      updateHTML(el, '<div class="loading">Error loading agents.</div>');
      return;
    }
    var agents = await r.json();
    if (!agents.length) {
      updateHTML(el, '<div class="empty-state">No agents found.</div>');
      return;
    }
    var cards = agents
      .map(function (a, i) {
        var init = (a.name || "?")[0].toUpperCase();
        var rate = a.rate || 0;
        var rateColor = "var(--text)";
        return (
          '<div class="card agent-card" role="button" tabindex="0" data-agent="' +
          attr(a.name || "") +
          '" data-username="' +
          attr(a.username || "") +
          '" data-agent-id="' +
          attr(a.id || "") +
          '" onclick="openAgentModal(this.dataset.agent, this.dataset.username, this.dataset.agentId)">' +
          '<div class="agent-card-rank">#' +
          (i + 1) +
          "</div>" +
          '<div style="display:flex;align-items:center;gap:14px;margin-bottom:18px;padding-right:36px">' +
          '<div class="agent-card-avatar">' +
          h(init) +
          "</div>" +
          '<div style="min-width:0"><div style="font-size:17px;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' +
          h(a.name || "") +
          "</div>" +
          '<div style="font-size:12px;color:var(--muted)">' +
          (a.username ? "@" + h(a.username) : "No username") +
          "</div></div>" +
          "</div>" +
          '<div class="mini-stat-grid">' +
          '<div class="agent-card-statbox"><div class="agent-card-statval" style="color:var(--text)">' +
          (a.total || 0) +
          '</div><div class="agent-card-statlabel">Total</div></div>' +
          '<div class="agent-card-statbox"><div class="agent-card-statval" style="color:var(--text)">' +
          (a.done || 0) +
          '</div><div class="agent-card-statlabel">Resolved</div></div>' +
          '<div class="agent-card-statbox"><div class="agent-card-statval" style="color:var(--text)">' +
          (a.missed || 0) +
          '</div><div class="agent-card-statlabel">Missed</div></div>' +
          '<div class="agent-card-statbox"><div class="agent-card-statval" style="color:' +
          rateColor +
          '">' +
          rate +
          '%</div><div class="agent-card-statlabel">Resolution rate</div></div>' +
          "</div>" +
          '<div class="agent-rate-track"><div class="agent-rate-fill" style="width:' +
          Math.min(rate, 100) +
          '%"></div></div>' +
          '<div class="agent-card-footer" style="justify-content:flex-end">' +
          '<span style="color:var(--accent);font-weight:700;display:flex;align-items:center;gap:4px">View Cases <i class="ph ph-arrow-right"></i></span>' +
          "</div>" +
          "</div>"
        );
      })
      .join("");
    updateHTML(el, '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,340px),1fr));gap:18px">' +
      cards +
      "</div>");
  } catch (e) {
    if (e.name === "AbortError") return;
    console.error(e);
    if (!el.children.length || el.querySelector(":scope > .loading")) updateHTML(el, errorContent(e));
  }
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
  tractor: {title:'American semi tractor', note:'Real CC Attribution web 3D asset · drag to rotate, scroll/pinch to zoom', id:'11f3b663616a4ff5956a7d86a74c009a', source:'https://sketchfab.com/3d-models/american-semi-truck-11f3b663616a4ff5956a7d86a74c009a'},
  reefer: {title:"53' reefer trailer", note:'Separate CC Attribution reefer asset · inspect trailer exterior and running gear', id:'904c9af9277d410c98c395fd9f8f9250', source:'https://sketchfab.com/3d-models/53-reefer-trailer-904c9af9277d410c98c395fd9f8f9250'},
  engine: {title:'Truck diesel engine', note:'Dedicated CC Attribution mechanical asset · inspect engine separately', id:'d7ff313ff1e64c4db803e39b600a0fbf', source:'https://sketchfab.com/3d-models/truck-engine-d7ff313ff1e64c4db803e39b600a0fbf'},
  hood: {title:'Open-hood semi reference', note:'CC Attribution open-engine-bay reference · useful for hood/engine orientation', id:'ea0c42d238c44a8abc53edc03779ad72', source:'https://sketchfab.com/3d-models/open-hood-semi-truck-ea0c42d238c44a8abc53edc03779ad72'}
};
function setTruckLabModel(key, btn){
  var m=truckLabModels[key]; if(!m)return;
  document.querySelectorAll('.truck-lab-tab').forEach(function(b){b.classList.toggle('active',b===btn)});
  document.getElementById('truck-model-title').textContent=m.title;
  document.getElementById('truck-model-note').textContent=m.note;
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
