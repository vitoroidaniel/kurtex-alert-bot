// Quietly poll the visible page; keep controls and open dialogs in place.
var refreshing = false;
var refreshQueued = false;
async function refresh(force) {
  if (refreshing) { if (force) refreshQueued = true; return; }
  if (!force && (document.hidden || activeRequests.size)) return;
  refreshing = true;
  var button = document.getElementById("refresh-button");
  button.disabled = true;
  try {
    var tasks = [loadStats()];
    if (currentPage === "overview") tasks.push(loadRecent());
    else if (currentPage === "cases") tasks.push(loadCases(false, true));
    else if (currentPage === "missed") tasks.push(loadMissed(false, true));
    else if (currentPage === "fleet") tasks.push(loadFleet());
    else if (currentPage === "trends") tasks.push(loadTrends());
    else if (currentPage === "comparison") tasks.push(loadComparison());
    else if (currentPage === "fleet_intel") tasks.push(loadFleetIntel());
    else if (currentPage === "my_profile") tasks.push(loadMyProfile());
    else if (currentPage === "agents") tasks.push(loadAgents());
    await Promise.allSettled(tasks);
  } finally {
    refreshing = false;
    button.disabled = false;
    if (refreshQueued) { refreshQueued = false; refresh(true); }
  }
}
async function loadRecent() {
  var el = document.getElementById("recent-table");
  try {
    var r = await apiFetch("/api/cases?filter=today&limit=10", "recent");
    var d = await r.json();
    updateHTML(el, caseTable(d.cases));
  } catch (e) {
    if (e.name !== "AbortError" && !el.querySelector("table"))
      if (!el.children.length || el.querySelector(":scope > .loading")) updateHTML(el, errorContent(e));
  }
}
function autoRefresh() {
  return refresh(false);
}
document.addEventListener("keydown", function (e) {
  if (
    (e.key === "Enter" || e.key === " ") &&
    e.target.matches('[role="button"]')
  ) {
    e.preventDefault();
    e.target.click();
  }
  var overlays = Array.from(
    document.querySelectorAll(".modal-overlay.open,.report-modal-overlay.open"),
  );
  overlays.sort(function (a, b) {
    return (
      Number(getComputedStyle(a).zIndex) - Number(getComputedStyle(b).zIndex)
    );
  });
  var top = overlays[overlays.length - 1];
  if (e.key === "Escape") {
    if (top) top.querySelector(".modal-close,.report-close").click();
    else closeSidebar();
  }
  if (e.key === "Tab" && top) {
    var nodes = Array.from(
      top.querySelectorAll("button,a[href],input,select"),
    ).filter(function (el) {
      return el.offsetParent !== null;
    });
    var first = nodes[0],
      last = nodes[nodes.length - 1];
    if (
      !top.contains(document.activeElement) ||
      (!e.shiftKey && document.activeElement === last)
    ) {
      e.preventDefault();
      if (first) first.focus();
    } else if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      if (last) last.focus();
    }
  }
});
function updateChicagoClock() {
  var now = new Date();
  var options = {timeZone: "America/Chicago"};
  function setClockText(id, value) {
    var el = document.getElementById(id);
    if (el.textContent !== value) el.textContent = value;
  }
  setClockText("chicago-month", now.toLocaleDateString("en-US", {...options, month:"short"}));
  setClockText("chicago-day", now.toLocaleDateString("en-US", {...options, day:"numeric"}));
  setClockText("chicago-date", now.toLocaleDateString("en-US", {...options, weekday:"long", month:"short", day:"numeric"}));
  setClockText("chicago-time", now.toLocaleTimeString("en-US", {...options, hour:"numeric", minute:"2-digit", timeZoneName:"short"}));
  document.getElementById("today-label").dateTime = now.toISOString();
}

updateChicagoClock();
setInterval(updateChicagoClock, 1000);
showPage(preferences.get("kurtex-page") || "overview");
setInterval(autoRefresh, 15000);
document.addEventListener("visibilitychange", function () {
  if (!document.hidden) autoRefresh();
});
window.addEventListener("online", autoRefresh);

// v13: mirror the existing connection timestamp into the mobile status row.
(function(){
  function syncMobileUpdate(){
    const source=document.getElementById('last-update');
    const target=document.querySelector('.mobile-last-update');
    if(source&&target){
      const text=(source.textContent||'').trim();
      target.textContent=text || 'Updated just now';
    }
  }
  document.addEventListener('DOMContentLoaded',function(){
    syncMobileUpdate();
    const source=document.getElementById('last-update');
    if(source&&window.MutationObserver){new MutationObserver(syncMobileUpdate).observe(source,{childList:true,subtree:true,characterData:true});}
  });
})();
