// Poll the visible page; pause while hidden, typing, or viewing a dialog.
var refreshing = false;
async function refresh(force) {
  if (refreshing && !force) return;
  if (
    !force &&
    (document.hidden ||
      anyModalOpen() ||
      activeRequests.size ||
      document.activeElement.matches("input,select,textarea"))
  )
    return;
  refreshing = true;
  var button = document.getElementById("refresh-button");
  button.disabled = true;
  try {
    var tasks = [loadStats()];
    if (currentPage === "overview") tasks.push(loadRecent());
    else if (currentPage === "cases") tasks.push(loadCases(false, !force));
    else if (currentPage === "missed") tasks.push(loadMissed(false, !force));
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
  }
}
async function loadRecent() {
  var el = document.getElementById("recent-table");
  try {
    var r = await apiFetch("/api/cases?filter=today&limit=10", "recent");
    var d = await r.json();
    el.innerHTML = caseTable(d.cases);
  } catch (e) {
    if (e.name !== "AbortError" && !el.querySelector("table"))
      el.innerHTML = errorContent(e);
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
document.getElementById("today-label").textContent =
  new Date().toLocaleDateString("en-US", {
    timeZone: "America/Chicago",
    weekday: "long",
    month: "short",
    day: "numeric",
  }) + " · Chicago time";
showPage(preferences.get("kurtex-page") || "overview");
setInterval(autoRefresh, 30000);
document.addEventListener("visibilitychange", function () {
  if (!document.hidden) autoRefresh();
});
window.addEventListener("online", autoRefresh);
