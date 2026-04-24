"""
dashboard.py — Kurtex Alert Bot Web Dashboard v2
Mobile responsive, Lucide icons, calendar, dark/light toggle,
case timeline, print report, agent profile, login reworked.
"""

import csv, hashlib, hmac, io, json, logging, os, re, time
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

def verify_telegram_login(data):
    check_hash = data.pop("hash", "")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    computed   = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
    data["hash"] = check_hash
    if abs(time.time() - int(data.get("auth_date", 0))) > 86400:
        return False
    return hmac.compare_digest(computed, check_hash)

def get_bot_username():
    return os.getenv("BOT_USERNAME", "")

def load_cases():
    f = DATA_DIR / "cases.json"
    if not f.exists(): return []
    try: return json.loads(f.read_text(encoding="utf-8"))
    except: return []

def today_str():
    return datetime.now(timezone.utc).date().isoformat()

def week_start_str():
    now = datetime.now(timezone.utc)
    return (now - timedelta(days=now.weekday())).date().isoformat()

def month_start_str():
    return datetime.now(timezone.utc).date().replace(day=1).isoformat()

def fmt_dt(iso):
    if not iso: return "—"
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        return datetime.fromisoformat(iso).astimezone(et).strftime("%b %d %H:%M")
    except: return str(iso)[:16]

def fmt_secs(secs):
    if secs is None: return "—"
    secs = int(secs)
    if secs < 60: return f"{secs}s"
    if secs < 3600: return f"{secs//60}m {secs%60}s"
    return f"{secs//3600}h {(secs%3600)//60}m"

def serialize_case(c):
    try:
        return {
            "id":          (c.get("id") or "")[:8],
            "full_id":     c.get("id") or "",
            "driver":      c.get("driver_name") or "—",
            "group":       c.get("group_name") or "—",
            "agent":       c.get("agent_name") or "—",
            "status":      c.get("status") or "open",
            "opened":      fmt_dt(c.get("opened_at")),
            "closed":      fmt_dt(c.get("closed_at")),
            "opened_raw":  (c.get("opened_at") or "")[:10],
            "response":    fmt_secs(c.get("response_secs")),
            "description": (c.get("description") or "")[:200],
            "notes":       c.get("notes") or "",
            "reassigned":  bool(c.get("reassigned")),
        }
    except Exception as e:
        logger.error(f"serialize_case error: {e} — case: {c.get('id','?')}")
        return {"id":"?","full_id":"","driver":"—","group":"—","agent":"—",
                "status":"open","opened":"—","closed":"—","opened_raw":"",
                "response":"—","description":"","notes":"","reassigned":False}

# ── Auth ──────────────────────────────────────────────────────────────────────
@app.route("/auth/telegram")
def telegram_auth():
    data = dict(request.args)
    if not data.get("hash"): return redirect("/login?error=missing")
    if verify_telegram_login(data):
        user_id = int(data.get("id", 0))
        role = "agent"
        try:
            from storage.user_store import get_user
            u = get_user(user_id)
            if u: role = u.get("role", "agent")
        except Exception:
            pass
        session["user"] = {"id": user_id, "first_name": data.get("first_name",""),
                           "username": data.get("username",""), "photo_url": data.get("photo_url",""),
                           "role": role}
        return redirect("/")
    return redirect("/login?error=invalid")



@app.route("/api/fleet")
def api_fleet():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    cases = [c for c in load_cases() if c.get("vehicle_type")]  # only reported cases
    
    total = len(cases)
    truck_count   = sum(1 for c in cases if c.get("vehicle_type") == "truck")
    trailer_count = sum(1 for c in cases if c.get("vehicle_type") == "trailer")
    reefer_count  = sum(1 for c in cases if c.get("vehicle_type") == "reefer")

    # Top units (truck/trailer numbers)
    unit_counts = Counter(
        (c.get("unit_number","").strip(), c.get("vehicle_type",""))
        for c in cases if c.get("unit_number","").strip()
    )
    top_units = [{"unit": u, "vtype": vt, "count": cnt}
                 for (u, vt), cnt in unit_counts.most_common(10)]

    # Top drivers from reports
    driver_counts = Counter(
        c.get("report_driver","").strip()
        for c in cases if c.get("report_driver","").strip()
    )
    top_drivers = [{"unit": n, "vtype": "", "count": cnt}
                   for n, cnt in driver_counts.most_common(10)]

    # Top issues
    issue_counts = Counter(
        c.get("issue_text","").strip()[:40]
        for c in cases if c.get("issue_text","").strip()
    )
    top_issues = [{"unit": iss, "vtype": "", "count": cnt}
                  for iss, cnt in issue_counts.most_common(8)]

    # Load types
    load_counts = Counter(
        c.get("load_type","").strip()
        for c in cases if c.get("load_type","").strip()
    )
    load_types = [{"unit": lt, "vtype": "", "count": cnt}
                  for lt, cnt in load_counts.most_common(6)]

    return jsonify({
        "total_reports": total,
        "truck_count": truck_count,
        "trailer_count": trailer_count,
        "reefer_count": reefer_count,
        "top_units": top_units,
        "top_drivers": top_drivers,
        "top_issues": top_issues,
        "load_types": load_types,
    })

@app.route("/api/report")
def api_report():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    period = request.args.get("period", "today")
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    cases = load_cases()

    if period == "today":
        label = "Today — " + datetime.now().strftime("%B %d, %Y")
        cases = [c for c in cases if c.get("opened_at","").startswith(today_str())]
    elif period == "week":
        label = "This Week"
        cases = [c for c in cases if c.get("opened_at","") >= week_start_str()]
    elif period == "month":
        label = "This Month"
        cases = [c for c in cases if c.get("opened_at","") >= month_start_str()]
    elif period == "custom" and date_from:
        dt = date_to or today_str()
        label = f"{date_from} → {dt}"
        cases = [c for c in cases if date_from <= c.get("opened_at","")[:10] <= dt]
    else:
        label = "All Time"

    total = len(cases)
    done = sum(1 for c in cases if c["status"] == "done")
    missed_list = [c for c in cases if c["status"] == "missed"]
    assigned = sum(1 for c in cases if c["status"] in ("assigned","reported","done"))
    open_cases = sum(1 for c in cases if c["status"] == "open")
    rt = [c["response_secs"] for c in cases if c.get("response_secs")]
    avg_resp = int(sum(rt)/len(rt)) if rt else 0
    res_t = [c["resolution_secs"] for c in cases if c.get("resolution_secs")]
    avg_res = int(sum(res_t)/len(res_t)) if res_t else 0

    agent_counts = Counter(c["agent_name"] for c in cases if c.get("agent_name") and c["status"] in ("assigned","reported","done"))
    group_counts = Counter(c.get("group_name","Unknown") for c in cases)
    driver_counts = Counter(c.get("driver_name","Unknown") for c in cases)

    return jsonify({
        "label": label,
        "period": period,
        "total": total,
        "done": done,
        "missed": len(missed_list),
        "assigned": assigned,
        "open": open_cases,
        "avg_resp": fmt_secs(avg_resp),
        "avg_res": fmt_secs(avg_res),
        "rate": round(done/total*100) if total else 0,
        "leaderboard": [{"name":n,"count":v} for n,v in agent_counts.most_common(10)],
        "top_groups": [{"name":n,"count":v} for n,v in group_counts.most_common(5)],
        "top_drivers": [{"name":n,"count":v} for n,v in driver_counts.most_common(5)],
        "missed_cases": [serialize_case(c) for c in missed_list[:20]],
    })


@app.route("/api/my_profile")
def api_my_profile():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    user = session["user"]
    name = user.get("first_name","")
    username = user.get("username","")
    cases = load_cases()
    # Match by agent_name (first_name) or agent_username
    my_cases = [c for c in cases if
                c.get("agent_name","").lower() == name.lower() or
                (username and c.get("agent_username","") == username)]
    today = today_str(); wk = week_start_str()
    today_cases = [c for c in my_cases if c.get("opened_at","").startswith(today)]
    week_cases  = [c for c in my_cases if c.get("opened_at","") >= wk]
    total = len(my_cases); done = sum(1 for c in my_cases if c["status"]=="done")
    missed = sum(1 for c in my_cases if c["status"]=="missed")
    rt = [c["response_secs"] for c in my_cases if c.get("response_secs")]
    avg = int(sum(rt)/len(rt)) if rt else 0
    recent = sorted(my_cases, key=lambda c: c.get("opened_at",""), reverse=True)[:10]
    return jsonify({
        "name": name, "username": username, "role": user.get("role","agent"),
        "total": total, "done": done, "missed": missed,
        "avg_resp": fmt_secs(avg),
        "rate": round(done/total*100) if total else 0,
        "today_total": len(today_cases),
        "today_done": sum(1 for c in today_cases if c["status"]=="done"),
        "week_total": len(week_cases),
        "week_done": sum(1 for c in week_cases if c["status"]=="done"),
        "recent": [serialize_case(c) for c in recent],
    })

@app.route("/api/agents")
def api_agents():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    if session["user"].get("role","agent") not in ("developer","super_admin"):
        return jsonify({"error":"forbidden"}), 403
    try:
        cases = load_cases()
        try:
            from storage.user_store import get_all_user_dicts
            users = [u for u in get_all_user_dicts() if u["role"] in ("agent","super_admin")]
        except Exception as e:
            logger.error(f"get_all_user_dicts error: {e}")
            users = []
        result = []
        for u in users:
            name  = u["name"]
            uname = u.get("username","").lower()
            agent_cases = [c for c in cases if
                           c.get("agent_name","").lower() == name.lower() or
                           (uname and c.get("agent_username","").lower() == uname)]
            total  = len(agent_cases)
            done   = sum(1 for c in agent_cases if c["status"] == "done")
            missed = sum(1 for c in agent_cases if c["status"] == "missed")
            rt     = [c["response_secs"] for c in agent_cases if c.get("response_secs")]
            avg    = int(sum(rt)/len(rt)) if rt else 0
            result.append({
                "name":     name,
                "username": u.get("username",""),
                "total":    total,
                "done":     done,
                "missed":   missed,
                "avg_resp": fmt_secs(avg),
                "rate":     round(done/total*100) if total else 0,
                "open":     sum(1 for c in agent_cases if c.get("status") in ("open","assigned","reported")),
            })
        result.sort(key=lambda x: -x["total"])
        return jsonify(result)
    except Exception as e:
        logger.error(f"api_agents error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ── API ───────────────────────────────────────────────────────────────────────
@app.route("/api/stats")
def api_stats():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    cases = load_cases()
    today = today_str(); wk = week_start_str(); mo = month_start_str()
    tc = [c for c in cases if c.get("opened_at","").startswith(today)]
    wc = [c for c in cases if c.get("opened_at","") >= wk]
    mc = [c for c in cases if c.get("opened_at","") >= mo]
    st = Counter(c["status"] for c in tc)
    def lb(lst):
        cnt = Counter(c["agent_name"] for c in lst if c.get("agent_name") and c["status"] in ("assigned","reported","done"))
        return [{"name":n,"count":v} for n,v in cnt.most_common(10)]
    grps = Counter(c.get("group_name","Unknown") for c in cases)
    hashtags = re.findall(r'#\w+', " ".join(c.get("description","") for c in cases).lower())
    rt = [c["response_secs"] for c in cases if c.get("response_secs")]
    avg = int(sum(rt)/len(rt)) if rt else 0
    return jsonify({
        "today": {"total":len(tc),"open":st.get("open",0),"assigned":st.get("assigned",0)+st.get("reported",0),"done":st.get("done",0),"missed":st.get("missed",0)},
        "week":  {"total":len(wc),"done":sum(1 for c in wc if c["status"]=="done"),"missed":sum(1 for c in wc if c["status"]=="missed")},
        "month": {"total":len(mc),"done":sum(1 for c in mc if c["status"]=="done"),"missed":sum(1 for c in mc if c["status"]=="missed")},
        "all_time": {"total":len(cases),"done":sum(1 for c in cases if c["status"]=="done"),"avg_resp":fmt_secs(avg)},
        "leaderboard_day": lb(tc), "leaderboard_week": lb(wc), "leaderboard_month": lb(mc),
        "top_groups": [{"name":n,"count":v} for n,v in grps.most_common(5)],
        "top_words": [{"word":w,"count":v} for w,v in Counter(hashtags).most_common(15)],
        "reassigned_count": sum(1 for c in cases if c.get("reassigned")),
    })

@app.route("/api/cases")
def api_cases():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    f = request.args.get("filter","today")
    search = request.args.get("search","").lower().strip()
    date_filter = request.args.get("date","").strip()
    cases = load_cases()
    if date_filter:
        cases = [c for c in cases if c.get("opened_at","").startswith(date_filter)]
    elif f == "today":   cases = [c for c in cases if c.get("opened_at","").startswith(today_str())]
    elif f == "week":    cases = [c for c in cases if c.get("opened_at","") >= week_start_str()]
    elif f == "missed":  cases = [c for c in cases if c["status"] == "missed"]
    elif f == "active":  cases = [c for c in cases if c["status"] in ("open","assigned","reported")]
    elif f == "reassigned": cases = [c for c in cases if c.get("reassigned")]
    if search:
        cases = [c for c in cases if search in (c.get("driver_name") or "").lower()
                 or search in (c.get("group_name") or "").lower()
                 or search in (c.get("agent_name") or "").lower()
                 or search in (c.get("description") or "").lower()]
    cases = sorted(cases, key=lambda c: c.get("opened_at",""), reverse=True)[:200]
    return jsonify([serialize_case(c) for c in cases])

@app.route("/api/case")
def api_case_detail():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    case_id = request.args.get("id","").strip()
    if not case_id: return jsonify({"error":"no id"}), 400
    for c in load_cases():
        if c["id"] == case_id or c["id"].startswith(case_id):
            data = serialize_case(c)
            data.update({
                "full_description": c.get("description",""),
                "full_notes":       c.get("notes","") or "",
                "agent_username":   c.get("agent_username",""),
                "driver_username":  c.get("driver_username",""),
                "assigned_at":      fmt_dt(c.get("assigned_at")),
                "resolution_secs":  fmt_secs(c.get("resolution_secs")),
                "opened_at_raw":    c.get("opened_at",""),
                "vehicle_type":     c.get("vehicle_type",""),
                "unit_number":      c.get("unit_number",""),
                "report_driver":    c.get("report_driver",""),
                "issue_text":       c.get("issue_text",""),
                "load_type":        c.get("load_type",""),
                "priority":         c.get("priority",""),
            })
            return jsonify(data)
    logger.warning(f"Case not found: id={case_id!r}, total cases={len(load_cases())}")
    return jsonify({"error":"not found","id":case_id}), 404

@app.route("/api/agent")
def api_agent():
    agent_name = request.args.get("name","")
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    cases = [c for c in load_cases() if c.get("agent_name") == agent_name]
    total = len(cases); done = sum(1 for c in cases if c["status"]=="done")
    missed = sum(1 for c in cases if c["status"]=="missed")
    rt = [c["response_secs"] for c in cases if c.get("response_secs")]
    avg = int(sum(rt)/len(rt)) if rt else 0
    recent = sorted(cases, key=lambda c: c.get("opened_at",""), reverse=True)[:10]
    return jsonify({"name":agent_name,"total":total,"done":done,"missed":missed,
                    "avg_resp":fmt_secs(avg),"rate": round(done/total*100) if total else 0,
                    "recent":[serialize_case(c) for c in recent]})

@app.route("/api/export")
def api_export():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    cases = load_cases()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ID","Reported By","Reporter Username","Group","Assigned To","Status","Opened","Assigned","Closed","Response (s)","Resolution (s)","Description","Notes"])
    for c in sorted(cases, key=lambda x: x.get("opened_at",""), reverse=True):
        w.writerow([c["id"][:8],c.get("driver_name",""),c.get("driver_username",""),c.get("group_name",""),
                    c.get("agent_name",""),c.get("status",""),
                    (c.get("opened_at","") or "")[:16],(c.get("assigned_at","") or "")[:16],
                    (c.get("closed_at","") or "")[:16],c.get("response_secs",""),
                    c.get("resolution_secs",""),c.get("description",""),c.get("notes","")])
    out.seek(0)
    today = datetime.now().strftime("%Y-%m-%d")
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=kurtex-{today}.csv"})

# ── HTML ──────────────────────────────────────────────────────────────────────

FAVICON = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>
<rect width='32' height='32' rx='8' fill='#6366f1'/>
<path d='M4 20h2v2H4zm22 0h2v2h-2z' fill='white'/>
<rect x='3' y='12' width='16' height='10' rx='2' fill='white'/>
<path d='M19 15h5l3 4v3h-8z' fill='white'/>
<circle cx='7' cy='22' r='2' fill='#6366f1'/>
<circle cx='24' cy='22' r='2' fill='#6366f1'/>
</svg>"""

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Kurtex Dashboard</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🚛</text></svg>">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Plus Jakarta Sans',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;overflow:hidden;background:#0a0a0f}
.bg-video{position:fixed;inset:0;z-index:0}
.bg-img{position:absolute;inset:0;background:url('https://images.unsplash.com/photo-1473445730015-841f29a9490b?auto=format&fit=crop&w=1920&q=80')center/cover;opacity:.25;filter:grayscale(30%)}
.bg-overlay{position:absolute;inset:0;background:linear-gradient(135deg,rgba(10,10,15,.95) 0%,rgba(20,15,40,.85) 50%,rgba(10,10,15,.95) 100%)}
.particles{position:absolute;inset:0;overflow:hidden}
.particle{position:absolute;border-radius:50%;animation:float linear infinite;opacity:.4}
@keyframes float{0%{transform:translateY(100vh) rotate(0deg);opacity:0}10%{opacity:.4}90%{opacity:.4}100%{transform:translateY(-100px) rotate(720deg);opacity:0}}
.card{position:relative;z-index:1;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:24px;padding:48px 40px;text-align:center;width:100%;max-width:400px;backdrop-filter:blur(20px)}
.logo-ring{width:72px;height:72px;border-radius:18px;background:linear-gradient(135deg,#6366f1,#8b5cf6);display:flex;align-items:center;justify-content:center;margin:0 auto 20px;box-shadow:0 0 40px rgba(99,102,241,.4)}
.logo-ring svg{width:36px;height:36px}
h1{color:#fff;font-size:24px;font-weight:800;margin-bottom:8px;letter-spacing:-.3px}
.sub{color:rgba(255,255,255,.45);font-size:14px;margin-bottom:8px}
.divider{display:flex;align-items:center;gap:12px;margin:28px 0}
.divider-line{flex:1;height:1px;background:rgba(255,255,255,.08)}
.divider span{font-size:11px;color:rgba(255,255,255,.3);text-transform:uppercase;letter-spacing:.08em}
.tg-wrap{display:flex;justify-content:center}
.features{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:28px}
.feat{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.06);border-radius:10px;padding:10px;text-align:left}
.feat-icon{font-size:16px;margin-bottom:4px}
.feat-text{font-size:11px;color:rgba(255,255,255,.5);font-weight:500}
.error{color:#f87171;font-size:13px;margin-bottom:16px;background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.2);border-radius:8px;padding:8px 12px}
.badge{display:inline-flex;align-items:center;gap:6px;background:rgba(99,102,241,.15);border:1px solid rgba(99,102,241,.25);border-radius:20px;padding:4px 12px;font-size:11px;color:#a5b4fc;margin-bottom:20px}
.badge-dot{width:6px;height:6px;border-radius:50%;background:#6366f1;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
</style>
</head>
<body>
<div class="bg-video">
  <div class="bg-img"></div>
  <div class="bg-overlay"></div>
  <div class="particles" id="particles"></div>
</div>
<div class="card">
  <div class="logo-ring">🚛</div>
  <div class="badge"><div class="badge-dot"></div> Live Dashboard</div>
  <h1>Kurtex Dashboard</h1>
  <p class="sub">Truck Maintenance Command Center</p>
  {% if error %}<div class="error">Authentication failed. Please try again.</div>{% endif %}
  <div class="divider"><div class="divider-line"></div><span>Sign in with</span><div class="divider-line"></div></div>
  <div class="tg-wrap">
    <script async src="https://telegram.org/js/telegram-widget.js?22"
      data-telegram-login="{{ bot_username }}"
      data-size="large" data-radius="10"
      data-auth-url="/auth/telegram"
      data-request-access="write"></script>
  </div>
  <div class="features">
    <div class="feat"><div class="feat-icon">📊</div><div class="feat-text">Live Overview</div></div>
    <div class="feat"><div class="feat-icon">🏆</div><div class="feat-text">Leaderboards</div></div>
    <div class="feat"><div class="feat-icon">📅</div><div class="feat-text">Calendar View</div></div>
    <div class="feat"><div class="feat-icon">⬇️</div><div class="feat-text">CSV Export</div></div>
  </div>
</div>
<script>
const p = document.getElementById('particles');
for(let i=0;i<18;i++){
  const d = document.createElement('div');
  d.className='particle';
  const s = Math.random()*6+3;
  d.style.cssText=`width:${s}px;height:${s}px;left:${Math.random()*100}%;background:hsl(${240+Math.random()*40},70%,70%);animation-duration:${8+Math.random()*12}s;animation-delay:${Math.random()*8}s`;
  p.appendChild(d);
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
  else if (currentPage==='fleet') loadFleet();
  else if (currentPage==='my_profile') loadMyProfile();
  else if (currentPage==='agents') loadAgents();
  document.getElementById('last-update').textContent = 'Updated '+new Date().toLocaleTimeString();
}

refresh();
setInterval(refresh, 10000);

</script>
</body>
</html>"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Kurtex Dashboard</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🚛</text></svg>">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://unpkg.com/@phosphor-icons/web@2.1.1/src/index.js"></script>
<style>
:root{
  --bg:#f4f4f8;--surface:#fff;--surface2:#f0f0f4;--surface3:#e8e8ee;
  --border:#e2e2e8;--border2:#d0d0da;
  --text:#18181b;--muted:#6b7280;--muted2:#9ca3af;
  --accent:#6366f1;--accent-bg:rgba(99,102,241,.08);--accent-border:rgba(99,102,241,.2);
  --green:#16a34a;--green-bg:rgba(22,163,74,.08);
  --red:#dc2626;--red-bg:rgba(220,38,38,.08);
  --yellow:#ca8a04;--yellow-bg:rgba(202,138,4,.08);
  --blue:#2563eb;--blue-bg:rgba(37,99,235,.08);
  --purple:#7c3aed;--purple-bg:rgba(124,58,237,.08);
  --shadow:0 1px 4px rgba(0,0,0,.06),0 4px 16px rgba(0,0,0,.04);
}
[data-theme="dark"]{
  --bg:#0f0f14;--surface:#18181f;--surface2:#1e1e26;--surface3:#25252f;
  --border:rgba(255,255,255,.07);--border2:rgba(255,255,255,.12);
  --text:#f0f0f5;--muted:#8b8b9e;--muted2:#5a5a6e;
  --accent:#818cf8;--accent-bg:rgba(129,140,248,.1);--accent-border:rgba(129,140,248,.25);
  --green:#4ade80;--green-bg:rgba(74,222,128,.08);
  --red:#f87171;--red-bg:rgba(248,113,113,.08);
  --yellow:#fbbf24;--yellow-bg:rgba(251,191,36,.08);
  --blue:#60a5fa;--blue-bg:rgba(96,165,250,.08);
  --purple:#c084fc;--purple-bg:rgba(192,132,252,.08);
  --shadow:0 1px 4px rgba(0,0,0,.3),0 4px 16px rgba(0,0,0,.2);
}
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html{scroll-behavior:smooth}
body{font-family:'Plus Jakarta Sans',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;transition:background .2s,color .2s}
.hero-bg{position:fixed;inset:0;z-index:0;background:url('https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?auto=format&fit=crop&w=1920&q=80')center/cover no-repeat;opacity:.03;pointer-events:none}
.layout{position:relative;z-index:1;display:flex;min-height:100vh}

/* ── Sidebar ── */
.sidebar{width:220px;flex-shrink:0;background:var(--surface);border-right:1px solid var(--border);padding:20px 12px;position:sticky;top:0;height:100vh;display:flex;flex-direction:column;transition:transform .25s,background .2s;z-index:50}
.sidebar-logo{display:flex;align-items:center;gap:10px;margin-bottom:24px;padding:0 8px}
.logo-icon{width:32px;height:32px;border-radius:9px;background:var(--accent);display:flex;align-items:center;justify-content:center;flex-shrink:0}
.logo-icon svg{width:18px;height:18px;fill:white}
.logo-text h2{font-size:14px;font-weight:700}
.logo-text small{font-size:10px;color:var(--muted)}
nav{flex:1}
.nav-item{display:flex;align-items:center;gap:9px;padding:9px 10px;border-radius:9px;color:var(--muted);font-size:13px;font-weight:500;cursor:pointer;transition:all .15s;margin-bottom:2px;position:relative;text-decoration:none}
.nav-item:hover{background:var(--surface2);color:var(--text)}
.nav-item.active{background:var(--accent-bg);color:var(--accent)}
.nav-item i{font-size:15px;width:18px;text-align:center;flex-shrink:0}
.nav-badge{margin-left:auto;background:var(--red);color:white;font-size:10px;font-weight:700;padding:1px 6px;border-radius:20px;min-width:18px;text-align:center}
.sidebar-footer{padding-top:14px;border-top:1px solid var(--border)}
.user-chip{display:flex;align-items:center;gap:8px;padding:8px 8px;border-radius:10px;background:var(--surface2);margin-bottom:8px}
.user-avatar{width:30px;height:30px;border-radius:50%;border:2px solid var(--border);flex-shrink:0;object-fit:cover}
.user-avatar-init{width:30px;height:30px;border-radius:50%;background:var(--accent-bg);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:var(--accent);flex-shrink:0}
.user-name{font-size:12px;font-weight:600}
.user-role{font-size:10px;color:var(--muted)}
.theme-btn{width:100%;padding:7px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--muted);font-size:12px;font-weight:500;cursor:pointer;transition:all .15s;font-family:inherit;display:flex;align-items:center;gap:7px;margin-bottom:6px}
.theme-btn:hover{color:var(--text);border-color:var(--border2)}
.logout-btn{width:100%;padding:7px;background:var(--red-bg);border:1px solid rgba(220,38,38,.15);color:var(--red);border-radius:8px;font-size:12px;font-weight:500;cursor:pointer;font-family:inherit;display:flex;align-items:center;justify-content:center;gap:6px;transition:all .15s}
.logout-btn:hover{background:rgba(220,38,38,.14)}

/* Mobile header */
.mobile-header{display:none;position:sticky;top:0;z-index:40;background:var(--surface);border-bottom:1px solid var(--border);padding:12px 16px;align-items:center;justify-content:space-between}
.mobile-logo{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:700}
.mobile-logo .logo-icon{width:28px;height:28px;border-radius:7px}
.hamburger{background:var(--surface2);border:1px solid var(--border);border-radius:8px;width:34px;height:34px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--text)}
.sidebar-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:49}

/* Main */
.main{flex:1;padding:22px 24px;overflow-x:hidden;min-width:0}
.topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;gap:10px;flex-wrap:wrap}
.topbar h1{font-size:18px;font-weight:700}
.topbar-right{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.badge-btn{display:flex;align-items:center;gap:5px;background:var(--surface);border:1px solid var(--border);border-radius:20px;padding:5px 12px;font-size:11px;color:var(--muted);cursor:pointer;transition:all .15s;text-decoration:none;font-family:inherit;font-weight:500}
.badge-btn:hover{border-color:var(--border2);color:var(--text)}
.badge-btn i{font-size:13px}
.dot{width:6px;height:6px;border-radius:50%;background:var(--green);animation:pulse 2s infinite;flex-shrink:0}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}

/* Stats */
.stat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px;margin-bottom:20px}
.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px;box-shadow:var(--shadow)}
.stat-label{font-size:10px;color:var(--muted);margin-bottom:5px;text-transform:uppercase;letter-spacing:.05em;font-weight:600}
.stat-value{font-size:26px;font-weight:800;line-height:1}
.v-accent{color:var(--accent)}.v-green{color:var(--green)}.v-red{color:var(--red)}
.v-yellow{color:var(--yellow)}.v-blue{color:var(--blue)}.v-purple{color:var(--purple)}
.v-sm{font-size:17px!important;margin-top:4px}

/* Two col */
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px}
@media(max-width:768px){.two-col{grid-template-columns:1fr}}

/* Card */
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px;box-shadow:var(--shadow)}
.card-title{font-size:13px;font-weight:700;margin-bottom:12px;display:flex;align-items:center;gap:7px}
.card-title i{font-size:14px;color:var(--accent)}

/* Toggle */
.toggle-tabs{display:flex;background:var(--surface2);border-radius:8px;padding:3px;gap:2px;margin-bottom:12px}
.toggle-btn{flex:1;padding:5px 8px;border-radius:6px;border:none;background:transparent;font-size:12px;font-weight:500;color:var(--muted);cursor:pointer;transition:all .15s;font-family:inherit}
.toggle-btn.active{background:var(--surface);color:var(--accent);box-shadow:0 1px 3px rgba(0,0,0,.08)}

/* Filter tabs */
.filter-tabs{display:flex;gap:6px;flex-wrap:wrap}
.tab-btn{padding:5px 11px;border-radius:7px;font-size:12px;font-weight:500;border:1px solid var(--border);background:var(--surface);color:var(--muted);cursor:pointer;transition:all .15s;font-family:inherit}
.tab-btn:hover{color:var(--text);border-color:var(--border2)}
.tab-btn.active{background:var(--accent);border-color:var(--accent);color:#fff}

/* List */
.list-row{display:flex;align-items:center;gap:7px;padding:7px 0;border-bottom:1px solid var(--border)}
.list-row:last-child{border-bottom:none}
.list-name{font-size:12px;font-weight:500;flex:1;color:var(--text)}
.list-count{font-size:12px;font-weight:700;color:var(--accent);background:var(--accent-bg);padding:2px 9px;border-radius:20px;flex-shrink:0}
.bar-wrap{flex:1.5;height:4px;background:var(--surface3);border-radius:2px;margin:0 6px}
.bar-fill{height:100%;border-radius:2px;background:var(--accent);transition:width .5s}
.medal{font-size:14px;flex-shrink:0;width:20px}

/* Section */
.section{margin-bottom:20px}
.section-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;flex-wrap:wrap;gap:8px}
.section-title{font-size:13px;font-weight:700}

/* Search */
.search-wrap{position:relative;margin-bottom:14px}
.search-wrap input{width:100%;padding:9px 14px 9px 38px;background:var(--surface);border:1px solid var(--border);border-radius:9px;font-size:13px;color:var(--text);font-family:inherit;outline:none;transition:border .15s}
.search-wrap input:focus{border-color:var(--accent)}
.search-wrap i{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:15px}


/* Table */
.table-wrap{background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden;box-shadow:var(--shadow)}
.table-scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12px;min-width:600px}
thead th{padding:9px 12px;text-align:left;color:var(--muted);font-weight:600;font-size:10px;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid var(--border);background:var(--surface2);white-space:nowrap}
tbody tr{border-bottom:1px solid var(--border);transition:background .1s;cursor:pointer}
tbody tr:last-child{border-bottom:none}
tbody tr:hover{background:var(--surface2)}
td{padding:9px 12px;vertical-align:middle}
.status-badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;text-transform:uppercase;white-space:nowrap}
.s-open{background:var(--blue-bg);color:var(--blue)}
.s-assigned{background:var(--yellow-bg);color:var(--yellow)}
.s-reported{background:var(--purple-bg);color:var(--purple)}
.s-done{background:var(--green-bg);color:var(--green)}
.s-missed{background:var(--red-bg);color:var(--red)}
.reassign-badge{display:inline-flex;padding:2px 6px;border-radius:20px;font-size:10px;font-weight:700;background:var(--purple-bg);color:var(--purple);margin-left:4px}
.desc-cell{max-width:200px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--muted)}

/* Word tags */
.word-grid{display:flex;flex-wrap:wrap;gap:7px}
.word-tag{padding:4px 11px;border-radius:20px;font-size:12px;font-weight:600;background:var(--accent-bg);color:var(--accent);border:1px solid var(--accent-border)}

/* Week stats */
.stats-list .row{display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border);font-size:13px}
.stats-list .row:last-child{border-bottom:none}
.stats-list .val{font-weight:700}

/* Modal */
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:300;align-items:center;justify-content:center;padding:16px}
.modal-overlay.open{display:flex}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:28px;max-width:900px;width:100%;max-height:90vh;overflow-y:auto;position:relative;box-shadow:0 8px 40px rgba(0,0,0,.15)}
.modal-close{position:absolute;top:14px;right:14px;background:var(--surface2);border:1px solid var(--border);border-radius:7px;width:28px;height:28px;cursor:pointer;font-size:16px;display:flex;align-items:center;justify-content:center;color:var(--muted);transition:all .15s}
.modal-close:hover{background:var(--surface3)}
.modal h2{font-size:16px;font-weight:700;margin-bottom:16px;padding-right:40px}
.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px}
@media(max-width:480px){.detail-grid{grid-template-columns:1fr}}
.detail-item{background:var(--surface2);border-radius:8px;padding:10px 12px}
.detail-label{font-size:10px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px}
.detail-val{font-size:13px;font-weight:600}
.desc-box,.notes-box{border-radius:8px;padding:12px;margin-bottom:10px}
.desc-box{background:var(--surface2)}
.notes-box{background:#fffbeb;border:1px solid #fde68a}
[data-theme="dark"] .notes-box{background:rgba(251,191,36,.06);border-color:rgba(251,191,36,.2)}
.box-label{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;display:block;margin-bottom:6px;color:var(--muted)}
.notes-box .box-label{color:#92400e}
[data-theme="dark"] .notes-box .box-label{color:var(--yellow)}
.box-text{font-size:13px;line-height:1.6;color:var(--text)}
.notes-box .box-text{color:#78350f}
[data-theme="dark"] .notes-box .box-text{color:var(--yellow)}

/* Timeline */
.timeline{display:flex;align-items:center;gap:0;margin-bottom:16px;padding:14px;background:var(--surface2);border-radius:10px}
.tl-step{display:flex;flex-direction:column;align-items:center;flex:1;position:relative}
.tl-step:not(:last-child)::after{content:'';position:absolute;top:12px;left:calc(50% + 12px);width:calc(100% - 24px);height:2px;background:var(--border)}
.tl-step.done-step::after{background:var(--accent)}
.tl-dot{width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;border:2px solid var(--border);background:var(--surface);z-index:1;position:relative}
.tl-dot.active{border-color:var(--accent);background:var(--accent);color:#fff}
.tl-dot.done{border-color:var(--green);background:var(--green);color:#fff}
.tl-label{font-size:9px;color:var(--muted);margin-top:5px;font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.tl-time{font-size:9px;color:var(--muted2);margin-top:2px}

/* Agent profile modal */
.agent-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:16px}
@media(max-width:480px){.agent-stats{grid-template-columns:repeat(2,1fr)}}
.agent-stat{background:var(--surface2);border-radius:8px;padding:10px;text-align:center}
.agent-stat-val{font-size:22px;font-weight:800;color:var(--accent)}
.agent-stat-label{font-size:10px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-top:2px}

/* Print */
@media print{
  .sidebar,.mobile-header,.topbar-right,.modal-overlay{display:none!important}
  .main{padding:0}
  .hero-bg{display:none}
  body{background:white;color:black}
  .stat-card,.card,.table-wrap{box-shadow:none;border:1px solid #ddd}
}


/* Report modal */
.report-modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:300;align-items:flex-start;justify-content:center;padding:20px;overflow-y:auto}
.report-modal-overlay.open{display:flex}
.report-modal{background:var(--surface);border:1px solid var(--border);border-radius:16px;width:100%;max-width:700px;margin:auto;position:relative}
.report-header{padding:20px 24px 0;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}
.report-header h2{font-size:17px;font-weight:700;display:flex;align-items:center;gap:8px}
.report-header h2 i{color:var(--accent);font-size:16px}
.report-tabs{display:flex;gap:0;background:var(--surface2);border-radius:8px;padding:3px}
.report-tab{padding:5px 16px;border-radius:6px;border:none;background:transparent;font-size:12px;font-weight:500;color:var(--muted);cursor:pointer;font-family:inherit;transition:all .15s}
.report-tab.active{background:var(--surface);color:var(--accent);box-shadow:0 1px 3px rgba(0,0,0,.08)}
.report-close{background:var(--surface2);border:1px solid var(--border);border-radius:7px;width:28px;height:28px;cursor:pointer;display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:16px;flex-shrink:0}
.report-body{padding:20px 24px}
.report-period-bar{display:flex;align-items:center;gap:8px;margin-bottom:16px;flex-wrap:wrap}
.report-period-bar select,.report-period-bar input{padding:7px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;font-size:12px;color:var(--text);font-family:inherit;outline:none}
.report-period-bar select:focus,.report-period-bar input:focus{border-color:var(--accent)}
.report-generate-btn{padding:7px 16px;background:var(--accent);color:#fff;border:none;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;font-family:inherit;transition:all .15s}
.report-generate-btn:hover{opacity:.9}
.report-title{font-size:18px;font-weight:800;margin-bottom:4px}
.report-subtitle{font-size:12px;color:var(--muted);margin-bottom:18px}
.report-stat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:8px;margin-bottom:18px}
.report-stat{background:var(--surface2);border-radius:10px;padding:12px;text-align:center}
.report-stat-val{font-size:24px;font-weight:800;line-height:1}
.report-stat-label{font-size:10px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-top:3px}
.report-section{margin-bottom:16px}
.report-section h3{font-size:12px;font-weight:700;margin-bottom:8px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.report-row{display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);font-size:12px}
.report-row:last-child{border-bottom:none}
.report-row .name{flex:1;font-weight:500}
.report-row .count{font-weight:700;color:var(--accent);background:var(--accent-bg);padding:1px 8px;border-radius:20px}
.report-footer{padding:12px 24px;border-top:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
.report-footer .ts{font-size:11px;color:var(--muted)}
.print-report-btn{display:flex;align-items:center;gap:6px;background:var(--accent);color:#fff;border:none;border-radius:8px;padding:7px 14px;font-size:12px;font-weight:600;cursor:pointer;font-family:inherit;transition:all .15s}
.print-report-btn:hover{opacity:.9}
@media print{
  body > *:not(.report-modal-overlay){display:none!important}
  .report-modal-overlay{position:static!important;background:none!important;display:block!important;padding:0!important}
  .report-modal{box-shadow:none!important;border:none!important;max-width:100%!important}
  .report-close,.report-footer .print-report-btn,.report-tabs,.report-header .badge-btn,.report-period-bar{display:none!important}
  .report-header{border-bottom:1px solid #ddd!important}
}
.loading{text-align:center;padding:28px;color:var(--muted);font-size:13px}
.page{display:none}
.page.active{display:block}
.empty-state{text-align:center;padding:40px;color:var(--muted)}
.empty-state i{font-size:32px;display:block;margin-bottom:8px;opacity:.4}

/* Responsive */
@media(max-width:768px){
  /* Sidebar becomes drawer */
  .sidebar{position:fixed;left:0;top:0;height:100vh;transform:translateX(-100%);z-index:50;width:240px;transition:transform .25s}
  .sidebar.open{transform:translateX(0);box-shadow:4px 0 24px rgba(0,0,0,.15)}
  .sidebar-overlay.open{display:block}
  .mobile-header{display:flex}

  /* Main content */
  .main{padding:12px 12px 80px}
  .layout{display:block}

  /* Topbar */
  .topbar{margin-bottom:12px;gap:8px}
  .topbar h1{font-size:15px}
  .topbar-right{gap:6px}
  .badge-btn{padding:4px 9px;font-size:11px}
  .badge-btn span{display:none}

  /* Stats */
  .stat-grid{grid-template-columns:repeat(2,1fr);gap:8px}
  .stat-value{font-size:22px}

  /* Two col becomes one */
  .two-col{grid-template-columns:1fr;gap:10px}

  /* Table - horizontal scroll */
  .table-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
  table{min-width:500px}

  /* Filter tabs wrap */
  .filter-tabs{gap:4px}
  .tab-btn{padding:4px 9px;font-size:11px}

  /* Section header stacks */
  .section-header{flex-direction:column;align-items:flex-start;gap:8px}

  /* Modal full screen on mobile */
  .modal-overlay{padding:0;align-items:flex-end}
  .modal{border-radius:16px 16px 0 0;max-height:92vh;max-width:100%;border-bottom:none}
  .report-modal{border-radius:16px 16px 0 0;max-width:100%}
  .report-modal-overlay{padding:0;align-items:flex-end}

  /* Detail grid single col */
  .detail-grid{grid-template-columns:1fr}
  .agent-stats{grid-template-columns:repeat(2,1fr)}

  /* Timeline compact */
  .timeline{padding:10px 8px}
  .tl-label{font-size:8px}
  .tl-time{font-size:8px}

  /* Search */
  .search-wrap input{font-size:14px}

  /* Cards */
  .card{padding:14px}

  /* Agent grid single col on small screens */
  #agents-content > div{grid-template-columns:1fr!important}
}

@media(max-width:480px){
  .stat-grid{grid-template-columns:repeat(2,1fr)}
  .topbar-right .badge-btn:not(:last-child){display:none}
  .detail-grid{grid-template-columns:1fr}
  table{min-width:420px}
}
</style>
</head>
<body>
<div class="hero-bg"></div>

<!-- Mobile header -->
<div class="mobile-header">
  <div class="mobile-logo">
    <div class="logo-icon">🚛</div>
    Kurtex
  </div>
  <div onclick="toggleSidebar()" class="hamburger"><i class="ph ph-list"></i></div>
</div>
<div class="sidebar-overlay" id="sidebar-overlay" onclick="closeSidebar()"></div>

<div class="layout">
<aside class="sidebar" id="sidebar">
  <div class="sidebar-logo">
    <div class="logo-icon">🚛</div>
    <div class="logo-text"><h2>Kurtex</h2><small>Alert Dashboard</small></div>
  </div>
  <nav>
    <div class="nav-item active" onclick="showPage('overview')"><i class="ph ph-squares-four"></i> Overview</div>
    <div class="nav-item" onclick="showPage('cases')"><i class="ph ph-clipboard-text"></i> Cases</div>
    <div class="nav-item" onclick="showPage('missed')"><i class="ph ph-warning"></i> Missed <span class="nav-badge" id="missed-badge" style="display:none"></span></div>
    <div class="nav-item" onclick="showPage('reassigned')"><i class="ph ph-arrows-clockwise"></i> Reassigned</div>
    <div class="nav-item" onclick="showPage('leaderboard')"><i class="ph ph-trophy"></i> Leaderboard</div>
    <div class="nav-item" onclick="showPage('analytics')"><i class="ph ph-chart-bar"></i> Analytics</div>
    <div class="nav-item" onclick="showPage('fleet')"><i class="ph ph-truck"></i> Fleet Stats</div>
    <div class="nav-item" onclick="showPage('my_profile')"><i class="ph ph-user"></i> My Profile</div>
    {% if is_manager %}<div class="nav-item" onclick="showPage('agents')"><i class="ph ph-users"></i> Agents</div>{% endif %}
  </nav>
  <div class="sidebar-footer">
    <div class="user-chip">
      {% if user.photo_url %}<img class="user-avatar" src="{{ user.photo_url }}" alt="">
      {% else %}<div class="user-avatar-init">{{ user.first_name[0] }}</div>{% endif %}
      <div><div class="user-name">{{ user.first_name }}</div><div class="user-role">Manager</div></div>
    </div>
    <button class="theme-btn" onclick="toggleTheme()"><i class="ph ph-sun" id="theme-icon"></i> <span id="theme-label">Light Mode</span></button>
    <button class="logout-btn" onclick="window.location='/logout'"><i class="ph ph-sign-out"></i> Sign out</button>
  </div>
</aside>

<main class="main">
  <div class="topbar">
    <h1 id="page-title">Overview</h1>
    <div class="topbar-right">
      <button class="badge-btn" onclick="openReport()"><i class="ph ph-file-text"></i> Report</button>
      <button class="badge-btn" onclick="window.print()"><i class="ph ph-printer"></i> Print</button>
      <a class="badge-btn" href="/api/export"><i class="ph ph-download-simple"></i> Export CSV</a>
      <div class="badge-btn"><div class="dot"></div><span id="last-update">Loading...</span></div>
    </div>
  </div>

  <!-- Overview -->
  <div class="page active" id="page-overview">
    <div class="stat-grid" id="stat-grid"><div class="loading">Loading...</div></div>
    <div class="two-col">
      <div class="card"><div class="card-title"><i class="ph ph-trophy"></i>Top Assigned Today</div><div id="lb-overview"></div></div>
      <div class="card"><div class="card-title"><i class="ph ph-broadcast"></i>Top Groups</div><div id="groups-overview"></div></div>
    </div>
    <div class="section">
      <div class="section-header"><div class="section-title">Recent Cases</div></div>
      <div class="table-wrap"><div class="table-scroll" id="recent-table"><div class="loading">Loading...</div></div></div>
    </div>
  </div>

  <!-- Cases -->
  <div class="page" id="page-cases">
    <div class="search-wrap"><i class="ph ph-magnifying-glass"></i><input type="text" id="cases-search" placeholder="Search reported by, group, assigned to..." oninput="onSearch('cases')"></div>
    <div class="section">
      <div class="section-header">
        <div class="section-title">All Cases</div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <div class="filter-tabs">
            <button class="tab-btn active" onclick="setCaseFilter('today',this)">Today</button>
            <button class="tab-btn" onclick="setCaseFilter('week',this)">This Week</button>
            <button class="tab-btn" onclick="setCaseFilter('active',this)">Active</button>
            <button class="tab-btn" onclick="setCaseFilter('all',this)">All</button>
          </div>
          <div style="display:flex;align-items:center;gap:6px">
            <input type="date" id="cases-date-picker" style="padding:5px 10px;background:var(--surface);border:1px solid var(--border);border-radius:7px;font-size:12px;color:var(--text);font-family:inherit;outline:none;cursor:pointer" onchange="setCaseDateFilter(this.value)">
            <button class="tab-btn" id="cases-date-clear" onclick="clearDateFilter()" style="display:none;padding:5px 8px">✕</button>
          </div>
        </div>
      </div>
      <div class="table-wrap"><div class="table-scroll" id="cases-table"><div class="loading">Loading...</div></div></div>
    </div>
  </div>

  <!-- Missed -->
  <div class="page" id="page-missed">
    <div class="search-wrap"><i class="ph ph-magnifying-glass"></i><input type="text" id="missed-search" placeholder="Search..." oninput="onSearch('missed')"></div>
    <div class="section">
      <div class="section-header"><div class="section-title">Missed Cases</div></div>
      <div class="table-wrap"><div class="table-scroll" id="missed-table"><div class="loading">Loading...</div></div></div>
    </div>
  </div>

  <!-- Reassigned -->
  <div class="page" id="page-reassigned">
    <div class="section">
      <div class="section-header"><div class="section-title">Reassigned Cases</div></div>
      <div class="table-wrap"><div class="table-scroll" id="reassigned-table"><div class="loading">Loading...</div></div></div>
    </div>
  </div>

  <!-- Leaderboard -->
  <div class="page" id="page-leaderboard">
    <div class="two-col">
      <div class="card">
        <div class="card-title"><i class="ph ph-trophy"></i>Agent Leaderboard</div>
        <div class="toggle-tabs">
          <button class="toggle-btn active" onclick="setLbPeriod('day',this)">Today</button>
          <button class="toggle-btn" onclick="setLbPeriod('week',this)">Week</button>
          <button class="toggle-btn" onclick="setLbPeriod('month',this)">Month</button>
        </div>
        <div id="leaderboard-full"></div>
      </div>
      <div class="card"><div class="card-title"><i class="ph ph-broadcast"></i>Cases by Group</div><div id="group-bars-lb"></div></div>
    </div>
  </div>

  <!-- Analytics -->
  <div class="page" id="page-analytics">
    <div class="two-col">
      <div class="card">
        <div class="card-title"><i class="ph ph-chart-bar"></i>Period Summary</div>
        <div class="toggle-tabs">
          <button class="toggle-btn active" onclick="setAnalyticsPeriod('week',this)">Week</button>
          <button class="toggle-btn" onclick="setAnalyticsPeriod('month',this)">Month</button>
        </div>
        <div class="stats-list" id="analytics-stats"></div>
      </div>
      <div class="card"><div class="card-title"><i class="ph ph-hash"></i>Top Issue Keywords</div><div id="word-cloud"></div></div>
    </div>
  </div>

  <!-- My Profile -->
  <div class="page" id="page-my_profile">
    <div id="my-profile-content"><div class="loading">Loading...</div></div>
  </div>

  <!-- Fleet Stats -->
  <div class="page" id="page-fleet">
    <div id="fleet-content"><div class="loading">Loading fleet stats...</div></div>
  </div>

  <!-- Agent Profiles (manager only) -->
  <div class="page" id="page-agents">
    <div id="agents-content"><div class="loading">Loading...</div></div>
  </div>
</main>
</div>

<!-- Case Modal -->
<div class="modal-overlay" id="modal-overlay" onclick="closeModalOutside(event)">
<div class="modal" id="modal-content">
  <button class="modal-close" onclick="closeModal()"><i class="ph ph-x"></i></button>
  <h2 id="modal-title">Case Detail</h2>
  <div id="modal-body"><div class="loading">Loading...</div></div>
</div>
</div>


<!-- Report Modal -->
<div class="report-modal-overlay" id="report-modal-overlay" onclick="closeReportOutside(event)">
<div class="report-modal" id="report-modal">
  <div class="report-header">
    <h2><i class="ph ph-file-text"></i>Report</h2>
    <div style="display:flex;align-items:center;gap:8px">
      <div class="report-tabs">
        <button class="report-tab active" onclick="setReportTab('today',this)">Today</button>
        <button class="report-tab" onclick="setReportTab('custom',this)">Custom</button>
      </div>
      <button class="report-close" onclick="closeReport()"><i class="ph ph-x"></i></button>
    </div>
  </div>
  <div class="report-body">
    <div id="report-period-bar" style="display:none" class="report-period-bar">
      <select id="report-period-select" onchange="toggleCustomDates()">
        <option value="week">This Week</option>
        <option value="month">This Month</option>
        <option value="custom">Custom Range</option>
      </select>
      <div id="custom-date-inputs" style="display:none;align-items:center;gap:6px">
        <input type="date" id="report-date-from">
        <span style="color:var(--muted);font-size:12px">to</span>
        <input type="date" id="report-date-to">
      </div>
      <button class="report-generate-btn" onclick="generateReport()">Generate</button>
    </div>
    <div id="report-content"><div class="loading">Loading report...</div></div>
  </div>
  <div class="report-footer">
    <span class="ts" id="report-ts"></span>
    <button class="print-report-btn" onclick="printReport()"><i class="ph ph-printer"></i> Print Report</button>
  </div>
</div>
</div>
<!-- Agent Modal -->
<div class="modal-overlay" id="agent-modal-overlay" style="z-index:200" onclick="closeAgentModalOutside(event)">
<div class="modal" id="agent-modal-content">
  <button class="modal-close" onclick="closeAgentModal()"><i class="ph ph-x"></i></button>
  <h2 id="agent-modal-title">Agent Profile</h2>
  <div id="agent-modal-body"><div class="loading">Loading...</div></div>
</div>
</div>

<script>
let stats = {};
let currentFilter = 'today';
let currentPage = 'overview';
let lbPeriod = 'day';
let analyticsPeriod = 'week';
let searchTimers = {};
let currentDateFilter = '';
const medals = ['🥇','🥈','🥉'];
const pages = ['overview','cases','missed','reassigned','leaderboard','analytics','fleet','my_profile','agents'];
const titles = {overview:'Overview',cases:'Cases',missed:'Missed Cases',reassigned:'Reassigned Cases',leaderboard:'Leaderboard',analytics:'Analytics',fleet:'Fleet Stats',my_profile:'My Profile',agents:'Agent Profiles'};

// Theme
let isDark = localStorage.getItem('theme') === 'dark';
function applyTheme() {
  document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
  document.getElementById('theme-icon').className = isDark ? 'ph ph-moon' : 'ph ph-sun';
  document.getElementById('theme-label').textContent = isDark ? 'Dark Mode' : 'Light Mode';
}
function toggleTheme() { isDark = !isDark; localStorage.setItem('theme', isDark?'dark':'light'); applyTheme(); }
applyTheme();

// Sidebar mobile
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sidebar-overlay').classList.toggle('open');
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebar-overlay').classList.remove('open');
}

function showPage(page) {
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(a=>a.classList.remove('active'));
  document.getElementById('page-'+page).classList.add('active');
  document.querySelectorAll('.nav-item')[pages.indexOf(page)].classList.add('active');
  document.getElementById('page-title').textContent = titles[page];
  currentPage = page;
  closeSidebar();
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

function onSearch(type) {
  clearTimeout(searchTimers[type]);
  searchTimers[type] = setTimeout(() => {
    if (type === 'cases') loadCases();
    else if (type === 'missed') loadMissed();
  }, 300);
}

function statusBadge(s) {
  const map = {open:'s-open',assigned:'s-assigned',reported:'s-reported',done:'s-done',missed:'s-missed'};
  return `<span class="status-badge ${map[s]||'s-open'}">${s}</span>`;
}

function caseTable(cases) {
  if (!cases || !cases.length) return '<div class="empty-state"><i class="ph ph-inbox"></i>No cases found</div>';
  return `<table><thead><tr>
    <th>Reported By</th><th>Group</th><th>Assigned To</th><th>Status</th><th>Opened</th><th>Response</th><th>Description</th>
  </tr></thead><tbody>${cases.map(c=>`<tr onclick="openCase('${c.full_id}')">
    <td><b>${c.driver}</b></td>
    <td style="color:var(--muted)">${c.group}</td>
    <td style="color:var(--text)">${c.agent}</td>
    <td>${statusBadge(c.status)}${c.reassigned?'<span class="reassign-badge">reassigned</span>':''}</td>
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
      <span class="list-name "list-name">${item.name}</span>
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
      <div class="stat-card"><div class="stat-label">Today Total</div><div class="stat-value v-accent">${t.total}</div></div>
      <div class="stat-card"><div class="stat-label">Assigned To</div><div class="stat-value v-yellow">${t.assigned}</div></div>
      <div class="stat-card"><div class="stat-label">Resolved</div><div class="stat-value v-green">${t.done}</div></div>
      <div class="stat-card"><div class="stat-label">Missed</div><div class="stat-value v-red">${t.missed}</div></div>
      <div class="stat-card"><div class="stat-label">Reassigned</div><div class="stat-value v-purple">${stats.reassigned_count}</div></div>
      <div class="stat-card"><div class="stat-label">Avg Response</div><div class="stat-value v-sm">${stats.all_time.avg_resp}</div></div>
    `;
    // missed badge
    if (t.missed > 0) {
      const b = document.getElementById('missed-badge');
      b.textContent = t.missed; b.style.display = '';
    }
    const lb = stats.leaderboard_day.slice(0,5);
    document.getElementById('lb-overview').innerHTML = listRows(lb, lb[0]?.count||1);
    const grps = stats.top_groups;
    document.getElementById('groups-overview').innerHTML = listRows(grps, grps[0]?.count||1);
    renderLeaderboard(); renderAnalytics();
    document.getElementById('word-cloud').innerHTML = stats.top_words.length
      ? `<div class="word-grid">${stats.top_words.map(w=>`<span class="word-tag">${w.word} <b>${w.count}</b></span>`).join('')}</div>`
      : '<div style="color:var(--muted);font-size:13px">No hashtag keywords yet</div>';
    document.getElementById('group-bars-lb').innerHTML = listRows(grps, grps[0]?.count||1);
  } catch(e){console.error(e);}
}

function renderLeaderboard() {
  if (!stats.leaderboard_day) return;
  const lb = stats['leaderboard_'+lbPeriod] || [];
  document.getElementById('leaderboard-full').innerHTML = lb.length
    ? lb.map((a,i)=>`<div class="list-row"><span class="medal">${medals[i]||((i+1)+'.')}</span><span class="list-name">${a.name}</span><span class="list-count">${a.count} cases</span></div>`).join('')
    : '<div style="color:var(--muted);font-size:13px;padding:8px 0">No data</div>';
}

function renderAnalytics() {
  if (!stats.week) return;
  const d = analyticsPeriod==='week' ? stats.week : stats.month;
  const rate = d.total ? Math.round(d.done/d.total*100) : 0;
  document.getElementById('analytics-stats').innerHTML = `
    <div class="row"><span>Total Cases</span><span class="val">${d.total}</span></div>
    <div class="row"><span>Resolved</span><span class="val" style="color:var(--green)">${d.done}</span></div>
    <div class="row"><span>Missed</span><span class="val" style="color:var(--red)">${d.missed}</span></div>
    <div class="row"><span>Resolution Rate</span><span class="val">${rate}%</span></div>
    <div class="row"><span>All Time Total</span><span class="val">${stats.all_time.total}</span></div>
    <div class="row"><span>Avg Response</span><span class="val">${stats.all_time.avg_resp}</span></div>
  `;
}

async function loadCases() {
  const search = document.getElementById('cases-search')?.value||'';
  document.getElementById('cases-table').innerHTML = '<div class="loading">Loading...</div>';
  try {
    const url = currentFilter === '__date__'
      ? `/api/cases?date=${currentDateFilter}&search=${encodeURIComponent(search)}`
      : `/api/cases?filter=${currentFilter}&search=${encodeURIComponent(search)}`;
    const r = await fetch(url);
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
  document.getElementById('modal-title').textContent = 'Loading...';
  try {
    const r = await fetch('/api/case?id='+encodeURIComponent(caseId));
    const c = await r.json();
    document.getElementById('modal-title').textContent = `${c.driver} — ${c.group}`;
    document.getElementById('modal-body').innerHTML = `
      ${buildTimeline(c)}
      <div class="detail-grid">
        <div class="detail-item"><div class="detail-label">Status</div><div class="detail-val">${statusBadge(c.status)}</div></div>
        <div class="detail-item"><div class="detail-label">Assigned To</div><div class="detail-val">${c.agent}</div></div>
        <div class="detail-item"><div class="detail-label">Reported By</div><div class="detail-val">${c.driver}</div></div>
        <div class="detail-item"><div class="detail-label">Group</div><div class="detail-val">${c.group}</div></div>
        <div class="detail-item"><div class="detail-label">Opened</div><div class="detail-val">${c.opened}</div></div>
        <div class="detail-item"><div class="detail-label">Assigned At</div><div class="detail-val">${c.assigned_at||'—'}</div></div>
        <div class="detail-item"><div class="detail-label">Response Time</div><div class="detail-val">${c.response}</div></div>
        <div class="detail-item"><div class="detail-label">Resolution Time</div><div class="detail-val">${c.resolution_secs||'—'}</div></div>
      </div>
      ${c.full_description?`<div class="desc-box"><span class="box-label">Issue Description</span><p class="box-text">${c.full_description}</p></div>`:''}
      ${c.full_notes?`<div class="notes-box"><span class="box-label">📋 Report / Notes</span><p class="box-text">${c.full_notes}</p></div>`:''}
    `;
  } catch(e){
    console.error('Case modal error:', e);
    document.getElementById('modal-body').innerHTML='<div class="loading">Error loading case. Check console.</div>';
  }
  }

function closeModal() { document.getElementById('modal-overlay').classList.remove('open'); }
function closeModalOutside(e) { if(e.target.id==='modal-overlay') closeModal(); }

function closeReport() { document.getElementById('report-modal-overlay').classList.remove('open'); }
function closeReportOutside(e) { if(e.target.id==='report-modal-overlay') closeReport(); }

function setReportTab(tab, btn) {
  reportTab = tab;
  document.querySelectorAll('.report-tab').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('report-period-bar').style.display = tab === 'custom' ? 'flex' : 'none';
  if (tab === 'today') generateReport();
}

function toggleCustomDates() {
  const v = document.getElementById('report-period-select').value;
  document.getElementById('custom-date-inputs').style.display = v === 'custom' ? 'flex' : 'none';
}


function buildTimeline(c) {
  const steps = [
    {label:'Open',     time: c.opened},
    {label:'Assigned', time: c.assigned_at||''},
    {label:'Reported', time: ''},
    {label:'Resolved', time: c.closed||''},
  ];
  const order = ['open','assigned','reported','done','missed'];
  const si = Math.max(0, order.indexOf(c.status));
  let html = '<div class="timeline">';
  steps.forEach((s,i) => {
    const isDone   = i < si;
    const isActive = i === si || (c.status === 'missed' && i === 0);
    const afterDone = i < si;
    const dotClass = isDone ? 'done' : isActive ? 'active' : '';
    html += `<div class="tl-step${afterDone?' done-step':''}">
      <div class="tl-dot ${dotClass}">${isDone?'✓':i+1}</div>
      <div class="tl-label">${s.label}</div>
      <div class="tl-time">${s.time&&s.time!=='—'?s.time:''}</div>
    </div>`;
  });
  html += '</div>';
  return html;
}

function openReport() {
  document.getElementById('report-modal-overlay').classList.add('open');
  reportTab = 'today';
  document.querySelectorAll('.report-tab').forEach((b,i)=>b.classList.toggle('active',i===0));
  document.getElementById('report-period-bar').style.display = 'none';
  generateReport();
}

function closeReport() { 
  document.getElementById('report-modal-overlay').classList.remove('open'); 
}

function closeReportOutside(e) { 
  if(e.target.id==='report-modal-overlay') closeReport(); 
}

function setReportTab(tab, btn) {
  reportTab = tab;
  document.querySelectorAll('.report-tab').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('report-period-bar').style.display = tab === 'custom' ? 'flex' : 'none';
  if (tab === 'today') generateReport();
}

function toggleCustomDates() {
  const v = document.getElementById('report-period-select').value;
  document.getElementById('custom-date-inputs').style.display = v === 'custom' ? 'flex' : 'none';
}
async function generateReport() {
  document.getElementById('report-content').innerHTML = '<div class="loading">Generating report...</div>';
  let url = '/api/report?period=today';
  if (reportTab === 'custom') {
    const period = document.getElementById('report-period-select').value;
    if (period === 'custom') {
      const from = document.getElementById('report-date-from').value;
      const to = document.getElementById('report-date-to').value;
      if (!from) { document.getElementById('report-content').innerHTML = '<div class="loading">Please select a start date.</div>'; return; }
      url = `/api/report?period=custom&from=${from}&to=${to||from}`;
    } else {
      url = `/api/report?period=${period}`;
    }
  }
  try {
    const r = await fetch(url);
    const d = await r.json();
    const medals = ['🥇','🥈','🥉'];
    const now = new Date().toLocaleString();
    document.getElementById('report-ts').textContent = 'Generated ' + now;
    document.getElementById('report-content').innerHTML = `
      <div class="report-title">${d.label}</div>
      <div class="report-subtitle">Kurtex Alert Bot — Truck Maintenance Command Center</div>
      <div class="report-stat-grid">
        <div class="report-stat"><div class="report-stat-val v-accent">${d.total}</div><div class="report-stat-label">Total</div></div>
        <div class="report-stat"><div class="report-stat-val v-green">${d.done}</div><div class="report-stat-label">Resolved</div></div>
        <div class="report-stat"><div class="report-stat-val v-red">${d.missed}</div><div class="report-stat-label">Missed</div></div>
        <div class="report-stat"><div class="report-stat-val v-yellow">${d.assigned}</div><div class="report-stat-label">Assigned</div></div>
        <div class="report-stat"><div class="report-stat-val" style="font-size:16px;margin-top:4px">${d.avg_resp}</div><div class="report-stat-label">Avg Response</div></div>
        <div class="report-stat"><div class="report-stat-val v-accent">${d.rate}%</div><div class="report-stat-label">Rate</div></div>
      </div>
      ${d.leaderboard.length ? `<div class="report-section"><h3>Agent Activity</h3>${d.leaderboard.map((a,i)=>`<div class="report-row"><span class="medal">${medals[i]||(i+1)+'.'}</span><span class="name">${a.name}</span><span class="count">${a.count} cases</span></div>`).join('')}</div>` : ''}
      ${d.top_groups.length ? `<div class="report-section"><h3>Top Groups</h3>${d.top_groups.map((g,i)=>`<div class="report-row"><span class="medal">${medals[i]||(i+1)+'.'}</span><span class="name">${g.name}</span><span class="count">${g.count}</span></div>`).join('')}</div>` : ''}
      ${d.missed_cases.length ? `<div class="report-section"><h3>Missed Cases (${d.missed})</h3>${d.missed_cases.map(c=>`<div class="report-row"><span class="name">${c.driver}</span><span style="color:var(--muted);font-size:11px;margin-right:8px">${c.group}</span><span style="color:var(--muted);font-size:11px">${c.opened}</span></div>`).join('')}</div>` : ''}
    `;
  } catch(e) { document.getElementById('report-content').innerHTML = '<div class="loading">Error generating report.</div>'; }
}



// ── Fleet Stats ───────────────────────────────────────────────────────────────
async function loadFleet() {
  document.getElementById('fleet-content').innerHTML = '<div class="loading">Loading fleet stats...</div>';
  try {
    const r = await fetch('/api/fleet');
    if (r.status === 401) { window.location='/login'; return; }
    const d = await r.json();
    
    function unitCard(title, items, colorVar) {
      if (!items || !items.length) return `<div class="card"><div class="card-title">${title}</div><div style="color:var(--muted);font-size:13px">No data yet</div></div>`;
      const max = items[0].count || 1;
      return `<div class="card">
        <div class="card-title">${title}</div>
        ${items.map((item,i)=>`
          <div class="list-row">
            <span class="medal">${['🥇','🥈','🥉'][i]||(i+1)+'.'}</span>
            <span class="list-name">${item.unit} <span style="font-size:10px;color:var(--muted);font-weight:400">${item.vtype}</span></span>
            <div class="bar-wrap"><div class="bar-fill" style="width:${Math.round(item.count/max*100)}%;background:${colorVar}"></div></div>
            <span class="list-count">${item.count}</span>
          </div>`).join('')}
      </div>`;
    }

    document.getElementById('fleet-content').innerHTML = `
      <div class="stat-grid" style="margin-bottom:20px">
        <div class="stat-card"><div class="stat-label">Total Reports</div><div class="stat-value v-accent">${d.total_reports}</div></div>
        <div class="stat-card"><div class="stat-label">Trucks</div><div class="stat-value v-blue">${d.truck_count}</div></div>
        <div class="stat-card"><div class="stat-label">Trailers</div><div class="stat-value v-yellow">${d.trailer_count}</div></div>
        <div class="stat-card"><div class="stat-label">Reefers</div><div class="stat-value v-purple">${d.reefer_count}</div></div>
      </div>
      <div class="two-col" style="margin-bottom:16px">
        ${unitCard('<i class="ph ph-truck"></i> Most Reported Units', d.top_units, 'var(--accent)')}
        ${unitCard('<i class="ph ph-user"></i> Most Reported Drivers', d.top_drivers, 'var(--red)')}
      </div>
      <div class="two-col">
        ${unitCard('<i class="ph ph-warning"></i> Top Issues', d.top_issues, 'var(--yellow)')}
        ${unitCard('<i class="ph ph-package"></i> Load Types', d.load_types, 'var(--green)')}
      </div>
    `;
  } catch(e) { 
    console.error(e);
    document.getElementById('fleet-content').innerHTML = '<div class="loading">Error loading fleet stats.</div>'; 
  }
}

// ── My Profile ────────────────────────────────────────────────────────────────
async function loadMyProfile() {
  document.getElementById('my-profile-content').innerHTML = '<div class="loading">Loading...</div>';
  try {
    const r = await fetch('/api/my_profile');
    const p = await r.json();
    document.getElementById('my-profile-content').innerHTML = `
      <div class="two-col" style="margin-bottom:16px">
        <div class="card">
          <div style="display:flex;align-items:center;gap:14px;margin-bottom:16px">
            <div style="width:52px;height:52px;border-radius:50%;background:var(--accent-bg);display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:700;color:var(--accent);flex-shrink:0">${p.name[0]}</div>
            <div>
              <div style="font-size:17px;font-weight:700">${p.name}</div>
              <div style="font-size:12px;color:var(--muted)">${p.username ? '@'+p.username : ''} · <span style="color:var(--accent)">${p.role}</span></div>
            </div>
          </div>
          <div class="report-stat-grid">
            <div class="report-stat"><div class="report-stat-val v-accent">${p.total}</div><div class="report-stat-label">All Time</div></div>
            <div class="report-stat"><div class="report-stat-val v-green">${p.done}</div><div class="report-stat-label">Resolved</div></div>
            <div class="report-stat"><div class="report-stat-val v-red">${p.missed}</div><div class="report-stat-label">Missed</div></div>
            <div class="report-stat"><div class="report-stat-val v-accent">${p.rate}%</div><div class="report-stat-label">Rate</div></div>
          </div>
        </div>
        <div class="card">
          <div class="card-title"><i class="ph ph-calendar"></i>Period Breakdown</div>
          <div class="stats-list">
            <div class="row"><span>Today assigned</span><span class="val">${p.today_total}</span></div>
            <div class="row"><span>Today resolved</span><span class="val" style="color:var(--green)">${p.today_done}</span></div>
            <div class="row"><span>This week assigned</span><span class="val">${p.week_total}</span></div>
            <div class="row"><span>This week resolved</span><span class="val" style="color:var(--green)">${p.week_done}</span></div>
            <div class="row"><span>Avg response</span><span class="val">${p.avg_resp}</span></div>
          </div>
        </div>
      </div>
      <div class="section-title" style="margin-bottom:10px">Recent Cases</div>
      <div class="table-wrap"><div class="table-scroll">${caseTable(p.recent)}</div></div>
    `;
      } catch(e) { document.getElementById('my-profile-content').innerHTML = '<div class="loading">Error loading profile.</div>'; }
}

// ── Agents (manager only) ─────────────────────────────────────────────────────
async function loadAgents() {
  document.getElementById('agents-content').innerHTML = '<div class="loading">Loading...</div>';
  try {
    const r = await fetch('/api/agents');
    if (r.status === 403) { document.getElementById('agents-content').innerHTML = '<div class="loading">Access denied.</div>'; return; }
    const agents = await r.json();
    if (!agents.length) { document.getElementById('agents-content').innerHTML = '<div class="empty-state"><i class="ph ph-users"></i>No agents yet</div>'; return; }
    document.getElementById('agents-content').innerHTML = `
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px">
        ${agents.map(a=>`
          <div class="card" style="cursor:pointer" onclick="openAgentModal('${a.name}')">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
              <div style="width:38px;height:38px;border-radius:50%;background:var(--accent-bg);display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:700;color:var(--accent);flex-shrink:0">${a.name[0]}</div>
              <div style="min-width:0">
                <div style="font-size:13px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${a.name}</div>
                <div style="font-size:11px;color:var(--muted)">${a.username?'@'+a.username:'No username'}</div>
              </div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;text-align:center">
              <div style="background:var(--surface2);border-radius:7px;padding:6px">
                <div style="font-size:16px;font-weight:800;color:var(--accent)">${a.total}</div>
                <div style="font-size:9px;color:var(--muted);font-weight:600;text-transform:uppercase">Total</div>
              </div>
              <div style="background:var(--surface2);border-radius:7px;padding:6px">
                <div style="font-size:16px;font-weight:800;color:var(--green)">${a.done}</div>
                <div style="font-size:9px;color:var(--muted);font-weight:600;text-transform:uppercase">Done</div>
              </div>
              <div style="background:var(--surface2);border-radius:7px;padding:6px">
                <div style="font-size:16px;font-weight:800;color:var(--accent)">${a.rate}%</div>
                <div style="font-size:9px;color:var(--muted);font-weight:600;text-transform:uppercase">Rate</div>
              </div>
            </div>
            <div style="margin-top:8px;font-size:11px;color:var(--muted);text-align:center">Avg: ${a.avg_resp}</div>
          </div>
        `).join('')}
      </div>
    `;
      } catch(e) { document.getElementById('agents-content').innerHTML = '<div class="loading">Error.</div>'; }
}

async function openAgentModal(name) {
  const overlay = document.getElementById('agent-modal-overlay');
  const body = document.getElementById('agent-modal-body');
  const title = document.getElementById('agent-modal-title');
  overlay.classList.add('open');
  body.innerHTML = '<div class="loading">Loading profile...</div>';
  title.textContent = name;
  try {
    const r = await fetch('/api/agent?name='+encodeURIComponent(name));
    if (!r.ok) { body.innerHTML = '<div class="loading">Agent not found.</div>'; return; }
    const a = await r.json();
    const rate = a.total > 0 ? Math.round(a.done/a.total*100) : 0;
    body.innerHTML = `
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:16px">
        <div class="agent-stat"><div class="agent-stat-val" style="color:var(--accent)">${a.total}</div><div class="agent-stat-label">Total</div></div>
        <div class="agent-stat"><div class="agent-stat-val" style="color:var(--green)">${a.done}</div><div class="agent-stat-label">Done</div></div>
        <div class="agent-stat"><div class="agent-stat-val" style="color:var(--red)">${a.missed}</div><div class="agent-stat-label">Missed</div></div>
        <div class="agent-stat"><div class="agent-stat-val" style="color:var(--accent)">${rate}%</div><div class="agent-stat-label">Rate</div></div>
      </div>
      <div style="background:var(--surface2);border-radius:8px;padding:10px 12px;margin-bottom:14px;font-size:13px">
        Avg response: <b>${a.avg_resp}</b>
      </div>
      ${a.recent && a.recent.length ? `
        <div style="font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">Recent Cases</div>
        <div style="overflow-x:auto">
          <table style="width:100%;border-collapse:collapse;font-size:12px;min-width:400px">
            <thead><tr style="background:var(--surface2);border-bottom:1px solid var(--border)">
              <th style="padding:8px 10px;text-align:left;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Reported By</th>
              <th style="padding:8px 10px;text-align:left;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Group</th>
              <th style="padding:8px 10px;text-align:left;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Status</th>
              <th style="padding:8px 10px;text-align:left;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Date</th>
            </tr></thead>
            <tbody>${a.recent.map(c=>`
              <tr style="border-bottom:1px solid var(--border);cursor:pointer" onclick="closeAgentModal();setTimeout(()=>openCase('${c.full_id}'),200)">
                <td style="padding:8px 10px;font-weight:500">${c.driver}</td>
                <td style="padding:8px 10px;color:var(--muted)">${c.group}</td>
                <td style="padding:8px 10px">${statusBadge(c.status)}</td>
                <td style="padding:8px 10px;color:var(--muted);font-size:11px">${c.opened}</td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>` : '<div style="color:var(--muted);font-size:13px;padding:8px 0">No cases yet.</div>'}
    `;
  } catch(e) {
    console.error('Agent modal error:', e);
    body.innerHTML = '<div class="loading">Error loading profile.</div>';
  }
}

function printReport() {
  const orig = document.title;
  document.title = 'Kurtex Report — ' + new Date().toLocaleDateString();
  window.print();
  document.title = orig;
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
  else if (currentPage==='fleet') loadFleet();
  else if (currentPage==='my_profile') loadMyProfile();
  else if (currentPage==='agents') loadAgents();
  document.getElementById('last-update').textContent = 'Updated '+new Date().toLocaleTimeString();
}

refresh();
setInterval(refresh, 10000);

</script>
</body>
</html>"""

@app.route("/login")
def login():
    return render_template_string(LOGIN_HTML, bot_username=get_bot_username(), error=request.args.get("error"))

@app.route("/")
def index():
    if not session.get("user"): return redirect("/login")
    user = session["user"]
    role = user.get("role","agent")
    is_manager = role in ("developer","super_admin")
    return render_template_string(DASHBOARD_HTML, user=user, is_manager=is_manager)

def run_dashboard():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=DASHBOARD_PORT, debug=False, use_reloader=False)

def start_dashboard_thread():
    Thread(target=run_dashboard, daemon=True).start()
    logger.info(f"Dashboard started on port {DASHBOARD_PORT}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_dashboard()
