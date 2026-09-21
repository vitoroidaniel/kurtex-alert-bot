function openReport() {
  document.getElementById("report-modal-overlay").classList.add("open");
  lockBodyScroll();
  reportTab = "today";
  document.querySelectorAll(".report-tab").forEach(function (b, i) {
    b.classList.toggle("active", i === 0);
  });
  document.getElementById("report-period-bar").style.display = "none";
  document.getElementById("report-sections-bar").style.display = "none";
  generateReport();
}
function closeReport() {
  var overlay = document.getElementById("report-modal-overlay");
  if (overlay.classList.contains("open")) {
    overlay.classList.remove("open");
    unlockBodyScroll();
  }
}
function setReportTab(tab, btn) {
  reportTab = tab;
  document.querySelectorAll(".report-tab").forEach(function (b) {
    b.classList.remove("active");
  });
  btn.classList.add("active");
  document.getElementById("report-period-bar").style.display =
    tab === "custom" ? "flex" : "none";
  if (tab === "today") generateReport();
}
function toggleCustomDates() {
  var v = document.getElementById("report-period-select").value;
  document.getElementById("custom-date-inputs").style.display =
    v === "custom" ? "flex" : "none";
}
async function generateReport() {
  document.getElementById("report-content").innerHTML =
    '<div class="loading">Generating report...</div>';
  document.getElementById("report-sections-bar").style.display = "none";
  var url = "/api/report?period=today";
  if (reportTab === "custom") {
    var period = document.getElementById("report-period-select").value;
    if (period === "custom") {
      var from = document.getElementById("report-date-from").value;
      var to = document.getElementById("report-date-to").value;
      if (!from) {
        document.getElementById("report-content").innerHTML =
          '<div class="loading">Please select a start date.</div>';
        return;
      }
      if (to && from > to) {
        document.getElementById("report-content").innerHTML =
          '<div class="empty-state">End date must be on or after the start date.</div>';
        return;
      }
      url = "/api/report?period=custom&from=" + from + "&to=" + (to || from);
    } else {
      url = "/api/report?period=" + period;
    }
  }
  try {
    var r = await apiFetch(url);
    if (!r.ok) {
      document.getElementById("report-content").innerHTML =
        '<div class="loading">Error generating report.</div>';
      return;
    }
    window._reportData = await r.json();
    document.getElementById("report-sections-bar").style.display = "flex";
    renderReportContent();
  } catch (e) {
    if (e.name === "AbortError") return;
    document.getElementById("report-content").innerHTML = errorContent(e);
  }
}

function reportSectionEnabled(name) {
  var cb = document.querySelector(
    '.report-section-cb[data-section="' + name + '"]',
  );
  return !cb || cb.checked;
}

function renderReportContent() {
  var d = window._reportData;
  if (!d) return;
  document.getElementById("report-ts").textContent =
    "Generated " +
    new Date().toLocaleString("en-US", { timeZone: "America/Chicago" }) +
    " CT";
  var now = new Date();
  var dateStr = now.toLocaleDateString("en-US", {
    timeZone: "America/Chicago",
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
  });
  var timeStr =
    now.toLocaleTimeString("en-US", {
      timeZone: "America/Chicago",
      hour: "2-digit",
      minute: "2-digit",
    }) + " CT";
  var resRate = d.rate || 0;
  var vtypeLabels = { truck: "Trucks", trailer: "Trailers", reefer: "Reefers" };
  var vtypeIcons = { truck: "🚚", trailer: "📦", reefer: "❄️" };
  var html = "";

  // Official letterhead — always shown
  html +=
    '<div style="text-align:center;border-bottom:3px double var(--accent);padding-bottom:16px;margin-bottom:22px">' +
    '<div style="font-size:25px;font-weight:900;letter-spacing:.06em;color:var(--text)">KURTEX MAINTENANCE</div>' +
    '<div style="font-size:12px;font-weight:600;letter-spacing:.1em;color:var(--accent);text-transform:uppercase;margin-top:3px">Official Fleet Operations Report</div>' +
    '<div style="display:flex;justify-content:center;gap:28px;margin-top:16px;flex-wrap:wrap">' +
    '<div><div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em">Report Period</div><div style="font-size:14px;font-weight:700">' +
    h(d.label) +
    "</div></div>" +
    '<div><div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em">Generated</div><div style="font-size:14px;font-weight:700">' +
    dateStr +
    '</div><div style="font-size:11px;color:var(--muted)">' +
    timeStr +
    "</div></div>" +
    "</div></div>";

  if (reportSectionEnabled("summary")) {
    html +=
      '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px">' +
      '<div style="background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center">' +
      '<div style="font-size:28px;font-weight:800;color:var(--accent)">' +
      d.total +
      "</div>" +
      '<div style="font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-top:3px">Total Alerts</div>' +
      "</div>" +
      '<div style="background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center">' +
      '<div style="font-size:28px;font-weight:800;color:var(--green)">' +
      d.done +
      "</div>" +
      '<div style="font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-top:3px">Resolved</div>' +
      "</div>" +
      '<div style="background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center">' +
      '<div style="font-size:28px;font-weight:800;color:var(--red)">' +
      d.missed +
      "</div>" +
      '<div style="font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-top:3px">Missed</div>' +
      "</div>" +
      "</div>" +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:20px">' +
      '<div style="background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:12px 16px;display:flex;align-items:center;justify-content:space-between">' +
      '<span style="font-size:12px;color:var(--muted);font-weight:500">Resolution Rate</span>' +
      '<span style="font-size:18px;font-weight:800;color:' +
      (resRate >= 80
        ? "var(--green)"
        : resRate >= 60
          ? "var(--yellow)"
          : "var(--red)") +
      '">' +
      resRate +
      "%</span>" +
      "</div>" +
      '<div style="background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:12px 16px;display:flex;align-items:center;justify-content:space-between">' +
      '<span style="font-size:12px;color:var(--muted);font-weight:500">Avg Response Time</span>' +
      '<span style="font-size:18px;font-weight:800;color:var(--text)">' +
      d.avg_resp +
      "</span>" +
      "</div>" +
      "</div>";
  }

  if (reportSectionEnabled("agents") && d.leaderboard.length) {
    html +=
      '<div style="margin-bottom:20px">' +
      '<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--border)">Agent Performance</div>' +
      '<table style="width:100%;border-collapse:collapse;font-size:13px">' +
      "<thead><tr>" +
      '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">#</th>' +
      '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Agent</th>' +
      '<th style="text-align:right;padding:6px 8px;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Cases</th>' +
      "</tr></thead><tbody>" +
      d.leaderboard
        .map(function (a, i) {
          return (
            '<tr style="border-top:1px solid var(--border)">' +
            '<td style="padding:8px;font-weight:700;color:var(--muted);width:30px">' +
            (i + 1) +
            ".</td>" +
            '<td style="padding:8px;font-weight:500">' +
            (medals[i] ? medals[i] + " " : "") +
            h(a.name) +
            "</td>" +
            '<td style="padding:8px;text-align:right;font-weight:700;color:var(--accent)">' +
            h(a.count) +
            "</td>" +
            "</tr>"
          );
        })
        .join("") +
      "</tbody></table></div>";
  }

  if (reportSectionEnabled("groups") && d.top_groups.length) {
    html +=
      '<div style="margin-bottom:20px">' +
      '<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--border)">Most Active Groups</div>' +
      d.top_groups
        .map(function (g, i) {
          var maxCount = d.top_groups[0].count;
          var pct = Math.round((g.count / maxCount) * 100);
          return (
            '<div style="display:flex;align-items:center;gap:10px;padding:6px 0;border-top:1px solid var(--border)">' +
            '<span style="font-size:12px;font-weight:500;width:180px;flex-shrink:0">' +
            h(g.name) +
            "</span>" +
            '<div style="flex:1;height:5px;background:var(--surface3);border-radius:3px">' +
            '<div style="height:100%;border-radius:3px;background:var(--accent);width:' +
            pct +
            '%"></div></div>' +
            '<span style="font-size:12px;font-weight:700;color:var(--accent);width:30px;text-align:right">' +
            h(g.count) +
            "</span>" +
            "</div>"
          );
        })
        .join("") +
      "</div>";
  }

  if (reportSectionEnabled("vtype") && d.by_vtype) {
    var vtypeBlocks = ["truck", "trailer", "reefer"]
      .map(function (vt) {
        var vd = d.by_vtype[vt];
        if (!vd || !vd.total) return "";
        return (
          '<div style="margin-bottom:14px">' +
          '<div style="font-size:12px;font-weight:700;margin-bottom:6px">' +
          vtypeIcons[vt] +
          " " +
          vtypeLabels[vt] +
          ' <span style="color:var(--muted);font-weight:500">(' +
          vd.total +
          " reports)</span></div>" +
          (vd.top_issues.length
            ? vd.top_issues
                .map(function (x) {
                  return (
                    '<div style="display:flex;justify-content:space-between;gap:8px;padding:4px 0 4px 20px;border-top:1px solid var(--border);font-size:12px">' +
                    "<span>" +
                    h(x.issue) +
                    '</span><span style="font-weight:700;color:var(--accent);flex-shrink:0">' +
                    x.count +
                    "x</span></div>"
                  );
                })
                .join("")
            : '<div style="padding-left:20px;color:var(--muted);font-size:12px">No issues logged</div>') +
          "</div>"
        );
      })
      .join("");
    html +=
      '<div style="margin-bottom:20px">' +
      '<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--border)">Top Issues by Vehicle Type</div>' +
      (vtypeBlocks ||
        '<div style="color:var(--muted);font-size:12px">No vehicle-type data for this period</div>') +
      "</div>";
  }

  if (reportSectionEnabled("units") && d.top_units && d.top_units.length) {
    html +=
      '<div style="margin-bottom:20px">' +
      '<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--border)">Top Problem Units</div>' +
      '<table style="width:100%;border-collapse:collapse;font-size:12px">' +
      "<thead><tr>" +
      '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Unit</th>' +
      '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Type</th>' +
      '<th style="text-align:right;padding:6px 8px;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Reports</th>' +
      "</tr></thead><tbody>" +
      d.top_units
        .map(function (u) {
          return (
            '<tr style="border-top:1px solid var(--border)">' +
            '<td style="padding:7px 8px;font-weight:700">' +
            h(u.unit) +
            "</td>" +
            '<td style="padding:7px 8px;color:var(--muted);text-transform:capitalize">' +
            h(u.vtype || "—") +
            "</td>" +
            '<td style="padding:7px 8px;text-align:right;font-weight:700;color:var(--accent)">' +
            h(u.count) +
            "</td>" +
            "</tr>"
          );
        })
        .join("") +
      "</tbody></table></div>";
  }

  if (reportSectionEnabled("missed") && d.missed_cases.length) {
    html +=
      '<div style="margin-bottom:8px">' +
      '<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--red);margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--border)">Unresolved Alerts (' +
      d.missed +
      ")</div>" +
      '<table style="width:100%;border-collapse:collapse;font-size:12px">' +
      "<thead><tr>" +
      '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Driver</th>' +
      '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Group</th>' +
      '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Time</th>' +
      "</tr></thead><tbody>" +
      d.missed_cases
        .map(function (c) {
          return (
            '<tr style="border-top:1px solid var(--border)">' +
            '<td style="padding:7px 8px;font-weight:500">' +
            h(c.driver) +
            "</td>" +
            '<td style="padding:7px 8px;color:var(--muted)">' +
            h(c.group) +
            "</td>" +
            '<td style="padding:7px 8px;color:var(--muted);font-size:11px">' +
            h(c.opened) +
            "</td>" +
            "</tr>"
          );
        })
        .join("") +
      "</tbody></table></div>";
  }

  html +=
    '<div style="margin-top:24px;padding-top:12px;border-top:1px solid var(--border);text-align:center;font-size:10px;color:var(--muted)">This is an official Kurtex Maintenance report, generated automatically from live fleet data.</div>';

  document.getElementById("report-content").innerHTML = html;
}
function printReport() {
  var orig = document.title;
  document.title =
    "Kurtex Maintenance Report — " +
    new Date().toLocaleDateString("en-US", { timeZone: "America/Chicago" });
  document.body.classList.add("printing-report");
  function cleanup() {
    document.title = orig;
    document.body.classList.remove("printing-report");
    window.removeEventListener("afterprint", cleanup);
  }
  window.addEventListener("afterprint", cleanup);
  window.print();
  // Fallback in case afterprint doesn't fire (some mobile browsers)
  cleanup();
}

// ── Refresh ────────────────────────────────────────────────────────────────

// ── Nav Groups ──────────────────────────────────────────────────────────────
