// Shared transport: bounded requests, newest-response wins, and visible errors.
var activeRequests = new Map();
var requestProblems = new Map();
var lastSuccessfulRead = null;
var preferences = {
  get: function (key) {
    try {
      return localStorage.getItem(key);
    } catch (_) {
      return null;
    }
  },
  set: function (key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (_) {}
  },
};
function connectionState() {
  var message = requestProblems.values().next().value;
  var banner = document.getElementById("connection-banner");
  if (!banner) return;
  banner.hidden = !message;
  document.getElementById("connection-message").textContent = message || "";
  document
    .getElementById("connection-status")
    .classList.toggle("is-stale", !!message);
  document.getElementById("last-update").textContent = message
    ? "Refresh needed"
    : lastSuccessfulRead
      ? "Updated " +
        lastSuccessfulRead.toLocaleTimeString("en-US", {
          timeZone: "America/Chicago",
          hour: "2-digit",
          minute: "2-digit",
        }) +
        " CT"
      : "Connecting…";
}
async function apiFetch(url, key) {
  key = key || url.split("?")[0];
  if (activeRequests.has(key)) activeRequests.get(key).abort();
  var controller = new AbortController(),
    timedOut = false;
  activeRequests.set(key, controller);
  var timer = setTimeout(function () {
    timedOut = true;
    controller.abort();
  }, 12000);
  try {
    var response = await fetch(url, {
      signal: controller.signal,
      cache: "no-store",
      credentials: "same-origin",
    });
    if (response.status === 401) {
      window.location.assign("/login");
      throw new Error("Please sign in again.");
    }
    var data = await response.json();
    if (activeRequests.get(key) !== controller)
      throw new DOMException("Superseded", "AbortError");
    if (!response.ok)
      throw new Error(
        data.error || "Could not load dashboard data. Please retry.",
      );
    if (response.headers.get("X-Case-Data-Stale") === "true") {
      requestProblems.set(
        key,
        "Case storage is unavailable. Showing the last valid snapshot; it may be out of date.",
      );
    } else {
      requestProblems.delete(key);
      lastSuccessfulRead = new Date();
    }
    connectionState();
    return {
      ok: true,
      status: response.status,
      json: async function () {
        return data;
      },
    };
  } catch (e) {
    if (e.name === "AbortError" && !timedOut) throw e;
    var message = timedOut
      ? "Request timed out. Please retry."
      : e instanceof TypeError
        ? "Connection lost. Check your connection and retry."
        : e.message;
    requestProblems.set(key, message);
    connectionState();
    throw new Error(message);
  } finally {
    clearTimeout(timer);
    if (activeRequests.get(key) === controller) activeRequests.delete(key);
  }
}
function errorContent(e) {
  return (
    '<div class="empty-state error-state">' +
    h(e.message || "Unable to load data.") +
    "</div>"
  );
}
function preserveInput(id, render) {
  var input = document.getElementById(id),
    focused = input && document.activeElement === input;
  var start = focused ? input.selectionStart : 0,
    end = focused ? input.selectionEnd : 0;
  render();
  var next = document.getElementById(id);
  if (focused && next) {
    next.focus({ preventScroll: true });
    next.setSelectionRange(start, end);
  }
}
