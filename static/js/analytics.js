function toggleGroup(id) {
  var el = document.getElementById(id);
  var caret = document.getElementById("caret-" + id);
  var open = el.classList.contains("open");
  el.classList.toggle("open", !open);
  if (caret) caret.classList.toggle("open", !open);
}

// ── Trends ───────────────────────────────────────────────────────────────────
var trendPeriod = 7;
var trendCharts = {};
var trendSnapshot = "";

function setTrendPeriod(days, btn) {
  trendPeriod = days;
  document.querySelectorAll("#page-trends .toggle-btn").forEach(function (b) {
    b.classList.remove("active");
  });
  btn.classList.add("active");
  loadTrends();
}

async function loadTrends() {
  try {
    var r = await apiFetch("/api/trends?period=" + trendPeriod);
    if (!r.ok) return;
    var d = await r.json();

    var snapshot = JSON.stringify([d, trendPeriod, isDark]);
    if (snapshot === trendSnapshot) return;
    trendSnapshot = snapshot;
    var fallback = document.getElementById("chart-fallback");
    fallback.hidden = typeof Chart !== "undefined";
    if (typeof Chart === "undefined") {
      updateHTML(fallback, '<p class="empty-state">Charts are unavailable. Daily figures are shown below.</p><div class="table-scroll"><table><thead><tr><th>Date</th><th>Total</th><th>Resolved</th><th>Missed</th><th>Avg response (s)</th></tr></thead><tbody>' +
        d.labels
          .map(function (label, i) {
            return (
              "<tr><td>" +
              h(label) +
              "</td><td>" +
              d.totals[i] +
              "</td><td>" +
              d.resolved[i] +
              "</td><td>" +
              d.missed[i] +
              "</td><td>" +
              d.avg_resp[i] +
              "</td></tr>"
            );
          })
          .join("") +
        "</tbody></table></div>");
      return;
    }
    // Destroy existing charts
    Object.values(trendCharts).forEach(function (c) {
      if (c) c.destroy();
    });
    trendCharts = {};

    var accent = getComputedStyle(document.documentElement)
      .getPropertyValue("--accent")
      .trim();
    var green = getComputedStyle(document.documentElement)
      .getPropertyValue("--green")
      .trim();
    var red = getComputedStyle(document.documentElement)
      .getPropertyValue("--red")
      .trim();
    var muted = getComputedStyle(document.documentElement)
      .getPropertyValue("--muted")
      .trim();

    // Cases over time
    var ctx1 = document.getElementById("trend-cases-chart").getContext("2d");
    trendCharts.cases = new Chart(ctx1, {
      type: "line",
      data: {
        labels: d.labels,
        datasets: [
          {
            label: "Total",
            data: d.totals,
            borderColor: accent,
            backgroundColor: accent + "22",
            fill: true,
            tension: 0.4,
            pointRadius: 2,
          },
          {
            label: "Resolved",
            data: d.resolved,
            borderColor: green,
            backgroundColor: "transparent",
            tension: 0.4,
            pointRadius: 2,
          },
          {
            label: "Missed",
            data: d.missed,
            borderColor: red,
            backgroundColor: "transparent",
            tension: 0.4,
            pointRadius: 2,
          },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { labels: { color: muted, font: { size: 11 } } } },
        scales: {
          x: { ticks: { color: muted, font: { size: 10 }, maxTicksLimit: 10 } },
          y: { ticks: { color: muted, font: { size: 10 } }, beginAtZero: true },
        },
      },
    });

    // Avg response time
    var ctx2 = document.getElementById("trend-resp-chart").getContext("2d");
    trendCharts.resp = new Chart(ctx2, {
      type: "line",
      data: {
        labels: d.labels,
        datasets: [
          {
            label: "Avg Resp (secs)",
            data: d.avg_resp,
            borderColor: accent,
            backgroundColor: accent + "22",
            fill: true,
            tension: 0.4,
            pointRadius: 2,
          },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { labels: { color: muted, font: { size: 11 } } } },
        scales: {
          x: { ticks: { color: muted, font: { size: 10 }, maxTicksLimit: 10 } },
          y: { ticks: { color: muted, font: { size: 10 } }, beginAtZero: true },
        },
      },
    });

    // Daily bar
    var ctx3 = document.getElementById("trend-bar-chart").getContext("2d");
    trendCharts.bar = new Chart(ctx3, {
      type: "bar",
      data: {
        labels: d.labels,
        datasets: [
          { label: "Total", data: d.totals, backgroundColor: accent + "88" },
          {
            label: "Resolved",
            data: d.resolved,
            backgroundColor: green + "88",
          },
          { label: "Missed", data: d.missed, backgroundColor: red + "88" },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { labels: { color: muted, font: { size: 11 } } } },
        scales: {
          x: {
            stacked: false,
            ticks: { color: muted, font: { size: 10 }, maxTicksLimit: 15 },
          },
          y: { beginAtZero: true, ticks: { color: muted, font: { size: 10 } } },
        },
      },
    });
  } catch (e) {
    if (e.name === "AbortError") return;
    document.getElementById("chart-fallback").hidden = false;
    updateHTML(document.getElementById("chart-fallback"), errorContent(e));
  }
}

// ── Comparison ───────────────────────────────────────────────────────────────
async function loadComparison() {
  var el = document.getElementById("comparison-content");
  if (!el.children.length || el.querySelector(":scope > .loading")) updateHTML(el, '<div class="loading">Loading comparison...</div>');
  try {
    var r = await apiFetch("/api/comparison");
    var d = await r.json();
    function deltaHtml(delta, label) {
      if (!delta || delta.pct === 0)
        return '<span style="color:var(--muted);font-size:11px">—</span>';
      var color = delta.up ? "var(--green)" : "var(--red)";
      var arrow = delta.up ? "↑" : "↓";
      return (
        '<span style="color:' +
        color +
        ';font-size:11px;font-weight:600">' +
        arrow +
        " " +
        delta.pct +
        "%</span>"
      );
    }
    function compRow(label, thisVal, lastVal, delta, unit) {
      unit = unit || "";
      return (
        '<tr style="border-bottom:1px solid var(--border)">' +
        '<td style="padding:12px 14px;font-size:13px;font-weight:500;color:var(--muted)">' +
        label +
        "</td>" +
        '<td style="padding:12px 14px;font-size:18px;font-weight:800;color:var(--text);text-align:center">' +
        thisVal +
        unit +
        "</td>" +
        '<td style="padding:12px 14px;font-size:16px;font-weight:600;color:var(--muted2);text-align:center">' +
        lastVal +
        unit +
        "</td>" +
        '<td style="padding:12px 14px;text-align:center">' +
        deltaHtml(delta) +
        "</td>" +
        "</tr>"
      );
    }
    updateHTML(el, '<div class="card">' +
      '<table style="width:100%;border-collapse:collapse">' +
      '<thead><tr style="background:var(--surface2)">' +
      '<th style="padding:10px 14px;text-align:left;font-size:11px;color:var(--muted);font-weight:600;text-transform:uppercase">Metric</th>' +
      '<th style="padding:10px 14px;text-align:center;font-size:11px;color:var(--accent);font-weight:700;text-transform:uppercase">This Week</th>' +
      '<th style="padding:10px 14px;text-align:center;font-size:11px;color:var(--muted);font-weight:600;text-transform:uppercase">Last Week</th>' +
      '<th style="padding:10px 14px;text-align:center;font-size:11px;color:var(--muted);font-weight:600;text-transform:uppercase">Change</th>' +
      "</tr></thead><tbody>" +
      compRow(
        "Total Cases",
        d.this_week.total,
        d.last_week.total,
        d.delta_total,
      ) +
      compRow("Resolved", d.this_week.done, d.last_week.done, d.delta_done) +
      compRow(
        "Missed",
        d.this_week.missed,
        d.last_week.missed,
        d.delta_missed,
      ) +
      compRow(
        "Resolution Rate",
        d.this_week.rate,
        d.last_week.rate,
        d.delta_rate,
        "%",
      ) +
      compRow(
        "Avg Response",
        d.this_week.avg_resp,
        d.last_week.avg_resp,
        d.delta_resp,
      ) +
      "</tbody></table></div>");
  } catch (e) {
    if (e.name === "AbortError") return;
    if (!el.children.length || el.querySelector(":scope > .loading")) updateHTML(el, errorContent(e));
  }
}

// ── Issue Search ──────────────────────────────────────────────────────────────
var issueSearchVtype = "";
function setIssueSearchVtype(vtype) {
  issueSearchVtype = vtype;
  document
    .querySelectorAll("#issue-search-vtype-tabs .toggle-btn")
    .forEach(function (b) {
      b.classList.toggle("active", b.dataset.vtype === vtype);
    });
  if (document.getElementById("issue-search-input").value.trim()) searchIssue();
}

async function searchIssue() {
  var q = document.getElementById("issue-search-input").value.trim();
  var vtype = issueSearchVtype;
  var el = document.getElementById("issue-search-results");
  if (!q) {
    updateHTML(el, "");
    return;
  }
  updateHTML(el, '<div class="loading">Searching...</div>');
  try {
    var r = await apiFetch(
      "/api/issue_search?q=" +
        encodeURIComponent(q) +
        (vtype ? "&vtype=" + encodeURIComponent(vtype) : ""),
    );
    var d = await r.json();
    if (!r.ok) {
      updateHTML(el, '<div class="empty-state">' + h(d.error || "Error") + "</div>");
      return;
    }
    if (!d.results.length) {
      updateHTML(el, '<div class="empty-state">No units found for "' + h(q) + '".</div>');
      return;
    }
    updateHTML(el, '<div style="font-size:11px;color:var(--muted);margin-bottom:8px">' +
      d.total_matches +
      " matching case(s) across " +
      d.results.length +
      " unit(s)</div>" +
      '<div class="table-wrap"><div class="table-scroll"><table>' +
      "<thead><tr><th>Unit #</th><th>Type</th><th>Matches</th><th>Sample Issue</th><th>Last Seen</th></tr></thead><tbody>" +
      d.results
        .map(function (u) {
          return (
            '<tr style="cursor:pointer" data-unit="' +
            attr(u.unit) +
            '" data-vtype="' +
            attr(u.vtype || "") +
            '" onclick="openUnitModal(this.dataset.unit, this.dataset.vtype)" title="View all cases for unit ' +
            attr(u.unit) +
            '">' +
            "<td><b>" +
            h(u.unit) +
            "</b></td>" +
            '<td><span style="background:var(--accent-bg);color:var(--accent);padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600">' +
            h(u.vtype) +
            "</span></td>" +
            '<td><b style="color:var(--accent)">' +
            h(u.count) +
            "</b></td>" +
            '<td style="color:var(--muted);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
            h(u.sample_issue) +
            "</td>" +
            '<td style="color:var(--muted);font-size:11px">' +
            h(u.last_seen) +
            "</td>" +
            "</tr>"
          );
        })
        .join("") +
      "</tbody></table></div></div>");
  } catch (e) {
    if (e.name === "AbortError") return;
    if (!el.children.length || el.querySelector(":scope > .loading")) updateHTML(el, errorContent(e));
  }
}

// ── Fleet Intelligence ────────────────────────────────────────────────────────
async function loadFleetIntel() {
  var el = document.getElementById("fleet-intel-content");
  if (!el.children.length || el.querySelector(":scope > .loading")) updateHTML(el, '<div class="loading">Loading fleet intelligence...</div>');
  try {
    var r = await apiFetch("/api/fleet_intelligence");
    var d = await r.json();
    window._intelData = d;

    var driversHtml =
      '<div class="table-wrap"><div class="table-scroll"><table>' +
      "<thead><tr><th>Driver</th><th>Reports</th><th>Most Common Issue</th></tr></thead><tbody>" +
      (d.top_drivers.length
        ? d.top_drivers
            .map(function (dr, i) {
              return (
                "<tr>" +
                '<td><span style="margin-right:6px">' +
                (i < 3 ? ["🥇", "🥈", "🥉"][i] : i + 1 + ".") +
                "</span><b>" +
                h(dr.name) +
                "</b></td>" +
                '<td><b style="color:var(--accent)">' +
                h(dr.total) +
                "</b></td>" +
                '<td style="color:var(--muted)">' +
                h(dr.top_issue) +
                "</td>" +
                "</tr>"
              );
            })
            .join("")
        : '<tr><td colspan="3" style="text-align:center;color:var(--muted);padding:20px">No data yet</td></tr>') +
      "</tbody></table></div></div>";

    updateHTML(el, '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px">' +
      '<div class="stat-card c-accent"><div class="stat-icon"><i class="ph ph-chart-bar"></i></div><div class="stat-label">Total Reports</div><div class="stat-value v-accent">' +
      d.total_reports +
      "</div></div>" +
      '<div class="stat-card c-blue"><div class="stat-icon"><i class="ph ph-hash"></i></div><div class="stat-label">Unique Units Tracked</div><div class="stat-value v-blue">' +
      d.total_units +
      "</div></div>" +
      "</div>" +
      '<div id="intel-units-wrap"></div>' +
      '<div id="intel-drivers-block"><div class="section-title" style="margin:16px 0 10px">Most Reported Drivers</div>' + driversHtml + '</div>');
    renderIntelUnits(intelFilter.vtype, intelFilter.search);
  } catch (e) {
    if (e.name === "AbortError") return;
    if (!el.children.length || el.querySelector(":scope > .loading")) updateHTML(el, errorContent(e));
  }
}

function intelUniversalSearch(value) {
  var q = (value || "").trim();
  intelFilter.search = q;
  renderIntelUnits(intelFilter.vtype, q);
  var recurring = document.getElementById("recurring-problems-content");
  if (recurring) {
    Array.prototype.forEach.call(recurring.children, function (row) {
      row.style.display = !q || row.textContent.toLowerCase().indexOf(q.toLowerCase()) !== -1 ? "" : "none";
    });
  }
  var drivers = document.getElementById("intel-drivers-block");
  if (drivers) {
    var rows = drivers.querySelectorAll("tbody tr");
    var visible = 0;
    rows.forEach(function (row) {
      var show = !q || row.textContent.toLowerCase().indexOf(q.toLowerCase()) !== -1;
      row.style.display = show ? "" : "none";
      if (show) visible++;
    });
    drivers.style.display = !q || visible ? "" : "none";
  }
  if (q.length >= 2) searchIssue();
  else updateHTML(document.getElementById("issue-search-results"), "");
}

var intelFilter = { vtype: "all", search: "" };
function renderIntelUnits(vtype, search) {
  intelFilter = { vtype: vtype, search: search };
  preserveInput("intel-units-search", function () {
    renderIntelUnitsContent(vtype, search);
  });
}
function renderIntelUnitsContent(vtype, search) {
  var wrap = document.getElementById("intel-units-wrap");
  if (!wrap || !window._intelData) return;
  var items = window._intelData.top_units || [];
  var q = (search || "").toLowerCase().trim();
  var filtered = items.filter(function (u) {
    if (vtype !== "all" && (u.vtype || "").toLowerCase() !== vtype)
      return false;
    if (
      q &&
      (u.unit || "").toLowerCase().indexOf(q) === -1 &&
      (u.top_issue || "").toLowerCase().indexOf(q) === -1
    )
      return false;
    return true;
  });
  function vbtn(v, label) {
    return (
      '<button class="toggle-btn' +
      (vtype === v ? " active" : "") +
      '" onclick="renderIntelUnits(\'' +
      v +
      "', (document.getElementById('issue-search-input')||{value:''}).value)\">" +
      label +
      "</button>"
    );
  }
  var rows = filtered
    .map(function (u) {
      return (
        '<tr style="cursor:pointer" data-unit="' +
        attr(u.unit) +
        '" data-vtype="' +
        attr(u.vtype || "") +
        '" onclick="openUnitModal(this.dataset.unit, this.dataset.vtype)" title="View all cases for unit ' +
        attr(u.unit) +
        '">' +
        "<td><b>" +
        h(u.unit) +
        "</b></td>" +
        '<td><span style="background:var(--accent-bg);color:var(--accent);padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600">' +
        h(u.vtype) +
        "</span></td>" +
        '<td><b style="color:var(--accent)">' +
        h(u.total) +
        "</b></td>" +
        '<td style="color:var(--muted);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
        h(u.top_issue) +
        "</td>" +
        '<td style="color:var(--muted);font-size:11px">' +
        h(u.last_seen) +
        "</td>" +
        "</tr>"
      );
    })
    .join("");
  updateHTML(wrap, '<div class="section-title" style="margin-bottom:10px">Most reported units · top 20</div>' +
    '<div class="toggle-tabs" style="margin-bottom:10px">' +
    vbtn("all", "All") +
    vbtn("truck", "Truck") +
    vbtn("trailer", "Trailer") +
    vbtn("reefer", "Reefer") +
    "</div>" +
    '<div class="search-wrap" style="margin-bottom:12px"><i class="ph ph-magnifying-glass"></i><input type="text" id="intel-units-search" aria-label="Search intelligence units or issues" placeholder="Search unit or issue..." value="' +
    attr(search || "") +
    '" oninput="renderIntelUnits(\'' +
    vtype +
    "', this.value)\"></div>" +
    '<div class="table-wrap"><div class="table-scroll"><table>' +
    "<thead><tr><th>Unit #</th><th>Type</th><th>Reports</th><th>Top Issue</th><th>Last Seen</th></tr></thead><tbody>" +
    (filtered.length
      ? rows
      : '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:20px">No units match this filter</td></tr>') +
    "</tbody></table></div></div>");
}
