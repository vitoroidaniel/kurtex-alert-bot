// ── State ──────────────────────────────────────────────────────────────────
var stats = {};
var currentFilter = "today";
var currentPage = "overview";
var lbPeriod = "day";
var analyticsPeriod = "week";
var reportTab = "today";
var currentDateFilter = "";
var searchTimers = {};
var isDark = preferences.get("kurtex-theme") === "dark";

var bodyScrollY = 0;
var pages = [
  "overview",
  "cases",
  "missed",
  "leaderboard",
  "trends",
  "comparison",
  "fleet",
  "fleet_intel",
  "parts_manual",
  "my_profile",
  "agents",
];
var titles = {
  overview: "Overview",
  cases: "Cases",
  missed: "Missed Cases",
  leaderboard: "Leaderboard",
  trends: "Trends",
  comparison: "Week Comparison",
  fleet: "Fleet Stats",
  fleet_intel: "Fleet Intelligence",
  parts_manual: "Parts Manual",
  my_profile: "My Profile",
  agents: "Agent Profiles",
};
var medals = ["01", "02", "03"];

// ── Theme ──────────────────────────────────────────────────────────────────
function applyTheme() {
  document.documentElement.setAttribute(
    "data-theme",
    isDark ? "dark" : "light",
  );
  var icon = document.getElementById("theme-icon");
  var label = document.getElementById("theme-label");
  if (icon) icon.className = isDark ? "ph ph-moon" : "ph ph-sun";
  if (label) label.textContent = isDark ? "Dark Mode" : "Light Mode";
}
function toggleTheme() {
  isDark = !isDark;
  preferences.set("kurtex-theme", isDark ? "dark" : "light");
  applyTheme();
  if (currentPage === "trends") loadTrends();
}
applyTheme();

// ── Sidebar ────────────────────────────────────────────────────────────────
function toggleSidebar() {
  var sb = document.getElementById("sidebar");
  var ov = document.getElementById("sidebar-overlay");
  var isOpen = sb.classList.contains("open");
  if (isOpen) {
    sb.classList.remove("open");
    ov.classList.remove("open");
    document.body.style.overflow = "";
  } else {
    sb.classList.add("open");
    ov.classList.add("open");
    document.body.style.overflow = "hidden";
  }
}
function closeSidebar() {
  document.getElementById("sidebar").classList.remove("open");
  document.getElementById("sidebar-overlay").classList.remove("open");
  document.body.style.overflow = "";
}

function lockBodyScroll() {
  if (!document.body.classList.contains("modal-open")) {
    bodyScrollY = window.scrollY || document.documentElement.scrollTop || 0;
    document.body.classList.add("modal-open");
    document.body.style.position = "fixed";
    document.body.style.top = "-" + bodyScrollY + "px";
    document.body.style.left = "0";
    document.body.style.right = "0";
    document.body.style.width = "100%";
  }
}

function unlockBodyScroll() {
  if (!anyModalOpen() && document.body.classList.contains("modal-open")) {
    document.body.classList.remove("modal-open");
    document.body.style.position = "";
    document.body.style.top = "";
    document.body.style.left = "";
    document.body.style.right = "";
    document.body.style.width = "";
    document.body.style.overflow = "";
    window.scrollTo(0, bodyScrollY);
  }
}

function anyModalOpen() {
  return !!document.querySelector(
    ".modal-overlay.open,.report-modal-overlay.open",
  );
}

// ── Navigation ─────────────────────────────────────────────────────────────
function showPage(page) {
  if (
    pages.indexOf(page) < 0 ||
    !document.querySelector('.nav-item[data-page="' + page + '"]')
  )
    page = "overview";
  // Always close sidebar first on mobile
  document.getElementById("sidebar").classList.remove("open");
  document.getElementById("sidebar-overlay").classList.remove("open");
  document.body.style.overflow = "";

  document.querySelectorAll(".page").forEach(function (p) {
    p.classList.remove("active");
  });
  document.querySelectorAll(".nav-item").forEach(function (a) {
    a.classList.remove("active");
  });
  var pg = document.getElementById("page-" + page);
  if (pg) pg.classList.add("active");
  var nav = document.querySelector('.nav-item[data-page="' + page + '"]');
  if (nav) {
    nav.classList.add("active");
    var group = nav.closest(".nav-group-items");
    if (group && !group.classList.contains("open")) toggleGroup(group.id);
  }
  var titleEl = document.getElementById("page-title");
  if (titleEl) titleEl.textContent = titles[page] || page;
  var headerIcon = document.querySelector(".page-title-icon i");
  if (headerIcon) {
    var iconMap = {overview:"ph-squares-four",cases:"ph-clipboard-text",missed:"ph-phone-x",fleet:"ph-truck",fleet_intel:"ph-chart-line-up",intelligence:"ph-chart-line-up",agents:"ph-users-three",parts_manual:"ph-wrench",reports:"ph-file-text",trends:"ph-chart-bar",comparison:"ph-scales"};
    headerIcon.className = "ph " + (iconMap[page] || "ph-squares-four");
  }
  document.getElementById("page-description").textContent =
    page === "overview"
      ? "Today’s activity, outstanding cases, and team performance."
      : page === "cases"
        ? "Find cases and review progress. Assign and resolve cases in Telegram."
        : page === "parts_manual"
          ? "Look up truck and reefer parts, how they work, common symptoms, checks, and source material."
        : "Review " +
          (titles[page] || page).toLowerCase() +
          " and case history.";
  currentPage = page;
  document.body.setAttribute("data-current-page", page);
  preferences.set("kurtex-page", page);
  refresh(true);
}

function setCaseFilter(f, btn) {
  currentFilter = f;
  currentDateFilter = "";
  document.querySelectorAll("#page-cases .tab-btn").forEach(function (b) {
    b.classList.remove("active");
  });
  btn.classList.add("active");
  document.getElementById("cases-date-clear").style.display = "none";
  document.getElementById("cases-date-picker").value = "";
  loadCases();
}

function setCaseDateFilter(date) {
  if (!date) return;
  document.querySelectorAll("#page-cases .tab-btn").forEach(function (b) {
    b.classList.remove("active");
  });
  document.getElementById("cases-date-clear").style.display = "";
  currentFilter = "__date__";
  currentDateFilter = date;
  loadCases();
}

function clearDateFilter() {
  document.getElementById("cases-date-picker").value = "";
  document.getElementById("cases-date-clear").style.display = "none";
  currentDateFilter = "";
  currentFilter = "today";
  var firstTab = document.querySelector("#page-cases .tab-btn");
  if (firstTab) firstTab.classList.add("active");
  loadCases();
}

function setLbPeriod(p, btn) {
  lbPeriod = p;
  document
    .querySelectorAll("#page-leaderboard .toggle-btn")
    .forEach(function (b) {
      b.classList.remove("active");
    });
  btn.classList.add("active");
  renderLeaderboard();
}

function setAnalyticsPeriod(p, btn) {
  analyticsPeriod = p;
  document
    .querySelectorAll("#page-analytics .toggle-btn")
    .forEach(function (b) {
      b.classList.remove("active");
    });
  btn.classList.add("active");
  renderAnalytics();
}

function onSearch(type) {
  clearTimeout(searchTimers[type]);
  searchTimers[type] = setTimeout(function () {
    if (type === "cases") loadCases();
    else if (type === "missed") loadMissed();
  }, 300);
}

// ── Helpers ────────────────────────────────────────────────────────────────
function statusBadge(s) {
  var map = {
    open: "s-open",
    assigned: "s-assigned",
    reported: "s-reported",
    done: "s-done",
    missed: "s-missed",
  };
  return (
    '<span class="status-badge ' +
    (map[s] || "s-open") +
    '">' +
    h(s) +
    "</span>"
  );
}

function h(v) {
  return String(v == null ? "" : v).replace(/[&<>"']/g, function (ch) {
    return {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch];
  });
}

function attr(v) {
  return h(v);
}

function caseTable(cases) {
  if (!cases || !cases.length)
    return '<div class="empty-state">No cases found</div>';
  var rows = cases
    .map(function (c) {
      var cid = c.full_id || "";
      return (
        '<tr onclick="openCase(this.dataset.id)" data-id="' +
        attr(cid) +
        '">' +
        "<td><b>" +
        h(c.driver || "—") +
        "</b></td>" +
        '<td style="color:var(--muted)">' +
        h(c.group || "—") +
        "</td>" +
        "<td>" +
        h(c.agent || "—") +
        "</td>" +
        "<td>" +
        statusBadge(c.status) +
        (c.reassigned ? '<span class="reassign-badge">reassigned</span>' : "") +
        "</td>" +
        '<td style="color:var(--muted);font-size:11px">' +
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
    .join("");
  return (
    "<table><thead><tr>" +
    "<th>Reported By</th><th>Group</th><th>Assigned To</th><th>Status</th><th>Opened</th><th>Response</th><th>Description</th>" +
    "</tr></thead><tbody>" +
    rows +
    "</tbody></table>"
  );
}
function groupRateRows(groups) {
  if (!groups || !groups.length)
    return '<div style="color:var(--muted);font-size:13px;padding:8px 0">No data yet</div>';
  var maxTotal = groups[0].total || 1;
  return groups
    .map(function (g, i) {
      var rateColor =
        g.rate >= 80
          ? "var(--green)"
          : g.rate >= 50
            ? "var(--yellow)"
            : "var(--red)";
      return (
        '<div class="list-row" style="flex-direction:column;align-items:stretch;gap:4px;padding:8px 0">' +
        '<div style="display:flex;align-items:center;gap:7px">' +
        '<span class="medal">' +
        (medals[i] || i + 1 + ".") +
        "</span>" +
        '<span class="list-name" style="font-size:12px;font-weight:600">' +
        h(g.name) +
        "</span>" +
        '<span style="margin-left:auto;font-size:11px;font-weight:700;color:' +
        rateColor +
        '">' +
        g.rate +
        "% ✓</span>" +
        '<span style="font-size:11px;color:var(--muted)">' +
        g.total +
        " cases</span>" +
        "</div>" +
        '<div style="display:flex;gap:3px;height:4px;border-radius:3px;overflow:hidden;background:var(--surface3)">' +
        '<div style="width:' +
        Math.round((g.done / Math.max(g.total, 1)) * 100) +
        '%;background:var(--green);transition:width .4s"></div>' +
        '<div style="width:' +
        Math.round((g.missed / Math.max(g.total, 1)) * 100) +
        '%;background:var(--red);transition:width .4s"></div>' +
        "</div>" +
        "</div>"
      );
    })
    .join("");
}

function unitProblemRows(units) {
  if (!units || !units.length)
    return '<div style="color:var(--muted);font-size:13px;padding:8px 0">No data yet</div>';
  var maxCount = units[0].count || 1;
  var vtypeStyles = {
    truck: "color:#fff;background:#000",
    trailer: "color:var(--purple);background:var(--surface2)",
    reefer: "color:var(--accent);background:var(--surface2)",
  };
  return units
    .map(function (u, i) {
      var vs =
        vtypeStyles[(u.vtype || "").toLowerCase()] ||
        "color:var(--muted);background:var(--surface2)";
      return (
        '<div class="list-row" style="flex-direction:column;align-items:stretch;gap:4px;padding:8px 0;cursor:pointer" data-unit="' +
        attr(u.unit) +
        '" data-vtype="' +
        attr(u.vtype || "") +
        '" onclick="openUnitModal(this.dataset.unit,this.dataset.vtype)">' +
        '<div style="display:flex;align-items:center;gap:7px">' +
        '<span class="medal">' +
        (medals[i] || i + 1 + ".") +
        "</span>" +
        '<span class="list-name" style="font-size:12px;font-weight:600">' +
        h(u.unit) +
        "</span>" +
        (u.vtype
          ? '<span style="font-size:10px;font-weight:700;' +
            vs +
            ';padding:2px 7px;border-radius:20px;text-transform:capitalize">' +
            h(u.vtype) +
            "</span>"
          : "") +
        '<span style="margin-left:auto;font-size:11px;font-weight:700;color:var(--red)">' +
        u.count +
        " cases</span>" +
        "</div>" +
        '<div style="height:4px;border-radius:3px;overflow:hidden;background:var(--surface3)">' +
        '<div style="width:' +
        Math.round((u.count / maxCount) * 100) +
        '%;background:var(--red);transition:width .4s"></div>' +
        "</div>" +
        "</div>"
      );
    })
    .join("");
}

function listRows(items, maxCount) {
  if (!items || !items.length)
    return '<div style="color:var(--muted);font-size:13px;padding:8px 0">No data yet</div>';
  return items
    .map(function (item, i) {
      return (
        '<div class="list-row">' +
        '<span class="medal">' +
        (medals[i] || i + 1 + ".") +
        "</span>" +
        '<span class="list-name">' +
        h(item.name) +
        "</span>" +
        '<div class="bar-wrap"><div class="bar-fill" style="width:' +
        Math.round((item.count / (maxCount || 1)) * 100) +
        '%"></div></div>' +
        '<span class="list-count">' +
        item.count +
        "</span>" +
        "</div>"
      );
    })
    .join("");
}

function buildTimeline(c) {
  var steps = [
    { label: "Open", time: c.opened || "" },
    { label: "Assigned", time: c.assigned_at || "" },
    { label: "Reported", time: "" },
    { label: "Resolved", time: c.closed || "" },
  ];
  var order = ["open", "assigned", "reported", "done"];
  var si = Math.max(0, order.indexOf(c.status));
  var html = '<div class="timeline">';
  steps.forEach(function (s, i) {
    var isDone = i < si;
    var isActive = i === si || (c.status === "missed" && i === 0);
    var dotClass = isDone ? "done" : isActive ? "active" : "";
    html +=
      '<div class="tl-step' +
      (isDone ? " done-step" : "") +
      '">' +
      '<div class="tl-dot ' +
      dotClass +
      '">' +
      (isDone ? "✓" : i + 1) +
      "</div>" +
      '<div class="tl-label">' +
      s.label +
      "</div>" +
      '<div class="tl-time">' +
      (s.time && s.time !== "—" ? h(s.time) : "") +
      "</div>" +
      "</div>";
  });
  html += "</div>";
  return html;
}

// ── Data loading ───────────────────────────────────────────────────────────
