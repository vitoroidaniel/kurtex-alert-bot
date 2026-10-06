function toggleGroup(id) {
  var el=document.getElementById(id), caret=document.getElementById("caret-"+id), open=el.classList.contains("open");
  el.classList.toggle("open",!open); if(caret) caret.classList.toggle("open",!open);
}
var trendPeriod=7;
function setTrendPeriod(days,btn){trendPeriod=days;document.querySelectorAll("#page-trends .toggle-btn").forEach(b=>b.classList.remove("active"));if(btn)btn.classList.add("active");loadTrends()}
function deltaText(v){if(v===null||v===undefined)return "No previous data";return (v>0?"↑ ":v<0?"↓ ":"— ")+Math.abs(v)+"% vs previous period"}
function kpi(label,value,delta){return '<div class="card mgmt-kpi"><small>'+h(label)+'</small><strong>'+h(value)+'</strong><span class="delta">'+h(deltaText(delta))+'</span></div>'}
function rankRows(rows){return '<div class="analytics-rank">'+(rows||[]).map(function(x){return '<div class="analytics-rank-row"><div><b>'+h(x.label)+'</b><small>'+h(x.detail||'')+'</small></div><strong>'+h(x.value)+'</strong></div>'}).join('')+'</div>'}
async function loadTrends(){var el=document.getElementById('fleet-analytics-content');if(!el)return;updateHTML(el,'<div class="loading">Calculating fleet analytics...</div>');try{var r=await apiFetch('/api/trends?period='+trendPeriod),d=await r.json();if(!r.ok)throw new Error(d.error||'Unable to load analytics');var m=d.metrics||{};updateHTML(el,
'<div class="mgmt-kpis">'+kpi('Total cases',m.total,m.delta_total)+kpi('Resolved',m.resolved,m.delta_resolved)+kpi('Open',m.open,m.delta_open)+kpi('Incomplete',m.incomplete,m.delta_incomplete)+kpi('Affected units',m.units,m.delta_units)+kpi('Recurring units',m.recurring_units,m.delta_recurring)+kpi('Repeat problems',m.repeat_problems,m.delta_repeat)+kpi('Reports today',m.today,null)+'</div>'+ 
'<div class="card ai-brief-card"><div class="ai-brief-head"><div class="card-title"><i class="ph ph-sparkle"></i> AI Fleet Brief</div><small>Based on '+h(d.sample_size)+' cases · live data</small></div><div class="ai-brief-list">'+(d.insights||[]).map(x=>'<div class="ai-brief-item">'+h(x)+'</div>').join('')+'</div></div>'+ 
'<div class="analytics-two"><div class="card"><div class="card-title"><i class="ph ph-warning-circle"></i> Problem Categories</div>'+rankRows(d.categories)+'</div><div class="card"><div class="card-title"><i class="ph ph-repeat"></i> Units to Watch</div>'+rankRows(d.units_to_watch)+'</div></div>');}catch(e){updateHTML(el,errorContent(e))}}
async function loadComparison(){var el=document.getElementById('comparison-content');if(!el)return;var sel=document.getElementById('compare-days'),days=sel?sel.value:30;updateHTML(el,'<div class="loading">Building comparison...</div>');try{var r=await apiFetch('/api/comparison?period='+days),d=await r.json();if(!r.ok)throw new Error(d.error||'Unable to compare');var cards=(d.rows||[]).map(function(x){return '<div class="card compare-card"><small>'+h(x.label)+'</small><div class="compare-values"><strong>'+h(x.current)+'</strong><span>Previous '+h(x.previous)+'</span></div><div class="delta">'+h(deltaText(x.delta))+'</div></div>'}).join('');updateHTML(el,'<div class="compare-grid">'+cards+'</div><div class="analytics-two"><div class="card"><div class="card-title"><i class="ph ph-chart-bar"></i> Category Change</div>'+rankRows(d.category_change)+'</div><div class="card"><div class="card-title"><i class="ph ph-sparkle"></i> Management Summary</div><div class="ai-brief-list">'+(d.insights||[]).map(x=>'<div class="ai-brief-item">'+h(x)+'</div>').join('')+'</div></div></div>');}catch(e){updateHTML(el,errorContent(e))}}

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
  else renderMobileIssueDefault();
}

async function searchIssue() {
  var q = document.getElementById("issue-search-input").value.trim();
  var vtype = issueSearchVtype;
  var el = document.getElementById("issue-search-results");
  if (!q) {
    renderMobileIssueDefault();
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
    var mobileCards='<div class="mobile-search-result-count">'+d.total_matches+' matching case(s) across '+d.results.length+' unit(s)</div><div class="mobile-issue-list">'+d.results.map(function(u){return issueUnitCard(u,'count','sample_issue')}).join('')+'</div>';
    updateHTML(el, mobileCards + '<div class="desktop-issue-results"><div style="font-size:11px;color:var(--muted);margin-bottom:8px">' +
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
      "</tbody></table></div></div></div>");
  } catch (e) {
    if (e.name === "AbortError") return;
    if (!el.children.length || el.querySelector(":scope > .loading")) updateHTML(el, errorContent(e));
  }
}

function issueUnitCard(u, countKey, issueKey) {
  var type=(u.vtype||'unit').toLowerCase();
  var icon=type==='truck'?'ph-truck':type==='trailer'?'ph-truck-trailer':type==='reefer'?'ph-snowflake':'ph-wrench';
  return '<button type="button" class="mobile-issue-unit" data-unit="'+attr(u.unit)+'" data-vtype="'+attr(u.vtype||'')+'" onclick="openUnitModal(this.dataset.unit,this.dataset.vtype)">'+
    '<span class="mobile-issue-icon"><i class="ph '+icon+'"></i></span><span class="mobile-issue-copy"><span><strong>'+h(u.unit)+'</strong><em class="mobile-unit-type '+attr(type)+'">'+h(u.vtype||'Unit')+'</em></span><small>'+h(u[issueKey]||'No issue description')+'</small><small class="mobile-issue-meta">'+h(u[countKey]||0)+' report'+(Number(u[countKey]||0)===1?'':'s')+(u.last_seen?' · '+h(u.last_seen):'')+'</small></span><i class="ph ph-caret-right"></i></button>';
}
function renderMobileIssueDefault(){
  if(!window.matchMedia('(max-width:700px)').matches)return;
  var el=document.getElementById('issue-search-results');if(!el)return;
  var q=(document.getElementById('issue-search-input')||{}).value||'';if(q.trim())return;
  var rows=((window._intelData||{}).top_units||[]).filter(function(u){return !issueSearchVtype || (u.vtype||'').toLowerCase()===issueSearchVtype}).slice(0,10);
  if(!rows.length){updateHTML(el,'<div class="mobile-search-empty"><i class="ph ph-magnifying-glass"></i><strong>Fleet search</strong><span>Search a unit, issue, part, code, driver or case history.</span></div>');return;}
  updateHTML(el,'<div class="mobile-search-default-head"><span>Most reported units</span><small>Tap a unit to view case history</small></div><div class="mobile-issue-list">'+rows.map(function(u){return issueUnitCard(u,'total','top_issue')}).join('')+'</div>');
}

// ── Fleet Intelligence ────────────────────────────────────────────────────────
async function loadFleetIntel() {
  var el = document.getElementById("fleet-intel-content");
  if (!el.children.length || el.querySelector(":scope > .loading")) updateHTML(el, '<div class="loading">Loading fleet intelligence...</div>');
  try {
    var r = await apiFetch("/api/fleet_intelligence");
    var d = await r.json();
    window._intelData = d;
    renderMobileIssueDefault();

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

    var summary = document.getElementById("intel-summary");
    if (summary) updateHTML(summary,
      '<div class="stat-card c-accent"><div class="stat-icon"><i class="ph ph-chart-bar"></i></div><div class="stat-label">Total Reports</div><div class="stat-value v-accent">' + d.total_reports + '</div></div>' +
      '<div class="stat-card c-blue"><div class="stat-icon"><i class="ph ph-hash"></i></div><div class="stat-label">Unique Units Tracked</div><div class="stat-value v-blue">' + d.total_units + '</div></div>'
    );
    updateHTML(el, '<div id="intel-units-wrap"></div>');
    renderIntelUnits(intelFilter.vtype, intelFilter.search);
  } catch (e) {
    if (e.name === "AbortError") return;
    if (!el.children.length || el.querySelector(":scope > .loading")) updateHTML(el, errorContent(e));
  }
}

var intelLiveSearchTimer = null;
function intelUniversalSearch(value) {
  var q = (value || "").trim();
  intelFilter.search = q;
  var clearBtn = document.getElementById("issue-search-clear");
  if (clearBtn) clearBtn.hidden = !q;

  renderIntelUnits(intelFilter.vtype, q);
  var recurring = document.getElementById("recurring-problems-content");
  if (recurring) {
    Array.prototype.forEach.call(recurring.children, function (row) {
      var show = !q || row.textContent.toLowerCase().indexOf(q.toLowerCase()) !== -1;
      row.style.display = show ? "" : "none";
      if (show && q && row.tagName === "DETAILS") row.open = true;
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

  if (intelLiveSearchTimer) clearTimeout(intelLiveSearchTimer);
  if (q.length >= 2) {
    var results = document.getElementById("issue-search-results");
    if (results && window.matchMedia('(max-width:700px)').matches) {
      updateHTML(results, '<div class="loading">Searching...</div>');
    }
    intelLiveSearchTimer = setTimeout(function () { searchIssue(); }, 280);
  } else {
    renderMobileIssueDefault();
  }
}

function clearIntelLiveSearch() {
  if (intelLiveSearchTimer) clearTimeout(intelLiveSearchTimer);
  var input = document.getElementById("issue-search-input");
  if (input) { input.value = ""; input.focus(); }
  var clearBtn = document.getElementById("issue-search-clear");
  if (clearBtn) clearBtn.hidden = true;
  intelFilter.search = "";
  renderIntelUnits(intelFilter.vtype, "");
  renderMobileIssueDefault();
}

var intelFilter = { vtype: "all", search: "", page: 1 };
var intelUnitsPerPage = 10;
function renderIntelUnits(vtype, search, page) {
  var changedFilter=intelFilter.vtype!==vtype || intelFilter.search!==(search||"");
  intelFilter = { vtype: vtype, search: search, page: changedFilter ? 1 : (page || intelFilter.page || 1) };
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
  var totalItems=filtered.length;
  var totalPages=Math.max(1,Math.ceil(totalItems/intelUnitsPerPage));
  var page=Math.max(1,Math.min(totalPages,intelFilter.page||1));
  intelFilter.page=page;
  var startIndex=(page-1)*intelUnitsPerPage;
  var visible=filtered.slice(startIndex,startIndex+intelUnitsPerPage);
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
  var rows = visible
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
  updateHTML(wrap, '<div class="intel-units-head"><div><div class="section-title">Most Reported Units</div><div class="intel-units-subtitle">Units with the highest maintenance report volume. Select a unit to open its full case history.</div></div><div class="intel-units-total"><b>'+filtered.length+'</b><span>units shown</span></div></div>' +
    '<div class="toggle-tabs" style="margin-bottom:10px">' +
    vbtn("all", "All") +
    vbtn("truck", "Truck") +
    vbtn("trailer", "Trailer") +
    vbtn("reefer", "Reefer") +
    "</div>" +
    '<div class="table-wrap"><div class="table-scroll"><table>' +
    "<thead><tr><th>Unit #</th><th>Type</th><th>Reports</th><th>Top Issue</th><th>Last Seen</th></tr></thead><tbody>" +
    (totalItems
      ? rows
      : '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:20px">No units match this filter</td></tr>') +
    "</tbody></table></div></div>" +
    (totalItems ? '<div class="fleet-list-footer fleet-pager intel-units-pager"><span>Showing '+(startIndex+1)+'–'+Math.min(startIndex+visible.length,totalItems)+' of '+totalItems+'</span><div class="fleet-pager-controls"><button class="btn secondary pager-btn" '+(page<=1?'disabled':'')+' onclick="renderIntelUnits(intelFilter.vtype,intelFilter.search,'+(page-1)+')"><i class="ph ph-arrow-left"></i> Previous</button><span class="pager-page">'+page+' / '+totalPages+'</span><button class="btn secondary pager-btn" '+(page>=totalPages?'disabled':'')+' onclick="renderIntelUnits(intelFilter.vtype,intelFilter.search,'+(page+1)+')">Next <i class="ph ph-arrow-right"></i></button></div></div>' : ''));
}
