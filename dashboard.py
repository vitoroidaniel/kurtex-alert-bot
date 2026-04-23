"""
dashboard.py — Kurtex Alert Bot Web Dashboard
Light theme, Telegram login, auto-refresh, CSV export, search, reassigned tab.
"""

import csv
import hashlib
import hmac
import io
import json
import logging
import os
import time
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Thread

from flask import Flask, jsonify, render_template_string, request, session, redirect, Response

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("DASHBOARD_SECRET", "kurtex-dashboard-secret-change-me")

DATA_DIR       = Path(os.getenv("DATA_DIR", "/app/data"))
BOT_TOKEN      = os.getenv("BOT_TOKEN", "")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))


def verify_telegram_login(data: dict) -> bool:
    check_hash = data.pop("hash", "")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    computed   = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
    data["hash"] = check_hash
    if abs(time.time() - int(data.get("auth_date", 0))) > 86400:
        return False
    return hmac.compare_digest(computed, check_hash)


def get_bot_username() -> str:
    return os.getenv("BOT_USERNAME", "")


def load_cases() -> list:
    f = DATA_DIR / "cases.json"
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []


def today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def week_start_str() -> str:
    now = datetime.now(timezone.utc)
    return (now - timedelta(days=now.weekday())).date().isoformat()


def month_start_str() -> str:
    now = datetime.now(timezone.utc)
    return now.date().replace(day=1).isoformat()


def fmt_dt(iso):
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%b %d %H:%M")
    except Exception:
        return str(iso)[:16]


def fmt_secs(secs):
    if secs is None:
        return "—"
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs//60}m {secs%60}s"
    return f"{secs//3600}h {(secs%3600)//60}m"


def serialize_case(c):
    return {
        "id":          c["id"][:8],
        "full_id":     c["id"],
        "driver":      c.get("driver_name", "—"),
        "group":       c.get("group_name", "—"),
        "agent":       c.get("agent_name") or "—",
        "status":      c.get("status", "open"),
        "opened":      fmt_dt(c.get("opened_at")),
        "closed":      fmt_dt(c.get("closed_at")),
        "response":    fmt_secs(c.get("response_secs")),
        "description": (c.get("description") or "")[:200],
        "notes":       c.get("notes") or "",
        "reassigned":  bool(c.get("reassigned")),
    }


# ── Auth ──────────────────────────────────────────────────────────────────────

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


# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    if not session.get("user"):
        return jsonify({"error": "unauthorized"}), 401

    cases    = load_cases()
    today    = today_str()
    wk_start = week_start_str()
    mo_start = month_start_str()

    today_cases = [c for c in cases if c.get("opened_at", "").startswith(today)]
    week_cases  = [c for c in cases if c.get("opened_at", "") >= wk_start]
    month_cases = [c for c in cases if c.get("opened_at", "") >= mo_start]

    from collections import Counter as C
    status_today = C(c["status"] for c in today_cases)

    def leaderboard(case_list):
        counts = C(
            c["agent_name"] for c in case_list
            if c.get("agent_name") and c["status"] in ("assigned", "reported", "done")
        )
        return [{"name": n, "count": v} for n, v in counts.most_common(10)]

    # Top groups
    group_counts = C(c.get("group_name", "Unknown") for c in cases)

    # Only # keywords
    all_desc = " ".join(c.get("description", "") for c in cases).lower()
    import re
    hashtags = re.findall(r'#\w+', all_desc)
    top_words = [{"word": w, "count": c} for w, c in C(hashtags).most_common(15)]

    resp_times = [c["response_secs"] for c in cases if c.get("response_secs")]
    avg_resp   = int(sum(resp_times) / len(resp_times)) if resp_times else 0

    reassigned_count = sum(1 for c in cases if c.get("reassigned"))

    return jsonify({
        "today": {
            "total":    len(today_cases),
            "open":     status_today.get("open", 0),
            "assigned": status_today.get("assigned", 0) + status_today.get("reported", 0),
            "done":     status_today.get("done", 0),
            "missed":   status_today.get("missed", 0),
        },
        "week":  {"total": len(week_cases),  "done": sum(1 for c in week_cases  if c["status"]=="done"), "missed": sum(1 for c in week_cases  if c["status"]=="missed")},
        "month": {"total": len(month_cases), "done": sum(1 for c in month_cases if c["status"]=="done"), "missed": sum(1 for c in month_cases if c["status"]=="missed")},
        "all_time": {"total": len(cases), "done": sum(1 for c in cases if c["status"]=="done"), "avg_resp": fmt_secs(avg_resp)},
        "leaderboard_day":   leaderboard(today_cases),
        "leaderboard_week":  leaderboard(week_cases),
        "leaderboard_month": leaderboard(month_cases),
        "top_groups":        [{"name": n, "count": c} for n, c in group_counts.most_common(5)],
        "top_words":         top_words,
        "reassigned_count":  reassigned_count,
    })


@app.route("/api/cases")
def api_cases():
    if not session.get("user"):
        return jsonify({"error": "unauthorized"}), 401

    filter_type = request.args.get("filter", "today")
    search      = request.args.get("search", "").lower().strip()
    cases       = load_cases()

    if filter_type == "today":
        cases = [c for c in cases if c.get("opened_at", "").startswith(today_str())]
    elif filter_type == "week":
        cases = [c for c in cases if c.get("opened_at", "") >= week_start_str()]
    elif filter_type == "missed":
        cases = [c for c in cases if c["status"] == "missed"]
    elif filter_type == "active":
        cases = [c for c in cases if c["status"] in ("open", "assigned", "reported")]
    elif filter_type == "reassigned":
        cases = [c for c in cases if c.get("reassigned")]

    if search:
        cases = [
            c for c in cases
            if search in (c.get("driver_name") or "").lower()
            or search in (c.get("group_name") or "").lower()
            or search in (c.get("agent_name") or "").lower()
            or search in (c.get("description") or "").lower()
        ]

    cases = sorted(cases, key=lambda c: c.get("opened_at", ""), reverse=True)[:200]
    return jsonify([serialize_case(c) for c in cases])


@app.route("/api/case/<case_id>")
def api_case_detail(case_id):
    if not session.get("user"):
        return jsonify({"error": "unauthorized"}), 401
    cases = load_cases()
    for c in cases:
        if c["id"].startswith(case_id) or c["id"] == case_id:
            return jsonify(serialize_case(c) | {
                "full_description": c.get("description", ""),
                "full_notes":       c.get("notes", ""),
                "agent_username":   c.get("agent_username", ""),
                "driver_username":  c.get("driver_username", ""),
                "assigned_at":      fmt_dt(c.get("assigned_at")),
                "resolution_secs":  fmt_secs(c.get("resolution_secs")),
            })
    return jsonify({"error": "not found"}), 404


@app.route("/api/export")
def api_export():
    if not session.get("user"):
        return jsonify({"error": "unauthorized"}), 401
    cases  = load_cases()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID","Reported By","Reporter Username","Group","Assigned To","Status","Opened","Assigned","Closed","Response (s)","Resolution (s)","Description","Notes"])
    for c in sorted(cases, key=lambda x: x.get("opened_at",""), reverse=True):
        writer.writerow([
            c["id"][:8],
            c.get("driver_name",""),
            c.get("driver_username",""),
            c.get("group_name",""),
            c.get("agent_name",""),
            c.get("status",""),
            c.get("opened_at","")[:16],
            c.get("assigned_at","")[:16] if c.get("assigned_at") else "",
            c.get("closed_at","")[:16] if c.get("closed_at") else "",
            c.get("response_secs",""),
            c.get("resolution_secs",""),
            c.get("description",""),
            c.get("notes",""),
        ])
    output.seek(0)
    today = datetime.now().strftime("%Y-%m-%d")
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=kurtex-cases-{today}.csv"}
    )


# ── Pages ─────────────────────────────────────────────────────────────────────

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Kurtex Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Plus Jakarta Sans',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#f4f4f5;position:relative;overflow:hidden}
.bg{position:absolute;inset:0;background:url('https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?auto=format&fit=crop&w=1920&q=80')center/cover no-repeat;opacity:0.12;filter:grayscale(20%)}
.card{position:relative;background:#fff;border:1px solid #e4e4e7;border-radius:20px;padding:48px 40px;text-align:center;width:100%;max-width:380px;box-shadow:0 4px 32px rgba(0,0,0,0.08)}
.logo{font-size:36px;margin-bottom:10px}
h1{color:#18181b;font-size:22px;font-weight:700;margin-bottom:6px}
p{color:#71717a;font-size:14px;margin-bottom:32px}
.error{color:#ef4444;font-size:13px;margin-bottom:16px}
.tg-wrap{display:flex;justify-content:center}
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
      data-request-access="write"></script>
  </div>
</div>
</body>
</html>"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Kurtex Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#f8f8fa;--surface:#fff;--surface2:#f4f4f6;
  --border:#e4e4e7;--border2:#d4d4d8;
  --text:#18181b;--muted:#71717a;--muted2:#a1a1aa;
  --accent:#6366f1;--accent-light:rgba(99,102,241,0.08);
  --green:#16a34a;--green-bg:rgba(22,163,74,0.08);
  --red:#dc2626;--red-bg:rgba(220,38,38,0.08);
  --yellow:#ca8a04;--yellow-bg:rgba(202,138,4,0.08);
  --blue:#2563eb;--blue-bg:rgba(37,99,235,0.08);
  --purple:#7c3aed;--purple-bg:rgba(124,58,237,0.08);
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Plus Jakarta Sans',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
.hero-bg{position:fixed;inset:0;z-index:0;background:url('https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?auto=format&fit=crop&w=1920&q=80')center/cover no-repeat;opacity:0.03;pointer-events:none}
.layout{position:relative;z-index:1;display:flex;min-height:100vh}

/* Sidebar */
.sidebar{width:220px;flex-shrink:0;background:var(--surface);border-right:1px solid var(--border);padding:24px 14px;position:sticky;top:0;height:100vh;display:flex;flex-direction:column}
.sidebar-logo{display:flex;align-items:center;gap:10px;margin-bottom:28px;padding:0 8px}
.sidebar-logo span{font-size:22px}
.sidebar-logo h2{font-size:14px;font-weight:700;color:var(--text)}
.sidebar-logo small{font-size:10px;color:var(--muted);display:block}
nav a{display:flex;align-items:center;gap:9px;padding:9px 11px;border-radius:9px;color:var(--muted);font-size:13px;font-weight:500;text-decoration:none;margin-bottom:2px;cursor:pointer;transition:all .15s}
nav a:hover{background:var(--surface2);color:var(--text)}
nav a.active{background:var(--accent-light);color:var(--accent)}
.nav-icon{font-size:15px;width:18px;text-align:center}
.sidebar-footer{margin-top:auto;padding-top:14px;border-top:1px solid var(--border)}
.user-chip{display:flex;align-items:center;gap:8px;padding:6px 8px}
.user-chip img{width:28px;height:28px;border-radius:50%;border:1px solid var(--border)}
.user-chip-name{font-size:12px;font-weight:600}
.user-chip-role{font-size:10px;color:var(--muted)}
.logout-btn{width:100%;margin-top:6px;padding:7px;background:var(--red-bg);border:1px solid rgba(220,38,38,0.15);color:var(--red);border-radius:7px;font-size:12px;font-weight:500;cursor:pointer;transition:all .15s}
.logout-btn:hover{background:rgba(220,38,38,0.14)}

/* Main */
.main{flex:1;padding:24px 28px;overflow-x:hidden}
.topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:22px;gap:12px;flex-wrap:wrap}
.topbar h1{font-size:20px;font-weight:700}
.topbar-right{display:flex;align-items:center;gap:10px}
.refresh-badge{display:flex;align-items:center;gap:6px;background:var(--surface);border:1px solid var(--border);border-radius:20px;padding:5px 12px;font-size:11px;color:var(--muted)}
.dot{width:6px;height:6px;border-radius:50%;background:#16a34a;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.export-btn{display:flex;align-items:center;gap:6px;background:var(--surface);border:1px solid var(--border);border-radius:20px;padding:5px 14px;font-size:12px;font-weight:500;color:var(--text);cursor:pointer;text-decoration:none;transition:all .15s}
.export-btn:hover{background:var(--surface2);border-color:var(--border2)}

/* Search */
.search-wrap{position:relative;margin-bottom:18px}
.search-wrap input{width:100%;padding:9px 14px 9px 36px;background:var(--surface);border:1px solid var(--border);border-radius:9px;font-size:13px;color:var(--text);font-family:inherit;outline:none;transition:border .15s}
.search-wrap input:focus{border-color:var(--accent)}
.search-icon{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:14px}

/* Stats */
.stat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px;margin-bottom:22px}
.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px}
.stat-label{font-size:10px;color:var(--muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em;font-weight:600}
.stat-value{font-size:28px;font-weight:800;line-height:1}
.stat-value.green{color:var(--green)}
.stat-value.red{color:var(--red)}
.stat-value.blue{color:var(--blue)}
.stat-value.yellow{color:var(--yellow)}
.stat-value.accent{color:var(--accent)}
.stat-value.purple{color:var(--purple)}

/* Two col */
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:22px}
@media(max-width:900px){.two-col{grid-template-columns:1fr}}

/* Cards */
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px}
.card h3{font-size:13px;font-weight:700;margin-bottom:14px;color:var(--text)}

/* Toggle tabs */
.toggle-tabs{display:flex;background:var(--surface2);border-radius:8px;padding:3px;gap:2px;margin-bottom:14px}
.toggle-btn{flex:1;padding:5px 10px;border-radius:6px;border:none;background:transparent;font-size:12px;font-weight:500;color:var(--muted);cursor:pointer;transition:all .15s;font-family:inherit}
.toggle-btn.active{background:var(--surface);color:var(--accent);box-shadow:0 1px 3px rgba(0,0,0,0.08)}

/* List rows */
.list-row{display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--border)}
.list-row:last-child{border-bottom:none}
.list-name{font-size:12px;font-weight:500;flex:1;color:var(--text)}
.list-count{font-size:12px;font-weight:700;color:var(--accent);background:var(--accent-light);padding:2px 9px;border-radius:20px}
.bar-wrap{flex:1.5;height:4px;background:var(--surface2);border-radius:2px;margin:0 8px}
.bar-fill{height:100%;border-radius:2px;background:var(--accent);transition:width .5s}
.medal{font-size:15px}

/* Filter tabs */
.filter-tabs{display:flex;gap:6px;flex-wrap:wrap}
.tab-btn{padding:5px 12px;border-radius:7px;font-size:12px;font-weight:500;border:1px solid var(--border);background:var(--surface);color:var(--muted);cursor:pointer;transition:all .15s;font-family:inherit}
.tab-btn:hover{color:var(--text);border-color:var(--border2)}
.tab-btn.active{background:var(--accent);border-color:var(--accent);color:#fff}

/* Table */
.section{margin-bottom:22px}
.section-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px}
.section-title{font-size:14px;font-weight:700}
.table-wrap{background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden}
table{width:100%;border-collapse:collapse;font-size:12px}
thead th{padding:10px 14px;text-align:left;color:var(--muted);font-weight:600;font-size:10px;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid var(--border);background:var(--surface2)}
tbody tr{border-bottom:1px solid var(--border);transition:background .1s;cursor:pointer}
tbody tr:last-child{border-bottom:none}
tbody tr:hover{background:var(--surface2)}
td{padding:10px 14px;vertical-align:middle}
.status-badge{display:inline-flex;align-items:center;padding:2px 9px;border-radius:20px;font-size:10px;font-weight:700;text-transform:uppercase}
.status-open{background:var(--blue-bg);color:var(--blue)}
.status-assigned{background:var(--yellow-bg);color:var(--yellow)}
.status-reported{background:var(--purple-bg);color:var(--purple)}
.status-done{background:var(--green-bg);color:var(--green)}
.status-missed{background:var(--red-bg);color:var(--red)}
.desc-cell{max-width:240px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--muted)}
.reassign-badge{display:inline-flex;padding:2px 7px;border-radius:20px;font-size:10px;font-weight:700;background:var(--purple-bg);color:var(--purple)}

/* Word tags */
.word-grid{display:flex;flex-wrap:wrap;gap:7px}
.word-tag{padding:4px 11px;border-radius:20px;font-size:12px;font-weight:600;background:var(--accent-light);color:var(--accent);border:1px solid rgba(99,102,241,0.15)}

/* Week stats */
.week-row{display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border);font-size:13px}
.week-row:last-child{border-bottom:none}
.week-val{font-weight:700}

/* Modal */
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.35);z-index:100;align-items:center;justify-content:center}
.modal-overlay.open{display:flex}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:28px;max-width:560px;width:90%;max-height:80vh;overflow-y:auto;position:relative}
.modal-close{position:absolute;top:16px;right:16px;background:var(--surface2);border:1px solid var(--border);border-radius:7px;width:28px;height:28px;cursor:pointer;font-size:16px;display:flex;align-items:center;justify-content:center;color:var(--muted)}
.modal h2{font-size:16px;font-weight:700;margin-bottom:16px;padding-right:36px}
.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px}
.detail-item{background:var(--surface2);border-radius:8px;padding:10px 12px}
.detail-label{font-size:10px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px}
.detail-val{font-size:13px;font-weight:600;color:var(--text)}
.detail-desc{background:var(--surface2);border-radius:8px;padding:12px;margin-bottom:12px}
.detail-desc label{font-size:10px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em;display:block;margin-bottom:6px}
.detail-desc p{font-size:13px;color:var(--text);line-height:1.5}
.notes-box{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:12px}
.notes-box label{font-size:10px;color:#92400e;font-weight:600;text-transform:uppercase;letter-spacing:.04em;display:block;margin-bottom:6px}
.notes-box p{font-size:13px;color:#78350f;line-height:1.5}

.loading{text-align:center;padding:32px;color:var(--muted);font-size:13px}
.page{display:none}
.page.active{display:block}
</style>
</head>
<body>
<div class="hero-bg"></div>
<div class="layout">

<aside class="sidebar">
  <div class="sidebar-logo">
    <span>🚛</span>
    <div><h2>Kurtex</h2><small>Alert Dashboard</small></div>
  </div>
  <nav>
    <a class="active" onclick="showPage('overview')"><span class="nav-icon">📊</span> Overview</a>
    <a onclick="showPage('cases')"><span class="nav-icon">📋</span> Cases</a>
    <a onclick="showPage('missed')"><span class="nav-icon">⚠️</span> Missed</a>
    <a onclick="showPage('reassigned')"><span class="nav-icon">🔁</span> Reassigned</a>
    <a onclick="showPage('leaderboard')"><span class="nav-icon">🏆</span> Leaderboard</a>
    <a onclick="showPage('analytics')"><span class="nav-icon">🔍</span> Analytics</a>
  </nav>
  <div class="sidebar-footer">
    <div class="user-chip">
      {% if user.photo_url %}
      <img src="{{ user.photo_url }}" alt="">
      {% else %}
      <div style="width:28px;height:28px;border-radius:50%;background:var(--accent-light);display:flex;align-items:center;justify-content:center;font-size:12px;color:var(--accent);font-weight:700;flex-shrink:0">{{ user.first_name[0] }}</div>
      {% endif %}
      <div><div class="user-chip-name">{{ user.first_name }}</div><div class="user-chip-role">Manager</div></div>
    </div>
    <button class="logout-btn" onclick="window.location='/logout'">Sign out</button>
  </div>
</aside>

<main class="main">
  <div class="topbar">
    <h1 id="page-title">Overview</h1>
    <div class="topbar-right">
      <a class="export-btn" href="/api/export">⬇ Export CSV</a>
      <div class="refresh-badge"><div class="dot"></div><span id="last-update">Loading...</span></div>
    </div>
  </div>

  <!-- Overview -->
  <div class="page active" id="page-overview">
    <div class="stat-grid" id="stat-grid"><div class="loading">Loading...</div></div>
    <div class="two-col">
      <div class="card"><h3>🏆 Top Assigned Today</h3><div id="lb-overview"></div></div>
      <div class="card"><h3>📡 Top Groups</h3><div id="groups-overview"></div></div>
    </div>
    <div class="section">
      <div class="section-header"><div class="section-title">Recent Cases</div></div>
      <div class="table-wrap" id="recent-table"><div class="loading">Loading...</div></div>
    </div>
  </div>

  <!-- Cases -->
  <div class="page" id="page-cases">
    <div class="search-wrap"><span class="search-icon">🔍</span><input type="text" id="cases-search" placeholder="Search reported by, group, assigned to..." oninput="onSearch()"></div>
    <div class="section">
      <div class="section-header">
        <div class="section-title">All Cases</div>
        <div class="filter-tabs">
          <button class="tab-btn active" onclick="setCaseFilter('today',this)">Today</button>
          <button class="tab-btn" onclick="setCaseFilter('week',this)">This Week</button>
          <button class="tab-btn" onclick="setCaseFilter('active',this)">Active</button>
          <button class="tab-btn" onclick="setCaseFilter('all',this)">All</button>
        </div>
      </div>
      <div class="table-wrap" id="cases-table"><div class="loading">Loading...</div></div>
    </div>
  </div>

  <!-- Missed -->
  <div class="page" id="page-missed">
    <div class="search-wrap"><span class="search-icon">🔍</span><input type="text" id="missed-search" placeholder="Search..." oninput="onSearchMissed()"></div>
    <div class="section">
      <div class="section-header"><div class="section-title">Missed Cases</div></div>
      <div class="table-wrap" id="missed-table"><div class="loading">Loading...</div></div>
    </div>
  </div>

  <!-- Reassigned -->
  <div class="page" id="page-reassigned">
    <div class="section">
      <div class="section-header"><div class="section-title">Reassigned Cases</div></div>
      <div class="table-wrap" id="reassigned-table"><div class="loading">Loading...</div></div>
    </div>
  </div>

  <!-- Leaderboard -->
  <div class="page" id="page-leaderboard">
    <div class="two-col">
      <div class="card">
        <h3>🏆 Leaderboard</h3>
        <div class="toggle-tabs">
          <button class="toggle-btn active" onclick="setLbPeriod('day',this)">Today</button>
          <button class="toggle-btn" onclick="setLbPeriod('week',this)">Week</button>
          <button class="toggle-btn" onclick="setLbPeriod('month',this)">Month</button>
        </div>
        <div id="leaderboard-full"></div>
      </div>
      <div class="card"><h3>📡 Cases by Group</h3><div id="group-bars-lb"></div></div>
    </div>
  </div>

  <!-- Analytics -->
  <div class="page" id="page-analytics">
    <div class="two-col">
      <div class="card">
        <h3>📊 Period Summary</h3>
        <div class="toggle-tabs">
          <button class="toggle-btn active" onclick="setAnalyticsPeriod('week',this)">Week</button>
          <button class="toggle-btn" onclick="setAnalyticsPeriod('month',this)">Month</button>
        </div>
        <div id="analytics-stats"></div>
      </div>
      <div class="card"><h3># Top Issue Keywords</h3><div id="word-cloud"></div></div>
    </div>
  </div>
</main>
</div>

<!-- Case Detail Modal -->
<div class="modal-overlay" id="modal-overlay" onclick="closeModal(event)">
  <div class="modal" id="modal-content">
    <button class="modal-close" onclick="closeModalBtn()">✕</button>
    <h2 id="modal-title">Case Detail</h2>
    <div id="modal-body"><div class="loading">Loading...</div></div>
  </div>
</div>

<script>
let stats = {};
let currentFilter = 'today';
let currentPage = 'overview';
let lbPeriod = 'day';
let analyticsPeriod = 'week';
let searchTimer = null;
const medals = ['🥇','🥈','🥉'];
const pages = ['overview','cases','missed','reassigned','leaderboard','analytics'];
const titles = {overview:'Overview',cases:'Cases',missed:'Missed Cases',reassigned:'Reassigned Cases',leaderboard:'Leaderboard',analytics:'Analytics'};

function showPage(page) {
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('nav a').forEach(a=>a.classList.remove('active'));
  document.getElementById('page-'+page).classList.add('active');
  document.querySelectorAll('nav a')[pages.indexOf(page)].classList.add('active');
  document.getElementById('page-title').textContent = titles[page];
  currentPage = page;
  refresh();
}

function setCaseFilter(f, btn) {
  currentFilter = f;
  document.querySelectorAll('#page-cases .tab-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  loadCases();
}

function setLbPeriod(p, btn) {
  lbPeriod = p;
  document.querySelectorAll('#page-leaderboard .toggle-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  renderLeaderboard();
}

function setAnalyticsPeriod(p, btn) {
  analyticsPeriod = p;
  document.querySelectorAll('#page-analytics .toggle-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  renderAnalytics();
}

function onSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadCases, 300);
}

function onSearchMissed() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadMissed, 300);
}

function statusBadge(s) {
  return `<span class="status-badge status-${s}">${s}</span>`;
}

function caseTable(cases) {
  if (!cases.length) return '<div class="loading">No cases found.</div>';
  return `<table><thead><tr>
    <th>Reported By</th><th>Group</th><th>Assigned To</th><th>Status</th><th>Opened</th><th>Response</th><th>Description</th>
  </tr></thead><tbody>${cases.map(c=>`<tr onclick="openCase('${c.full_id}')">
    <td><b>${c.driver}</b></td>
    <td style="color:var(--muted)">${c.group}</td>
    <td>${c.agent}</td>
    <td>${statusBadge(c.status)}${c.reassigned?' <span class="reassign-badge">reassigned</span>':''}</td>
    <td style="color:var(--muted);font-size:11px">${c.opened}</td>
    <td style="font-size:11px">${c.response}</td>
    <td class="desc-cell">${c.description}</td>
  </tr>`).join('')}</tbody></table>`;
}

function listRows(items, maxCount) {
  if (!items||!items.length) return '<div style="color:var(--muted);font-size:13px;padding:8px 0">No data yet</div>';
  return items.map((item,i)=>`
    <div class="list-row">
      <span class="medal">${medals[i]||((i+1)+'.')}</span>
      <span class="list-name">${item.name}</span>
      <div class="bar-wrap"><div class="bar-fill" style="width:${Math.round(item.count/(maxCount||1)*100)}%"></div></div>
      <span class="list-count">${item.count}</span>
    </div>`).join('');
}

async function loadStats() {
  try {
    const r = await fetch('/api/stats');
    if (r.status===401){window.location='/login';return;}
    stats = await r.json();
    const t = stats.today;
    document.getElementById('stat-grid').innerHTML = `
      <div class="stat-card"><div class="stat-label">Today Total</div><div class="stat-value accent">${t.total}</div></div>
      <div class="stat-card"><div class="stat-label">Assigned To</div><div class="stat-value yellow">${t.assigned}</div></div>
      <div class="stat-card"><div class="stat-label">Resolved</div><div class="stat-value green">${t.done}</div></div>
      <div class="stat-card"><div class="stat-label">Missed</div><div class="stat-value red">${t.missed}</div></div>
      <div class="stat-card"><div class="stat-label">Reassigned</div><div class="stat-value purple">${stats.reassigned_count}</div></div>
      <div class="stat-card"><div class="stat-label">Avg Response</div><div class="stat-value" style="font-size:18px;margin-top:4px">${stats.all_time.avg_resp}</div></div>
    `;
    const lb = stats.leaderboard_day.slice(0,5);
    document.getElementById('lb-overview').innerHTML = listRows(lb, lb[0]?.count||1);
    const grps = stats.top_groups;
    document.getElementById('groups-overview').innerHTML = listRows(grps, grps[0]?.count||1);
    renderLeaderboard();
    renderAnalytics();
    document.getElementById('word-cloud').innerHTML = stats.top_words.length
      ? `<div class="word-grid">${stats.top_words.map(w=>`<span class="word-tag">${w.word} <b>${w.count}</b></span>`).join('')}</div>`
      : '<div style="color:var(--muted);font-size:13px">No hashtag keywords yet</div>';
    const grpBars = stats.top_groups;
    document.getElementById('group-bars-lb').innerHTML = listRows(grpBars, grpBars[0]?.count||1);
  } catch(e){console.error(e);}
}

function renderLeaderboard() {
  if (!stats.leaderboard_day) return;
  const key = 'leaderboard_'+lbPeriod;
  const lb = stats[key] || [];
  document.getElementById('leaderboard-full').innerHTML = lb.length
    ? lb.map((a,i)=>`<div class="list-row"><span class="medal">${medals[i]||((i+1)+'.')}</span><span class="list-name">${a.name}</span><span class="list-count">${a.count} cases</span></div>`).join('')
    : '<div style="color:var(--muted);font-size:13px;padding:8px 0">No data</div>';
}

function renderAnalytics() {
  if (!stats.week) return;
  const d = analyticsPeriod==='week' ? stats.week : stats.month;
  const rate = d.total ? Math.round(d.done/d.total*100) : 0;
  document.getElementById('analytics-stats').innerHTML = `
    <div class="week-row"><span>Total Cases</span><span class="week-val">${d.total}</span></div>
    <div class="week-row"><span>Resolved</span><span class="week-val" style="color:var(--green)">${d.done}</span></div>
    <div class="week-row"><span>Missed</span><span class="week-val" style="color:var(--red)">${d.missed}</span></div>
    <div class="week-row"><span>Resolution Rate</span><span class="week-val">${rate}%</span></div>
    <div class="week-row"><span>All Time Total</span><span class="week-val">${stats.all_time.total}</span></div>
  `;
}

async function loadCases() {
  const search = document.getElementById('cases-search')?.value||'';
  const f = currentFilter;
  try {
    const r = await fetch(`/api/cases?filter=${f}&search=${encodeURIComponent(search)}`);
    if (r.status===401){window.location='/login';return;}
    const cases = await r.json();
    document.getElementById('cases-table').innerHTML = caseTable(cases);
  } catch(e){console.error(e);}
}

async function loadMissed() {
  const search = document.getElementById('missed-search')?.value||'';
  try {
    const r = await fetch(`/api/cases?filter=missed&search=${encodeURIComponent(search)}`);
    const cases = await r.json();
    document.getElementById('missed-table').innerHTML = caseTable(cases);
  } catch(e){console.error(e);}
}

async function loadReassigned() {
  try {
    const r = await fetch('/api/cases?filter=reassigned');
    const cases = await r.json();
    document.getElementById('reassigned-table').innerHTML = caseTable(cases);
  } catch(e){console.error(e);}
}

async function openCase(caseId) {
  document.getElementById('modal-overlay').classList.add('open');
  document.getElementById('modal-body').innerHTML = '<div class="loading">Loading...</div>';
  document.getElementById('modal-title').textContent = 'Case #'+caseId.slice(0,8);
  try {
    const r = await fetch('/api/case/'+caseId);
    const c = await r.json();
    document.getElementById('modal-title').textContent = `Case — ${c.driver}`;
    document.getElementById('modal-body').innerHTML = `
      <div class="detail-grid">
        <div class="detail-item"><div class="detail-label">Status</div><div class="detail-val">${statusBadge(c.status)}</div></div>
        <div class="detail-item"><div class="detail-label">Assigned To</div><div class="detail-val">${c.agent}</div></div>
        <div class="detail-item"><div class="detail-label">Group</div><div class="detail-val">${c.group}</div></div>
        <div class="detail-item"><div class="detail-label">Reported By</div><div class="detail-val">${c.driver}</div></div>
        <div class="detail-item"><div class="detail-label">Opened</div><div class="detail-val">${c.opened}</div></div>
        <div class="detail-item"><div class="detail-label">Assigned At</div><div class="detail-val">${c.assigned_at||'—'}</div></div>
        <div class="detail-item"><div class="detail-label">Response Time</div><div class="detail-val">${c.response}</div></div>
        <div class="detail-item"><div class="detail-label">Resolution Time</div><div class="detail-val">${c.resolution_secs||'—'}</div></div>
      </div>
      ${c.full_description?`<div class="detail-desc"><label>Issue Description</label><p>${c.full_description}</p></div>`:''}
      ${c.full_notes?`<div class="notes-box"><label>📋 Report / Notes</label><p>${c.full_notes}</p></div>`:''}
    `;
  } catch(e){document.getElementById('modal-body').innerHTML='<div class="loading">Error loading case.</div>';}
}

function closeModal(e) {
  if (e.target.id==='modal-overlay') closeModalBtn();
}
function closeModalBtn() {
  document.getElementById('modal-overlay').classList.remove('open');
}

async function refresh() {
  await loadStats();
  if (currentPage==='overview') {
    const r = await fetch('/api/cases?filter=today');
    const cases = await r.json();
    document.getElementById('recent-table').innerHTML = caseTable(cases.slice(0,10));
  } else if (currentPage==='cases') loadCases();
  else if (currentPage==='missed') loadMissed();
  else if (currentPage==='reassigned') loadReassigned();
  document.getElementById('last-update').textContent = 'Updated '+new Date().toLocaleTimeString();
}

refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>"""


@app.route("/login")
def login():
    error = request.args.get("error")
    return render_template_string(LOGIN_HTML, bot_username=get_bot_username(), error=error)


@app.route("/")
def index():
    if not session.get("user"):
        return redirect("/login")
    return render_template_string(DASHBOARD_HTML, user=session["user"])


def run_dashboard():
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
