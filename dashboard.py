"""
dashboard.py — Kurtex Alert Bot Web Dashboard
Runs as a Flask server alongside the Telegram bot.
Telegram Login Widget for auth, auto-refreshing data via JSON API.
Start with: python dashboard.py (or imported by bot.py in a thread)
"""

import hashlib
import hmac
import json
import logging
import os
import time
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Thread

from flask import Flask, jsonify, render_template_string, request, session, redirect

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("DASHBOARD_SECRET", "kurtex-dashboard-secret-change-me")

DATA_DIR   = Path(os.getenv("DATA_DIR", "/app/data"))
BOT_TOKEN  = os.getenv("BOT_TOKEN", "")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))

# ── Auth helpers ──────────────────────────────────────────────────────────────

def verify_telegram_login(data: dict) -> bool:
    """Verify Telegram Login Widget data signature."""
    check_hash = data.pop("hash", "")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    computed   = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
    # Restore hash
    data["hash"] = check_hash
    # Check timestamp (not older than 1 day)
    if abs(time.time() - int(data.get("auth_date", 0))) > 86400:
        return False
    return hmac.compare_digest(computed, check_hash)


def get_bot_username() -> str:
    """Extract bot username from token if not set."""
    username = os.getenv("BOT_USERNAME", "")
    return username


# ── Data helpers ──────────────────────────────────────────────────────────────

def load_cases() -> list:
    f = DATA_DIR / "cases.json"
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []


def load_users() -> dict:
    f = DATA_DIR / "users.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def week_start_str() -> str:
    now = datetime.now(timezone.utc)
    return (now - timedelta(days=now.weekday())).date().isoformat()


def fmt_dt(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%b %d %H:%M")
    except Exception:
        return iso[:16]


def fmt_secs(secs) -> str:
    if secs is None:
        return "—"
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.route("/auth/telegram")
def telegram_auth():
    data = dict(request.args)
    if not data.get("hash"):
        return redirect("/login?error=missing")
    if verify_telegram_login(data):
        session["user"] = {
            "id":         data.get("id"),
            "first_name": data.get("first_name", ""),
            "username":   data.get("username", ""),
            "photo_url":  data.get("photo_url", ""),
        }
        return redirect("/")
    return redirect("/login?error=invalid")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/api/stats")
def api_stats():
    if not session.get("user"):
        return jsonify({"error": "unauthorized"}), 401

    cases    = load_cases()
    today    = today_str()
    wk_start = week_start_str()

    today_cases = [c for c in cases if c.get("opened_at", "").startswith(today)]
    week_cases  = [c for c in cases if c.get("opened_at", "") >= wk_start]

    # Status counts today
    status_today = Counter(c["status"] for c in today_cases)

    # Leaderboard (all time)
    agent_counts = Counter(
        c["agent_name"] for c in cases
        if c.get("agent_name") and c["status"] in ("assigned", "reported", "done")
    )

    # Top groups
    group_counts = Counter(c.get("group_name", "Unknown") for c in cases)

    # Top drivers  
    driver_counts = Counter(c.get("driver_name", "Unknown") for c in cases)

    # Top words in descriptions
    all_desc = " ".join(c.get("description", "") for c in cases).lower()
    stop = {"the","a","an","is","in","at","on","to","and","or","for","of","with",
            "this","that","it","was","has","have","be","not","#maintenance","#repair","#repairs","i"}
    words = [w.strip(".,!?:;()[]") for w in all_desc.split() if len(w) > 2 and w not in stop]
    top_words = Counter(words).most_common(10)

    # Reassigned cases (those with multiple agent assignments - we detect by status history)
    # Simple proxy: cases that have response_secs but were reassigned
    reassigned = [c for c in cases if c.get("agent_name") and c.get("status") == "assigned"
                  and c.get("response_secs", 0) == 0]

    # Avg response time
    resp_times = [c["response_secs"] for c in cases if c.get("response_secs")]
    avg_resp = int(sum(resp_times) / len(resp_times)) if resp_times else 0

    return jsonify({
        "today": {
            "total":    len(today_cases),
            "open":     status_today.get("open", 0),
            "assigned": status_today.get("assigned", 0) + status_today.get("reported", 0),
            "done":     status_today.get("done", 0),
            "missed":   status_today.get("missed", 0),
        },
        "week": {
            "total":  len(week_cases),
            "done":   sum(1 for c in week_cases if c["status"] == "done"),
            "missed": sum(1 for c in week_cases if c["status"] == "missed"),
        },
        "all_time": {
            "total":    len(cases),
            "done":     sum(1 for c in cases if c["status"] == "done"),
            "avg_resp": fmt_secs(avg_resp),
        },
        "leaderboard": [{"name": n, "count": c} for n, c in agent_counts.most_common(10)],
        "top_groups":  [{"name": n, "count": c} for n, c in group_counts.most_common(5)],
        "top_drivers": [{"name": n, "count": c} for n, c in driver_counts.most_common(5)],
        "top_words":   [{"word": w, "count": c} for w, c in top_words],
    })


@app.route("/api/cases")
def api_cases():
    if not session.get("user"):
        return jsonify({"error": "unauthorized"}), 401

    filter_type = request.args.get("filter", "today")
    cases = load_cases()

    if filter_type == "today":
        cases = [c for c in cases if c.get("opened_at", "").startswith(today_str())]
    elif filter_type == "week":
        cases = [c for c in cases if c.get("opened_at", "") >= week_start_str()]
    elif filter_type == "missed":
        cases = [c for c in cases if c["status"] == "missed"]
    elif filter_type == "active":
        cases = [c for c in cases if c["status"] in ("open", "assigned", "reported")]

    # Sort newest first
    cases = sorted(cases, key=lambda c: c.get("opened_at", ""), reverse=True)[:100]

    return jsonify([{
        "id":           c["id"][:8],
        "driver":       c.get("driver_name", "—"),
        "group":        c.get("group_name", "—"),
        "agent":        c.get("agent_name") or "—",
        "status":       c.get("status", "open"),
        "opened":       fmt_dt(c.get("opened_at")),
        "closed":       fmt_dt(c.get("closed_at")),
        "response":     fmt_secs(c.get("response_secs")),
        "description":  (c.get("description") or "")[:120],
        "notes":        c.get("notes") or "",
    } for c in cases])


# ── Pages ─────────────────────────────────────────────────────────────────────

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kurtex Dashboard — Login</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Plus Jakarta Sans', sans-serif;
  min-height: 100vh;
  display: flex; align-items: center; justify-content: center;
  background: #111;
  position: relative;
  overflow: hidden;
}
.bg {
  position: absolute; inset: 0;
  background: url('https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?auto=format&fit=crop&w=1920&q=80') center/cover no-repeat;
  opacity: 0.18;
  filter: grayscale(40%);
}
.card {
  position: relative;
  background: rgba(24,24,27,0.92);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 20px;
  padding: 48px 40px;
  text-align: center;
  width: 100%;
  max-width: 380px;
  backdrop-filter: blur(12px);
}
.logo { font-size: 32px; margin-bottom: 8px; }
h1 { color: #fff; font-size: 22px; font-weight: 700; margin-bottom: 6px; }
p { color: #71717a; font-size: 14px; margin-bottom: 32px; }
.error { color: #f87171; font-size: 13px; margin-bottom: 16px; }
.tg-wrap { display: flex; justify-content: center; }
</style>
</head>
<body>
<div class="bg"></div>
<div class="card">
  <div class="logo">🚛</div>
  <h1>Kurtex Dashboard</h1>
  <p>Truck Maintenance Command Center</p>
  {% if error %}<div class="error">Authentication failed. Try again.</div>{% endif %}
  <div class="tg-wrap">
    <script async src="https://telegram.org/js/telegram-widget.js?22"
      data-telegram-login="{{ bot_username }}"
      data-size="large"
      data-auth-url="/auth/telegram"
      data-request-access="write">
    </script>
  </div>
</div>
</body>
</html>"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kurtex Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root {
  --bg:      #0f0f11;
  --surface: #18181b;
  --border:  rgba(255,255,255,0.07);
  --text:    #e4e4e7;
  --muted:   #71717a;
  --accent:  #6366f1;
  --green:   #22c55e;
  --red:     #ef4444;
  --yellow:  #eab308;
  --blue:    #3b82f6;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Plus Jakarta Sans', sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
}

/* BG */
.hero-bg {
  position: fixed; inset: 0; z-index: 0;
  background: url('https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?auto=format&fit=crop&w=1920&q=80') center/cover no-repeat;
  opacity: 0.04;
  pointer-events: none;
}

/* Layout */
.layout { position: relative; z-index: 1; display: flex; min-height: 100vh; }

/* Sidebar */
.sidebar {
  width: 220px; flex-shrink: 0;
  background: var(--surface);
  border-right: 1px solid var(--border);
  padding: 24px 16px;
  position: sticky; top: 0; height: 100vh;
  display: flex; flex-direction: column;
}
.sidebar-logo { display: flex; align-items: center; gap: 10px; margin-bottom: 32px; padding: 0 8px; }
.sidebar-logo span { font-size: 22px; }
.sidebar-logo h2 { font-size: 15px; font-weight: 700; color: var(--text); }
.sidebar-logo small { font-size: 11px; color: var(--muted); display: block; }
nav a {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 12px; border-radius: 10px;
  color: var(--muted); font-size: 14px; font-weight: 500;
  text-decoration: none; margin-bottom: 4px;
  cursor: pointer; transition: all 0.15s;
}
nav a:hover, nav a.active { background: rgba(99,102,241,0.12); color: var(--text); }
nav a.active { color: var(--accent); }
.nav-icon { font-size: 16px; width: 20px; text-align: center; }
.sidebar-footer { margin-top: auto; padding-top: 16px; border-top: 1px solid var(--border); }
.user-chip {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 10px; border-radius: 10px;
}
.user-chip img { width: 28px; height: 28px; border-radius: 50%; }
.user-chip-info { flex: 1; min-width: 0; }
.user-chip-name { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.user-chip-role { font-size: 11px; color: var(--muted); }
.logout-btn {
  width: 100%; margin-top: 8px; padding: 8px;
  background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.2);
  color: #ef4444; border-radius: 8px; font-size: 13px; font-weight: 500;
  cursor: pointer; transition: all 0.15s;
}
.logout-btn:hover { background: rgba(239,68,68,0.2); }

/* Main */
.main { flex: 1; padding: 28px 32px; overflow-x: hidden; }
.topbar {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 28px;
}
.topbar h1 { font-size: 22px; font-weight: 700; }
.refresh-badge {
  display: flex; align-items: center; gap: 6px;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 20px; padding: 6px 14px; font-size: 12px; color: var(--muted);
}
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green); animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }

/* Stat cards */
.stat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 16px; margin-bottom: 28px; }
.stat-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 16px; padding: 20px;
}
.stat-label { font-size: 12px; color: var(--muted); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.05em; }
.stat-value { font-size: 32px; font-weight: 800; line-height: 1; }
.stat-value.green { color: var(--green); }
.stat-value.red   { color: var(--red); }
.stat-value.blue  { color: var(--blue); }
.stat-value.yellow{ color: var(--yellow); }
.stat-value.accent{ color: var(--accent); }

/* Section */
.section { margin-bottom: 28px; }
.section-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 16px;
}
.section-title { font-size: 15px; font-weight: 700; }

/* Filter tabs */
.filter-tabs { display: flex; gap: 6px; }
.tab-btn {
  padding: 6px 14px; border-radius: 8px; font-size: 13px; font-weight: 500;
  border: 1px solid var(--border); background: transparent; color: var(--muted);
  cursor: pointer; transition: all 0.15s;
}
.tab-btn:hover { color: var(--text); }
.tab-btn.active { background: var(--accent); border-color: var(--accent); color: #fff; }

/* Table */
.table-wrap { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; overflow: hidden; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th {
  padding: 12px 16px; text-align: left;
  color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em;
  border-bottom: 1px solid var(--border);
}
tbody tr { border-bottom: 1px solid var(--border); transition: background 0.1s; }
tbody tr:last-child { border-bottom: none; }
tbody tr:hover { background: rgba(255,255,255,0.02); }
td { padding: 12px 16px; vertical-align: middle; }
.status-badge {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 600; text-transform: uppercase;
}
.status-open     { background: rgba(59,130,246,0.15); color: #60a5fa; }
.status-assigned { background: rgba(234,179,8,0.15);  color: #fbbf24; }
.status-reported { background: rgba(168,85,247,0.15); color: #c084fc; }
.status-done     { background: rgba(34,197,94,0.15);  color: #4ade80; }
.status-missed   { background: rgba(239,68,68,0.15);  color: #f87171; }
.desc-cell { max-width: 280px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--muted); }

/* Two col grid */
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 28px; }
@media (max-width: 900px) { .two-col { grid-template-columns: 1fr; } }

/* List card */
.list-card { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 20px; }
.list-card h3 { font-size: 14px; font-weight: 700; margin-bottom: 16px; }
.list-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 0; border-bottom: 1px solid var(--border);
}
.list-row:last-child { border-bottom: none; }
.list-name { font-size: 13px; font-weight: 500; }
.list-count {
  font-size: 13px; font-weight: 700; color: var(--accent);
  background: rgba(99,102,241,0.1); padding: 2px 10px; border-radius: 20px;
}
.medal { font-size: 16px; margin-right: 8px; }
.bar-wrap { flex: 1; margin: 0 12px; height: 4px; background: rgba(255,255,255,0.06); border-radius: 2px; }
.bar-fill { height: 100%; border-radius: 2px; background: var(--accent); transition: width 0.5s; }

/* Word cloud */
.word-grid { display: flex; flex-wrap: wrap; gap: 8px; }
.word-tag {
  padding: 5px 12px; border-radius: 20px; font-size: 12px; font-weight: 600;
  background: rgba(99,102,241,0.1); color: var(--accent); border: 1px solid rgba(99,102,241,0.2);
}

/* Loading */
.loading { text-align: center; padding: 40px; color: var(--muted); font-size: 14px; }

/* Page sections */
.page { display: none; }
.page.active { display: block; }
</style>
</head>
<body>
<div class="hero-bg"></div>
<div class="layout">

  <!-- Sidebar -->
  <aside class="sidebar">
    <div class="sidebar-logo">
      <span>🚛</span>
      <div>
        <h2>Kurtex</h2>
        <small>Alert Dashboard</small>
      </div>
    </div>
    <nav>
      <a class="active" onclick="showPage('overview')">
        <span class="nav-icon">📊</span> Overview
      </a>
      <a onclick="showPage('cases')">
        <span class="nav-icon">📋</span> Cases
      </a>
      <a onclick="showPage('missed')">
        <span class="nav-icon">⚠️</span> Missed
      </a>
      <a onclick="showPage('leaderboard')">
        <span class="nav-icon">🏆</span> Leaderboard
      </a>
      <a onclick="showPage('analytics')">
        <span class="nav-icon">🔍</span> Analytics
      </a>
    </nav>
    <div class="sidebar-footer">
      <div class="user-chip">
        {% if user.photo_url %}
        <img src="{{ user.photo_url }}" alt="">
        {% else %}
        <div style="width:28px;height:28px;border-radius:50%;background:#3f3f46;display:flex;align-items:center;justify-content:center;font-size:13px;">👤</div>
        {% endif %}
        <div class="user-chip-info">
          <div class="user-chip-name">{{ user.first_name }}</div>
          <div class="user-chip-role">Manager</div>
        </div>
      </div>
      <button class="logout-btn" onclick="window.location='/logout'">Sign out</button>
    </div>
  </aside>

  <!-- Main -->
  <main class="main">
    <div class="topbar">
      <h1 id="page-title">Overview</h1>
      <div class="refresh-badge">
        <div class="dot"></div>
        <span id="last-update">Loading...</span>
      </div>
    </div>

    <!-- Overview Page -->
    <div class="page active" id="page-overview">
      <div class="stat-grid" id="stat-grid">
        <div class="loading">Loading stats...</div>
      </div>
      <div class="two-col">
        <div class="list-card">
          <h3>🏆 Top Agents Today</h3>
          <div id="leaderboard-mini"></div>
        </div>
        <div class="list-card">
          <h3>📡 Top Groups</h3>
          <div id="top-groups"></div>
        </div>
      </div>
      <div class="section">
        <div class="section-header">
          <div class="section-title">Recent Cases</div>
        </div>
        <div class="table-wrap" id="recent-table"><div class="loading">Loading...</div></div>
      </div>
    </div>

    <!-- Cases Page -->
    <div class="page" id="page-cases">
      <div class="section">
        <div class="section-header">
          <div class="section-title">All Cases</div>
          <div class="filter-tabs">
            <button class="tab-btn active" onclick="setCaseFilter('today', this)">Today</button>
            <button class="tab-btn" onclick="setCaseFilter('week', this)">This Week</button>
            <button class="tab-btn" onclick="setCaseFilter('active', this)">Active</button>
            <button class="tab-btn" onclick="setCaseFilter('all', this)">All</button>
          </div>
        </div>
        <div class="table-wrap" id="cases-table"><div class="loading">Loading...</div></div>
      </div>
    </div>

    <!-- Missed Page -->
    <div class="page" id="page-missed">
      <div class="section">
        <div class="section-header">
          <div class="section-title">Missed Cases</div>
        </div>
        <div class="table-wrap" id="missed-table"><div class="loading">Loading...</div></div>
      </div>
    </div>

    <!-- Leaderboard Page -->
    <div class="page" id="page-leaderboard">
      <div class="two-col">
        <div class="list-card">
          <h3>🏆 All Time Leaderboard</h3>
          <div id="leaderboard-full"></div>
        </div>
        <div class="list-card">
          <h3>👤 Top Drivers (Most Cases)</h3>
          <div id="top-drivers"></div>
        </div>
      </div>
    </div>

    <!-- Analytics Page -->
    <div class="page" id="page-analytics">
      <div class="two-col">
        <div class="list-card">
          <h3>📊 This Week</h3>
          <div id="week-stats"></div>
        </div>
        <div class="list-card">
          <h3>🔤 Top Issue Keywords</h3>
          <div id="word-cloud"></div>
        </div>
      </div>
      <div class="list-card" style="margin-top:16px">
        <h3>📡 Cases by Group</h3>
        <div id="group-bars"></div>
      </div>
    </div>

  </main>
</div>

<script>
let currentFilter = 'today';
let currentPage   = 'overview';
let stats         = {};

const medals = ['🥇','🥈','🥉'];

function showPage(page) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('nav a').forEach(a => a.classList.remove('active'));
  document.getElementById('page-' + page).classList.add('active');
  document.querySelectorAll('nav a')[['overview','cases','missed','leaderboard','analytics'].indexOf(page)].classList.add('active');
  const titles = {overview:'Overview',cases:'Cases',missed:'Missed Cases',leaderboard:'Leaderboard',analytics:'Analytics'};
  document.getElementById('page-title').textContent = titles[page];
  currentPage = page;
  refresh();
}

function setCaseFilter(f, btn) {
  currentFilter = f;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  loadCases(f);
}

function statusBadge(s) {
  return `<span class="status-badge status-${s}">${s}</span>`;
}

function caseTable(cases) {
  if (!cases.length) return '<div class="loading">No cases found.</div>';
  return `<table>
    <thead><tr>
      <th>ID</th><th>Driver</th><th>Group</th><th>Agent</th>
      <th>Status</th><th>Opened</th><th>Response</th><th>Description</th>
    </tr></thead>
    <tbody>${cases.map(c => `<tr>
      <td><code style="color:#a1a1aa;font-size:11px">${c.id}</code></td>
      <td><b>${c.driver}</b></td>
      <td style="color:#a1a1aa">${c.group}</td>
      <td>${c.agent}</td>
      <td>${statusBadge(c.status)}</td>
      <td style="color:#a1a1aa;font-size:12px">${c.opened}</td>
      <td style="font-size:12px">${c.response}</td>
      <td class="desc-cell">${c.description}</td>
    </tr>`).join('')}</tbody>
  </table>`;
}

async function loadStats() {
  try {
    const r = await fetch('/api/stats');
    if (r.status === 401) { window.location = '/login'; return; }
    stats = await r.json();

    // Stat cards
    const t = stats.today;
    document.getElementById('stat-grid').innerHTML = `
      <div class="stat-card"><div class="stat-label">Today Total</div><div class="stat-value accent">${t.total}</div></div>
      <div class="stat-card"><div class="stat-label">Assigned</div><div class="stat-value yellow">${t.assigned}</div></div>
      <div class="stat-card"><div class="stat-label">Resolved</div><div class="stat-value green">${t.done}</div></div>
      <div class="stat-card"><div class="stat-label">Missed</div><div class="stat-value red">${t.missed}</div></div>
      <div class="stat-card"><div class="stat-label">Open</div><div class="stat-value blue">${t.open}</div></div>
      <div class="stat-card"><div class="stat-label">Avg Response</div><div class="stat-value" style="font-size:20px">${stats.all_time.avg_resp}</div></div>
    `;

    // Leaderboard mini
    const lb = stats.leaderboard.slice(0,5);
    const maxLb = lb[0]?.count || 1;
    document.getElementById('leaderboard-mini').innerHTML = lb.map((a,i) => `
      <div class="list-row">
        <span class="medal">${medals[i] || (i+1)+'.'}</span>
        <span class="list-name">${a.name}</span>
        <div class="bar-wrap"><div class="bar-fill" style="width:${(a.count/maxLb*100).toFixed(0)}%"></div></div>
        <span class="list-count">${a.count}</span>
      </div>`).join('') || '<div style="color:#71717a;font-size:13px;padding:8px 0">No data yet</div>';

    // Top groups
    const grps = stats.top_groups;
    const maxG = grps[0]?.count || 1;
    document.getElementById('top-groups').innerHTML = grps.map(g => `
      <div class="list-row">
        <span class="list-name">${g.name}</span>
        <div class="bar-wrap"><div class="bar-fill" style="width:${(g.count/maxG*100).toFixed(0)}%"></div></div>
        <span class="list-count">${g.count}</span>
      </div>`).join('') || '<div style="color:#71717a;font-size:13px;padding:8px 0">No data yet</div>';

    // Leaderboard full
    document.getElementById('leaderboard-full').innerHTML = stats.leaderboard.map((a,i) => `
      <div class="list-row">
        <span class="medal">${medals[i] || (i+1)+'.'}</span>
        <span class="list-name">${a.name}</span>
        <span class="list-count">${a.count} cases</span>
      </div>`).join('') || '<div style="color:#71717a;font-size:13px;padding:8px 0">No data yet</div>';

    // Top drivers
    document.getElementById('top-drivers').innerHTML = stats.top_drivers.map((d,i) => `
      <div class="list-row">
        <span style="color:#71717a;font-size:12px;margin-right:8px">${i+1}.</span>
        <span class="list-name">${d.name}</span>
        <span class="list-count">${d.count}</span>
      </div>`).join('') || '<div style="color:#71717a;font-size:13px;padding:8px 0">No data yet</div>';

    // Week stats
    const w = stats.week;
    document.getElementById('week-stats').innerHTML = `
      <div class="list-row"><span class="list-name">Total Cases</span><span class="list-count">${w.total}</span></div>
      <div class="list-row"><span class="list-name">Resolved</span><span style="color:#4ade80;font-weight:700">${w.done}</span></div>
      <div class="list-row"><span class="list-name">Missed</span><span style="color:#f87171;font-weight:700">${w.missed}</span></div>
      <div class="list-row"><span class="list-name">Resolution Rate</span><span class="list-count">${w.total ? Math.round(w.done/w.total*100) : 0}%</span></div>
    `;

    // Word cloud
    document.getElementById('word-cloud').innerHTML = `<div class="word-grid">${
      stats.top_words.map(w => `<span class="word-tag">${w.word} <b>${w.count}</b></span>`).join('')
    }</div>` || '<div style="color:#71717a;font-size:13px">No data yet</div>';

    // Group bars
    const maxGb = grps[0]?.count || 1;
    document.getElementById('group-bars').innerHTML = grps.map(g => `
      <div class="list-row">
        <span class="list-name" style="width:180px;flex-shrink:0">${g.name}</span>
        <div class="bar-wrap"><div class="bar-fill" style="width:${(g.count/maxGb*100).toFixed(0)}%"></div></div>
        <span class="list-count">${g.count}</span>
      </div>`).join('') || '<div style="color:#71717a;font-size:13px;padding:8px 0">No data yet</div>';

  } catch(e) { console.error(e); }
}

async function loadCases(filter) {
  const f = filter || currentFilter;
  try {
    const r = await fetch('/api/cases?filter=' + f);
    if (r.status === 401) { window.location = '/login'; return; }
    const cases = await r.json();
    if (currentPage === 'overview') {
      document.getElementById('recent-table').innerHTML = caseTable(cases.slice(0,10));
    } else if (currentPage === 'cases') {
      document.getElementById('cases-table').innerHTML = caseTable(cases);
    } else if (currentPage === 'missed') {
      document.getElementById('missed-table').innerHTML = caseTable(cases);
    }
  } catch(e) { console.error(e); }
}

async function refresh() {
  await loadStats();
  if (currentPage === 'overview')     await loadCases('today');
  else if (currentPage === 'cases')   await loadCases(currentFilter);
  else if (currentPage === 'missed')  await loadCases('missed');
  document.getElementById('last-update').textContent =
    'Updated ' + new Date().toLocaleTimeString();
}

// Initial load + auto-refresh every 10s
refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>"""


@app.route("/login")
def login():
    error = request.args.get("error")
    bot_username = get_bot_username()
    return render_template_string(LOGIN_HTML, bot_username=bot_username, error=error)


@app.route("/")
def index():
    if not session.get("user"):
        return redirect("/login")
    return render_template_string(DASHBOARD_HTML, user=session["user"])


# ── Runner ────────────────────────────────────────────────────────────────────

def run_dashboard():
    """Run Flask in a background thread — called from bot.py."""
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=DASHBOARD_PORT, debug=False, use_reloader=False)


def start_dashboard_thread():
    t = Thread(target=run_dashboard, daemon=True)
    t.start()
    logger.info(f"Dashboard started on port {DASHBOARD_PORT}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_dashboard()
