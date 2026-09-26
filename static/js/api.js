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
  if (banner) banner.hidden = true;
  var status = document.getElementById("connection-status");
  if (status) status.classList.toggle("is-stale", !!message);
  var updated = document.getElementById("last-update");
  if (updated) updated.textContent = message ? "Refresh needed" :
    lastSuccessfulRead ? "Updated " + lastSuccessfulRead.toLocaleTimeString("en-US", {
      timeZone:"America/Chicago",hour:"2-digit",minute:"2-digit"
    }) + " CT" : "Connecting…";
  if (message && typeof pushLocalNotification === "function")
    pushLocalNotification("system","Dashboard refresh issue",message,"warning");
}
async function apiFetch(url, key, options) {
  // Backward compatible: apiFetch(url, key) for reads and
  // apiFetch(url, {method, headers, body}) for mutations.
  if (key && typeof key === "object") {
    options = key;
    key = null;
  }
  options = options || {};
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
    var fetchOptions = Object.assign({}, options, {
      signal: controller.signal,
      cache: "no-store",
      credentials: "same-origin",
    });
    var response = await fetch(url, fetchOptions);
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

// Patch existing nodes instead of replacing the view. Focus, selection and nested
// scroll positions survive; unchanged nodes (including search controls) stay put.
function updateHTML(target, html) {
  if (!target) return;
  var template = document.createElement('template');
  template.innerHTML = html;
  function key(node) {
    return node.nodeType === 1 ? node.id || node.getAttribute('data-id') || node.getAttribute('data-agent-id') : null;
  }
  function patch(parent, desired) {
    var cursor = parent.firstChild;
    Array.from(desired.childNodes).forEach(function (next) {
      var node = cursor, id = key(next);
      if (id && key(node || {}) !== id) {
        node = Array.from(parent.childNodes).find(function (item) { return key(item) === id; });
        if (node) parent.insertBefore(node, cursor);
      }
      if (!node || node.nodeType !== next.nodeType || node.nodeName !== next.nodeName || (id && key(node) !== id)) {
        parent.insertBefore(next.cloneNode(true), cursor);
        return;
      }
      cursor = node.nextSibling;
      if (node.nodeType === 3) {
        if (node.nodeValue !== next.nodeValue) node.nodeValue = next.nodeValue;
      } else if (node.nodeType === 1) {
        // These slots are rendered independently. Keep their live children.
        if (['fleet-status-wrap','intel-units-wrap'].includes(node.id) && !next.childNodes.length) return;
        Array.from(node.attributes).forEach(function (attr) {
          if (!next.hasAttribute(attr.name)) node.removeAttribute(attr.name);
        });
        Array.from(next.attributes).forEach(function (attr) {
          if (node.getAttribute(attr.name) !== attr.value) node.setAttribute(attr.name, attr.value);
        });
        if (!node.matches('input,textarea,select')) patch(node, next);
      }
    });
    while (cursor) { var following = cursor.nextSibling; parent.removeChild(cursor); cursor = following; }
  }
  patch(target, template.content);
  if (typeof applyOverviewLayout === 'function' && target.id === 'stat-grid') applyOverviewLayout();
}
