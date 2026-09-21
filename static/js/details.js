async function viewFullReport(caseIdOrEl) {
  var caseId =
    typeof caseIdOrEl === "string" ? caseIdOrEl : caseIdOrEl.dataset.id;
  document.getElementById("report-view-overlay").classList.add("open");
  lockBodyScroll();
  document.getElementById("report-view-body").innerHTML =
    '<div class="loading">Loading report...</div>';
  try {
    var r = await apiFetch(
      "/api/case?id=" + encodeURIComponent(caseId),
      "case-report",
    );
    if (!r.ok) {
      document.getElementById("report-view-body").innerHTML =
        '<div class="loading">Error loading report.</div>';
      return;
    }
    var c = await r.json();
    document.getElementById("report-view-title").textContent =
      "Report — " + (c.driver || "—") + " / " + (c.group || "—");

    // case data loaded

    // Parse the notes field which contains the bot report text
    var notes = c.full_notes || "";
    var isRealReport = notes && notes !== "case reported";

    // Build report rows from case data
    var vtype = c.vehicle_type || "";
    var unitLabel =
      vtype === "truck"
        ? "Truck"
        : vtype === "trailer"
          ? "Trailer"
          : vtype === "reefer"
            ? "Reefer"
            : "Unit";

    function row(label, val) {
      if (!val || val === "—") return "";
      return (
        '<div style="display:flex;gap:12px;padding:10px 0;border-bottom:1px solid var(--border)">' +
        '<div style="min-width:160px;font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;flex-shrink:0">' +
        h(label) +
        "</div>" +
        '<div style="font-size:14px;font-weight:500;color:var(--text)">' +
        h(val) +
        "</div>" +
        "</div>"
      );
    }

    // Priority icon
    var priorityIcon =
      c.priority === "critical"
        ? "🔴"
        : c.priority === "high"
          ? "🟠"
          : c.priority === "medium"
            ? "🟡"
            : "🟢";
    var priorityLabel = c.priority
      ? priorityIcon +
        " " +
        c.priority.charAt(0).toUpperCase() +
        c.priority.slice(1)
      : "";

    // Priority
    var pIcons = { critical: "🔴", high: "🟠", medium: "🟡", low: "🟢" };
    var pIcon = pIcons[c.priority] || "🟢";
    var pText = c.priority
      ? c.priority.charAt(0).toUpperCase() + c.priority.slice(1)
      : "Low";

    // Unit label
    var vtypeLabel =
      vtype === "truck"
        ? "Truck"
        : vtype === "trailer"
          ? "Trailer"
          : vtype === "reefer"
            ? "Reefer"
            : "";

    // Load section
    var loadType = c.load_type || "";
    var loadIsEmpty = loadType.toLowerCase() === "empty";

    function line(label, val) {
      if (!val || val === "—") return "";
      return (
        '<div style="font-size:13px;margin-bottom:6px"><b>' +
        h(label) +
        ":</b> " +
        h(val) +
        "</div>"
      );
    }

    // Build report exactly like Telegram bot
    var s =
      '<div style="font-size:15px;font-weight:700;margin-bottom:2px">' +
      pIcon +
      " Case Report";
    if (vtypeLabel) s += " — " + vtypeLabel;
    s += "</div>";
    s +=
      '<div style="font-size:13px;margin-bottom:16px">Priority: <b>' +
      h(pText) +
      "</b></div>";

    s += '<div style="margin-bottom:16px">';
    if (vtypeLabel && c.unit_number) s += line(vtypeLabel, c.unit_number);
    s += line("Reported by", c.report_driver || c.driver);
    s += line("Issue", c.issue_text || c.full_description);
    s += "</div>";

    if (loadType) {
      s += '<div style="margin-bottom:16px">';
      s += line("JBS/Broker Load", loadType);
      if (!loadIsEmpty) {
        s += line("Pick up Location/Time", c.pickup);
        s += line("Delivery Location/Time", c.delivery);
      }
      s += line("Current Location", c.location);
      s += "</div>";
    }

    if (vtype === "reefer") {
      s += '<div style="margin-bottom:16px">';
      s += line("Setpoint", c.setpoint);
      s += line("Current temp", c.current_temp);
      s += line("Temp recorder", c.temp_recorder);
      s += "</div>";
    }

    if (c.comments) {
      s +=
        '<div style="margin-bottom:16px">' +
        line("Comments", c.comments) +
        "</div>";
    }

    s += line("Handled by", c.agent);

    document.getElementById("report-view-body").innerHTML =
      '<div style="background:var(--surface2);border-radius:12px;padding:18px 20px;font-family:inherit;line-height:1">' +
      s +
      "</div>";
  } catch (e) {
    if (e.name === "AbortError") return;
    document.getElementById("report-view-body").innerHTML =
      '<div class="loading">Error loading report.</div>';
  }
}

function closeReportView() {
  var overlay = document.getElementById("report-view-overlay");
  if (overlay.classList.contains("open")) {
    overlay.classList.remove("open");
    unlockBodyScroll();
  }
}

function printReportView() {
  var orig = document.title;
  document.title = document.getElementById("report-view-title").textContent;
  document.body.classList.add("printing-case");
  try {
    window.print();
  } finally {
    document.body.classList.remove("printing-case");
    document.title = orig;
  }
}

var agentModalState = {
  name: "",
  username: "",
  period: "all",
  offset: 0,
  limit: 15,
  rows: "",
};

function agentCaseRow(c) {
  var cid = c.full_id || "";
  return (
    '<tr style="border-bottom:1px solid var(--border);cursor:pointer" data-id="' +
    attr(cid) +
    '" onclick="closeAgentModal();var id=this.dataset.id;setTimeout(function(){openCase(id);},200)">' +
    '<td style="padding:8px 10px;font-weight:500">' +
    h(c.driver || "—") +
    "</td>" +
    '<td style="padding:8px 10px;color:var(--muted)">' +
    h(c.group || "—") +
    "</td>" +
    '<td style="padding:8px 10px">' +
    statusBadge(c.status) +
    "</td>" +
    '<td style="padding:8px 10px;color:var(--muted);font-size:11px">' +
    h(c.opened || "—") +
    "</td>" +
    "</tr>"
  );
}

async function openAgentModal(nameOrEl, username, agentId) {
  var name = typeof nameOrEl === "string" ? nameOrEl : nameOrEl.dataset.agent;
  var uname =
    typeof nameOrEl === "string"
      ? username || ""
      : nameOrEl.dataset.username || "";
  agentModalState = {
    name: name,
    username: uname,
    id: agentId || "",
    period: "all",
    offset: 0,
    limit: 15,
    rows: "",
  };
  document.getElementById("agent-modal-overlay").classList.add("open");
  lockBodyScroll();
  document.getElementById("agent-modal-body").innerHTML =
    '<div class="loading">Loading profile...</div>';
  document.getElementById("agent-modal-title").textContent = name;
  await loadAgentProfileData(true);
}

function setAgentPeriod(period) {
  if (agentModalState.period === period) return;
  agentModalState.period = period;
  agentModalState.offset = 0;
  agentModalState.rows = "";
  loadAgentProfileData(true);
}

async function loadAgentModalMore() {
  await loadAgentProfileData(false);
}

async function loadAgentProfileData(resetHeader) {
  var body = document.getElementById("agent-modal-body");
  var s = agentModalState;
  var requestOffset = resetHeader ? 0 : s.offset + s.limit;
  if (resetHeader)
    body.innerHTML = '<div class="loading">Loading profile...</div>';
  try {
    var url =
      "/api/agent?name=" +
      encodeURIComponent(s.name) +
      "&username=" +
      encodeURIComponent(s.username || "") +
      "&period=" +
      encodeURIComponent(s.period) +
      "&id=" +
      encodeURIComponent(s.id || "") +
      "&offset=" +
      requestOffset +
      "&limit=" +
      s.limit;
    var r = await apiFetch(url);
    if (!r.ok) {
      body.innerHTML = '<div class="loading">Agent not found.</div>';
      return;
    }
    var a = await r.json();
    s.offset = requestOffset;
    var ps = a.period_stats || { total: 0, done: 0, missed: 0, rate: 0 };
    var newRows = (a.cases || []).map(agentCaseRow).join("");
    s.rows = s.offset > 0 ? s.rows + newRows : newRows;
    var periodLabels = {
      today: "Today",
      week: "This Week",
      month: "This Month",
      all: "All Time",
    };
    function tab(p) {
      return (
        '<button class="toggle-btn' +
        (s.period === p ? " active" : "") +
        '" onclick="setAgentPeriod(\'' +
        p +
        "')\">" +
        periodLabels[p] +
        "</button>"
      );
    }
    body.innerHTML =
      '<div class="mini-stat-grid" style="margin-bottom:16px">' +
      '<div class="agent-stat"><div class="agent-stat-val" style="color:var(--accent)">' +
      a.total +
      '</div><div class="agent-stat-label">Total (all-time)</div></div>' +
      '<div class="agent-stat"><div class="agent-stat-val" style="color:var(--green)">' +
      a.done +
      '</div><div class="agent-stat-label">Resolved</div></div>' +
      '<div class="agent-stat"><div class="agent-stat-val" style="color:var(--red)">' +
      a.missed +
      '</div><div class="agent-stat-label">Missed</div></div>' +
      '<div class="agent-stat"><div class="agent-stat-val" style="color:var(--accent)">' +
      a.rate +
      '%</div><div class="agent-stat-label">Rate</div></div>' +
      "</div>" +
      '<div class="toggle-tabs" style="margin-bottom:10px">' +
      tab("today") +
      tab("week") +
      tab("month") +
      tab("all") +
      "</div>" +
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;flex-wrap:wrap;gap:4px">' +
      '<div style="font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em">Cases — ' +
      periodLabels[s.period] +
      "</div>" +
      '<div style="font-size:11px;color:var(--muted)">' +
      ps.total +
      " total &middot; " +
      ps.done +
      " done &middot; " +
      ps.missed +
      " missed</div>" +
      "</div>" +
      (s.rows
        ? '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px;min-width:400px">' +
          '<thead><tr style="background:var(--surface2);border-bottom:1px solid var(--border)">' +
          '<th style="padding:8px 10px;text-align:left;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Reported By</th>' +
          '<th style="padding:8px 10px;text-align:left;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Group</th>' +
          '<th style="padding:8px 10px;text-align:left;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Status</th>' +
          '<th style="padding:8px 10px;text-align:left;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Date</th>' +
          '</tr></thead><tbody id="agent-case-rows">' +
          s.rows +
          "</tbody></table></div>" +
          (a.has_more
            ? '<div style="text-align:center;margin-top:12px"><button class="btn" style="background:var(--surface2);color:var(--text)" onclick="loadAgentModalMore()"><i class="ph ph-arrow-down"></i> Load More</button></div>'
            : "")
        : '<div style="color:var(--muted);font-size:13px;padding:20px 0;text-align:center">No cases in this period.</div>');
  } catch (e) {
    if (e.name === "AbortError") return;
    console.error("agent modal error:", e);
    body.innerHTML = '<div class="loading">Error loading profile.</div>';
  }
}
function closeAgentModal() {
  var overlay = document.getElementById("agent-modal-overlay");
  if (overlay.classList.contains("open")) {
    overlay.classList.remove("open");
    unlockBodyScroll();
  }
}

// ── Unit Modal ─────────────────────────────────────────────────────────────
async function openUnitModal(unitNumber, vtype) {
  var overlay = document.getElementById("unit-modal-overlay");
  var body = document.getElementById("unit-modal-body");
  var title = document.getElementById("unit-modal-title");
  overlay.classList.add("open");
  lockBodyScroll();
  body.innerHTML = '<div class="loading">Loading unit history...</div>';
  title.textContent = "Unit " + unitNumber;
  try {
    var url = "/api/unit?unit=" + encodeURIComponent(unitNumber);
    if (vtype) url += "&vtype=" + encodeURIComponent(vtype);
    var r = await apiFetch(url);
    if (!r.ok) {
      body.innerHTML = '<div class="loading">Error loading unit data.</div>';
      return;
    }
    var d = await r.json();
    var vtypeLabel = d.vtype
      ? ' <span style="font-size:11px;color:var(--muted);text-transform:uppercase;background:var(--surface2);padding:2px 7px;border-radius:5px">' +
        h(d.vtype) +
        "</span>"
      : "";
    title.innerHTML = "Unit " + h(unitNumber) + vtypeLabel;
    var issuesHtml = "";
    if (d.top_issues && d.top_issues.length) {
      issuesHtml =
        '<div style="margin-bottom:16px"><div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--muted);margin-bottom:8px;letter-spacing:.05em">Top Issues</div>' +
        d.top_issues
          .map(function (x) {
            return (
              '<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--border);font-size:12px">' +
              '<span style="flex:1">' +
              h(x.issue || "—") +
              "</span>" +
              '<span style="font-weight:700;color:var(--accent);background:var(--accent-bg);padding:1px 8px;border-radius:20px;font-size:11px">' +
              h(x.count) +
              "x</span>" +
              "</div>"
            );
          })
          .join("") +
        "</div>";
    }
    var statsHtml =
      '<div class="mini-stat-grid" style="margin-bottom:16px">' +
      '<div class="stat-card"><div class="stat-label">Total</div><div class="stat-value v-accent" style="font-size:20px">' +
      d.total +
      "</div></div>" +
      '<div class="stat-card"><div class="stat-label">Active</div><div class="stat-value v-yellow" style="font-size:20px">' +
      d.active +
      "</div></div>" +
      '<div class="stat-card"><div class="stat-label">Resolved</div><div class="stat-value v-green" style="font-size:20px">' +
      d.done +
      "</div></div>" +
      '<div class="stat-card"><div class="stat-label">Missed</div><div class="stat-value v-red" style="font-size:20px">' +
      d.missed +
      "</div></div>" +
      "</div>";
    var rows = "";
    if (d.cases && d.cases.length) {
      rows =
        '<div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--muted);margin-bottom:8px;letter-spacing:.05em">Recent cases (up to 50)</div>' +
        '<div class="table-wrap"><div class="table-scroll">' +
        "<table><thead><tr><th>Reported By</th><th>Status</th><th>Opened</th><th>Response</th><th>Description</th></tr></thead><tbody>" +
        d.cases
          .map(function (c) {
            var cid = c.full_id || "";
            return (
              '<tr style="cursor:pointer" onclick="closeUnitModal();var id=this.dataset.id;setTimeout(function(){openCase(id);},200)" data-id="' +
              attr(cid) +
              '">' +
              "<td><b>" +
              h(c.driver || "—") +
              "</b></td>" +
              "<td>" +
              statusBadge(c.status) +
              "</td>" +
              '<td style="font-size:11px;color:var(--muted)">' +
              h(c.opened || "—") +
              "</td>" +
              '<td style="font-size:11px">' +
              h(c.response || "—") +
              "</td>" +
              '<td class="desc-cell">' +
              h(c.description || "") +
              "</td>" +
              "</tr>"
            );
          })
          .join("") +
        "</tbody></table></div></div>";
    } else {
      rows = '<div class="empty-state">No cases found for this unit.</div>';
    }
    body.innerHTML = statsHtml + issuesHtml + rows;
  } catch (e) {
    if (e.name === "AbortError") return;
    body.innerHTML = '<div class="loading">Error: ' + h(e.message) + "</div>";
  }
}
function closeUnitModal() {
  var overlay = document.getElementById("unit-modal-overlay");
  if (overlay.classList.contains("open")) {
    overlay.classList.remove("open");
    unlockBodyScroll();
  }
}

// ── Report ─────────────────────────────────────────────────────────────────
