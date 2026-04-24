"""
dashboard.py — Kurtex Alert Bot Web Dashboard
Clean rewrite with all features working.
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
        logger.error(f"serialize_case error: {e}")
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
        except: pass
        session["user"] = {
            "id": user_id, "first_name": data.get("first_name",""),
            "username": data.get("username",""), "photo_url": data.get("photo_url",""),
            "role": role,
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
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    try:
        cases = load_cases()
        today = today_str(); wk = week_start_str(); mo = month_start_str()
        tc = [c for c in cases if (c.get("opened_at") or "").startswith(today)]
        wc = [c for c in cases if (c.get("opened_at") or "") >= wk]
        mc = [c for c in cases if (c.get("opened_at") or "") >= mo]
        st = Counter(c.get("status","open") for c in tc)
        def lb(lst):
            cnt = Counter(c["agent_name"] for c in lst if c.get("agent_name") and c.get("status") in ("assigned","reported","done"))
            return [{"name":n,"count":v} for n,v in cnt.most_common(10)]
        grps = Counter(c.get("group_name","Unknown") for c in cases)
        hashtags = re.findall(r'#\w+', " ".join(c.get("description","") for c in cases).lower())
        rt = [c["response_secs"] for c in cases if c.get("response_secs")]
        avg = int(sum(rt)/len(rt)) if rt else 0
        return jsonify({
            "today": {"total":len(tc),"open":st.get("open",0),"assigned":st.get("assigned",0)+st.get("reported",0),"done":st.get("done",0),"missed":st.get("missed",0)},
            "week":  {"total":len(wc),"done":sum(1 for c in wc if c.get("status")=="done"),"missed":sum(1 for c in wc if c.get("status")=="missed")},
            "month": {"total":len(mc),"done":sum(1 for c in mc if c.get("status")=="done"),"missed":sum(1 for c in mc if c.get("status")=="missed")},
            "all_time": {"total":len(cases),"done":sum(1 for c in cases if c.get("status")=="done"),"avg_resp":fmt_secs(avg)},
            "leaderboard_day": lb(tc), "leaderboard_week": lb(wc), "leaderboard_month": lb(mc),
            "top_groups": [{"name":n,"count":v} for n,v in grps.most_common(5)],
            "top_words": [{"word":w,"count":v} for w,v in Counter(hashtags).most_common(15)],
            "reassigned_count": sum(1 for c in cases if c.get("reassigned")),
        })
    except Exception as e:
        logger.error(f"api_stats error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/cases")
def api_cases():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    try:
        f = request.args.get("filter","today")
        search = request.args.get("search","").lower().strip()
        date_filter = request.args.get("date","").strip()
        cases = load_cases()
        if date_filter:
            cases = [c for c in cases if (c.get("opened_at") or "").startswith(date_filter)]
        elif f == "today":   cases = [c for c in cases if (c.get("opened_at") or "").startswith(today_str())]
        elif f == "week":    cases = [c for c in cases if (c.get("opened_at") or "") >= week_start_str()]
        elif f == "missed":  cases = [c for c in cases if c.get("status") == "missed"]
        elif f == "active":  cases = [c for c in cases if c.get("status") in ("open","assigned","reported")]
        elif f == "reassigned": cases = [c for c in cases if c.get("reassigned")]
        if search:
            cases = [c for c in cases if
                     search in (c.get("driver_name") or "").lower() or
                     search in (c.get("group_name") or "").lower() or
                     search in (c.get("agent_name") or "").lower() or
                     search in (c.get("description") or "").lower()]
        cases = sorted(cases, key=lambda c: c.get("opened_at",""), reverse=True)[:200]
        return jsonify([serialize_case(c) for c in cases])
    except Exception as e:
        logger.error(f"api_cases error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/case")
def api_case_detail():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    case_id = request.args.get("id","").strip()
    if not case_id: return jsonify({"error":"no id"}), 400
    try:
        for c in load_cases():
            if (c.get("id") or "") == case_id or (c.get("id") or "").startswith(case_id):
                data = serialize_case(c)
                data.update({
                    "full_description": c.get("description",""),
                    "full_notes":       c.get("notes","") or "",
                    "agent_username":   c.get("agent_username",""),
                    "assigned_at":      fmt_dt(c.get("assigned_at")),
                    "resolution_secs":  fmt_secs(c.get("resolution_secs")),
                    "vehicle_type":     c.get("vehicle_type",""),
                    "unit_number":      c.get("unit_number",""),
                    "report_driver":    c.get("report_driver",""),
                    "issue_text":       c.get("issue_text",""),
                    "load_type":        c.get("load_type",""),
                    "priority":         c.get("priority",""),
                })
                return jsonify(data)
        return jsonify({"error":"not found"}), 404
    except Exception as e:
        logger.error(f"api_case error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/agent")
def api_agent():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    agent_name = request.args.get("name","").strip()
    if not agent_name: return jsonify({"error":"no name"}), 400
    try:
        cases = [c for c in load_cases() if (c.get("agent_name") or "").lower() == agent_name.lower()]
        total  = len(cases)
        done   = sum(1 for c in cases if c.get("status") == "done")
        missed = sum(1 for c in cases if c.get("status") == "missed")
        rt     = [c["response_secs"] for c in cases if c.get("response_secs")]
        avg    = int(sum(rt)/len(rt)) if rt else 0
        recent = sorted(cases, key=lambda c: c.get("opened_at",""), reverse=True)[:15]
        return jsonify({
            "name": agent_name, "total": total, "done": done, "missed": missed,
            "avg_resp": fmt_secs(avg), "rate": round(done/total*100) if total else 0,
            "recent": [serialize_case(c) for c in recent],
        })
    except Exception as e:
        logger.error(f"api_agent error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/agents")
def api_agents():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    if session["user"].get("role","agent") not in ("developer","super_admin"):
        return jsonify({"error":"forbidden"}), 403
    try:
        cases = load_cases()
        users = []
        try:
            from storage.user_store import get_all_user_dicts
            users = [u for u in get_all_user_dicts() if (u.get("role") or "") in ("agent","super_admin")]
        except Exception as e:
            logger.error(f"user_store error: {e}")
        if not users:
            seen = {}
            for c in cases:
                name = (c.get("agent_name") or "").strip()
                if name and name not in seen:
                    seen[name] = {"name": name, "username": c.get("agent_username",""), "role": "agent"}
            users = list(seen.values())
        result = []
        for u in users:
            name = (u.get("name") or "").strip()
            if not name: continue
            uname = (u.get("username") or "").lower()
            agent_cases = [c for c in cases if
                           (c.get("agent_name") or "").lower() == name.lower() or
                           (uname and (c.get("agent_username") or "").lower() == uname)]
            total  = len(agent_cases)
            done   = sum(1 for c in agent_cases if c.get("status") == "done")
            missed = sum(1 for c in agent_cases if c.get("status") == "missed")
            rt     = [c["response_secs"] for c in agent_cases if c.get("response_secs")]
            avg    = int(sum(rt)/len(rt)) if rt else 0
            result.append({
                "name":     name,
                "username": u.get("username",""),
                "total":    total, "done": done, "missed": missed,
                "avg_resp": fmt_secs(avg),
                "rate":     round(done/total*100) if total else 0,
            })
        result.sort(key=lambda x: -x["total"])
        return jsonify(result)
    except Exception as e:
        logger.error(f"api_agents error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/my_profile")
def api_my_profile():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    try:
        user  = session["user"]
        name  = user.get("first_name","")
        uname = user.get("username","")
        cases = load_cases()
        my_cases = [c for c in cases if
                    (c.get("agent_name") or "").lower() == name.lower() or
                    (uname and (c.get("agent_username") or "") == uname)]
        today = today_str(); wk = week_start_str()
        tc = [c for c in my_cases if (c.get("opened_at") or "").startswith(today)]
        wc = [c for c in my_cases if (c.get("opened_at") or "") >= wk]
        total  = len(my_cases)
        done   = sum(1 for c in my_cases if c.get("status") == "done")
        missed = sum(1 for c in my_cases if c.get("status") == "missed")
        rt     = [c["response_secs"] for c in my_cases if c.get("response_secs")]
        avg    = int(sum(rt)/len(rt)) if rt else 0
        recent = sorted(my_cases, key=lambda c: c.get("opened_at",""), reverse=True)[:10]
        return jsonify({
            "name": name, "username": uname, "role": user.get("role","agent"),
            "total": total, "done": done, "missed": missed,
            "avg_resp": fmt_secs(avg), "rate": round(done/total*100) if total else 0,
            "today_total": len(tc), "today_done": sum(1 for c in tc if c.get("status")=="done"),
            "week_total":  len(wc), "week_done":  sum(1 for c in wc if c.get("status")=="done"),
            "recent": [serialize_case(c) for c in recent],
        })
    except Exception as e:
        logger.error(f"api_my_profile error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/fleet")
def api_fleet():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    try:
        cases = [c for c in load_cases() if c.get("vehicle_type")]
        total         = len(cases)
        truck_count   = sum(1 for c in cases if c.get("vehicle_type") == "truck")
        trailer_count = sum(1 for c in cases if c.get("vehicle_type") == "trailer")
        reefer_count  = sum(1 for c in cases if c.get("vehicle_type") == "reefer")
        unit_counts   = Counter((c.get("unit_number","").strip(), c.get("vehicle_type","")) for c in cases if c.get("unit_number","").strip())
        driver_counts = Counter(c.get("report_driver","").strip() for c in cases if c.get("report_driver","").strip())
        issue_counts  = Counter((c.get("issue_text","").strip() or "")[:40] for c in cases if c.get("issue_text","").strip())
        load_counts   = Counter(c.get("load_type","").strip() for c in cases if c.get("load_type","").strip())
        return jsonify({
            "total_reports": total, "truck_count": truck_count,
            "trailer_count": trailer_count, "reefer_count": reefer_count,
            "top_units":    [{"unit":u,"vtype":vt,"count":cnt} for (u,vt),cnt in unit_counts.most_common(10)],
            "top_drivers":  [{"unit":n,"vtype":"","count":cnt} for n,cnt in driver_counts.most_common(10)],
            "top_issues":   [{"unit":iss,"vtype":"","count":cnt} for iss,cnt in issue_counts.most_common(8)],
            "load_types":   [{"unit":lt,"vtype":"","count":cnt} for lt,cnt in load_counts.most_common(6)],
        })
    except Exception as e:
        logger.error(f"api_fleet error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/report")
def api_report():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    try:
        period    = request.args.get("period","today")
        date_from = request.args.get("from","")
        date_to   = request.args.get("to","")
        cases     = load_cases()
        if period == "today":
            label = "Today — " + datetime.now().strftime("%B %d, %Y")
            cases = [c for c in cases if (c.get("opened_at") or "").startswith(today_str())]
        elif period == "week":
            label = "This Week"; cases = [c for c in cases if (c.get("opened_at") or "") >= week_start_str()]
        elif period == "month":
            label = "This Month"; cases = [c for c in cases if (c.get("opened_at") or "") >= month_start_str()]
        elif period == "custom" and date_from:
            dt = date_to or today_str(); label = f"{date_from} to {dt}"
            cases = [c for c in cases if date_from <= (c.get("opened_at") or "")[:10] <= dt]
        else:
            label = "All Time"
        total  = len(cases)
        done   = sum(1 for c in cases if c.get("status") == "done")
        missed = [c for c in cases if c.get("status") == "missed"]
        rt     = [c["response_secs"] for c in cases if c.get("response_secs")]
        avg    = int(sum(rt)/len(rt)) if rt else 0
        agent_counts  = Counter(c["agent_name"] for c in cases if c.get("agent_name") and c.get("status") in ("assigned","reported","done"))
        group_counts  = Counter(c.get("group_name","Unknown") for c in cases)
        return jsonify({
            "label": label, "total": total, "done": done, "missed": len(missed),
            "assigned": sum(1 for c in cases if c.get("status") in ("assigned","reported","done")),
            "open": sum(1 for c in cases if c.get("status") == "open"),
            "avg_resp": fmt_secs(avg), "rate": round(done/total*100) if total else 0,
            "leaderboard": [{"name":n,"count":v} for n,v in agent_counts.most_common(10)],
            "top_groups":  [{"name":n,"count":v} for n,v in group_counts.most_common(5)],
            "missed_cases": [serialize_case(c) for c in missed[:20]],
        })
    except Exception as e:
        logger.error(f"api_report error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/export")
def api_export():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    cases = load_cases()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ID","Reported By","Group","Assigned To","Status","Opened","Closed","Response","Description","Notes"])
    for c in sorted(cases, key=lambda x: x.get("opened_at",""), reverse=True):
        w.writerow([
            (c.get("id") or "")[:8], c.get("driver_name",""), c.get("group_name",""),
            c.get("agent_name",""), c.get("status",""),
            (c.get("opened_at") or "")[:16], (c.get("closed_at") or "")[:16],
            fmt_secs(c.get("response_secs")), c.get("description",""), c.get("notes",""),
        ])
    out.seek(0)
    today = datetime.now().strftime("%Y-%m-%d")
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=kurtex-{today}.csv"})


# ── HTML pages ────────────────────────────────────────────────────────────────

LOGIN_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Kurtex Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Plus Jakarta Sans',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;overflow:hidden;background:#0a0a0f}
.bg{position:fixed;inset:0;background:url('https://images.unsplash.com/photo-1473445730015-841f29a9490b?auto=format&fit=crop&w=1920&q=80')center/cover;opacity:.2;filter:grayscale(30%)}
.overlay{position:fixed;inset:0;background:linear-gradient(135deg,rgba(10,10,15,.95),rgba(20,15,40,.85))}
.card{position:relative;z-index:1;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:24px;padding:48px 40px;text-align:center;width:100%;max-width:400px;backdrop-filter:blur(20px)}
.logo{width:72px;height:72px;border-radius:18px;background:linear-gradient(135deg,#6366f1,#8b5cf6);display:flex;align-items:center;justify-content:center;margin:0 auto 20px;font-size:32px}
h1{color:#fff;font-size:24px;font-weight:800;margin-bottom:6px}
.sub{color:rgba(255,255,255,.45);font-size:14px;margin-bottom:28px}
.divider{display:flex;align-items:center;gap:12px;margin-bottom:24px}
.divider-line{flex:1;height:1px;background:rgba(255,255,255,.1)}
.divider span{font-size:11px;color:rgba(255,255,255,.3);text-transform:uppercase;letter-spacing:.08em}
.tg-wrap{display:flex;justify-content:center}
.error{color:#f87171;font-size:13px;margin-bottom:16px;background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.2);border-radius:8px;padding:8px 12px}
</style>
</head><body>
<div class="bg"></div><div class="overlay"></div>
<div class="card">
  <div class="logo">🚛</div>
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
</div>
</body></html>"""


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Kurtex Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://unpkg.com/@phosphor-icons/web@2.1.1/src/index.js"></script>
<style>
:root{
  --bg:#f4f4f8;--surface:#fff;--surface2:#f0f0f4;--surface3:#e8e8ee;
  --border:#e2e2e8;--text:#18181b;--muted:#6b7280;--muted2:#9ca3af;
  --accent:#6366f1;--accent-bg:rgba(99,102,241,.08);
  --green:#16a34a;--green-bg:rgba(22,163,74,.08);
  --red:#dc2626;--red-bg:rgba(220,38,38,.08);
  --yellow:#ca8a04;--yellow-bg:rgba(202,138,4,.08);
  --blue:#2563eb;--blue-bg:rgba(37,99,235,.08);
  --purple:#7c3aed;--purple-bg:rgba(124,58,237,.08);
  --shadow:0 1px 4px rgba(0,0,0,.06),0 4px 16px rgba(0,0,0,.04);
}
[data-theme="dark"]{
  --bg:#0f0f14;--surface:#18181f;--surface2:#1e1e26;--surface3:#25252f;
  --border:rgba(255,255,255,.07);--text:#f0f0f5;--muted:#8b8b9e;--muted2:#5a5a6e;
  --accent:#818cf8;--accent-bg:rgba(129,140,248,.1);
  --green:#4ade80;--green-bg:rgba(74,222,128,.08);
  --red:#f87171;--red-bg:rgba(248,113,113,.08);
  --yellow:#fbbf24;--yellow-bg:rgba(251,191,36,.08);
  --blue:#60a5fa;--blue-bg:rgba(96,165,250,.08);
  --purple:#c084fc;--purple-bg:rgba(192,132,252,.08);
  --shadow:0 1px 4px rgba(0,0,0,.3),0 4px 16px rgba(0,0,0,.2);
}
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{font-family:'Plus Jakarta Sans',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;transition:background .2s,color .2s}
.hero-bg{position:fixed;inset:0;z-index:0;background:url('https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?auto=format&fit=crop&w=1920&q=80')center/cover;opacity:.03;pointer-events:none}
.layout{position:relative;z-index:1;display:flex;min-height:100vh}

.sidebar{width:220px;flex-shrink:0;background:var(--surface);border-right:1px solid var(--border);padding:20px 12px;position:sticky;top:0;height:100vh;display:flex;flex-direction:column;z-index:50;transition:transform .25s,background .2s}
.sidebar-logo{display:flex;align-items:center;gap:10px;margin-bottom:24px;padding:0 8px}
.logo-icon{width:32px;height:32px;border-radius:9px;background:var(--accent);display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0}
.logo-text h2{font-size:14px;font-weight:700}
.logo-text small{font-size:10px;color:var(--muted)}
nav{flex:1}
.nav-item{display:flex;align-items:center;gap:9px;padding:9px 10px;border-radius:9px;color:var(--muted);font-size:13px;font-weight:500;cursor:pointer;transition:all .15s;margin-bottom:2px}
.nav-item:hover{background:var(--surface2);color:var(--text)}
.nav-item.active{background:var(--accent-bg);color:var(--accent)}
.nav-item i{font-size:15px;width:18px;text-align:center;flex-shrink:0}
.nav-badge{margin-left:auto;background:var(--red);color:white;font-size:10px;font-weight:700;padding:1px 6px;border-radius:20px}
.sidebar-footer{padding-top:14px;border-top:1px solid var(--border)}
.user-chip{display:flex;align-items:center;gap:8px;padding:8px;border-radius:10px;background:var(--surface2);margin-bottom:8px}
.user-avatar{width:30px;height:30px;border-radius:50%;border:2px solid var(--border);flex-shrink:0;object-fit:cover}
.user-avatar-init{width:30px;height:30px;border-radius:50%;background:var(--accent-bg);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:var(--accent);flex-shrink:0}
.user-name{font-size:12px;font-weight:600}
.user-role{font-size:10px;color:var(--muted)}
.theme-btn{width:100%;padding:7px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--muted);font-size:12px;font-weight:500;cursor:pointer;font-family:inherit;display:flex;align-items:center;gap:7px;margin-bottom:6px;transition:all .15s}
.logout-btn{width:100%;padding:7px;background:var(--red-bg);border:1px solid rgba(220,38,38,.15);color:var(--red);border-radius:8px;font-size:12px;font-weight:500;cursor:pointer;font-family:inherit;display:flex;align-items:center;justify-content:center;gap:6px;transition:all .15s}

.mobile-header{display:none;position:sticky;top:0;z-index:40;background:var(--surface);border-bottom:1px solid var(--border);padding:12px 16px;align-items:center;justify-content:space-between}
.mobile-logo{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:700}
.hamburger{background:var(--surface2);border:1px solid var(--border);border-radius:8px;width:34px;height:34px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--text)}
.sidebar-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:49}

.main{flex:1;padding:22px 24px;overflow-x:hidden;min-width:0}
.topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;gap:10px;flex-wrap:wrap}
.topbar h1{font-size:18px;font-weight:700}
.topbar-right{display:flex;align-items:center;gap:8px}
.badge-btn{display:flex;align-items:center;gap:5px;background:var(--surface);border:1px solid var(--border);border-radius:20px;padding:5px 12px;font-size:12px;font-weight:500;color:var(--text);cursor:pointer;text-decoration:none;transition:all .15s;font-family:inherit}
.badge-btn:hover{background:var(--surface2)}
.dot{width:6px;height:6px;border-radius:50%;background:var(--green);animation:pulse 2s infinite;flex-shrink:0}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}

.stat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px;margin-bottom:20px}
.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px;box-shadow:var(--shadow)}
.stat-label{font-size:10px;color:var(--muted);margin-bottom:5px;text-transform:uppercase;letter-spacing:.05em;font-weight:600}
.stat-value{font-size:26px;font-weight:800;line-height:1}
.v-accent{color:var(--accent)}.v-green{color:var(--green)}.v-red{color:var(--red)}
.v-yellow{color:var(--yellow)}.v-blue{color:var(--blue)}.v-purple{color:var(--purple)}
.v-sm{font-size:17px!important;margin-top:4px}

.two-col{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px;box-shadow:var(--shadow)}
.card-title{font-size:13px;font-weight:700;margin-bottom:12px;display:flex;align-items:center;gap:7px}
.card-title i{font-size:14px;color:var(--accent)}

.toggle-tabs{display:flex;background:var(--surface2);border-radius:8px;padding:3px;gap:2px;margin-bottom:12px}
.toggle-btn{flex:1;padding:5px 8px;border-radius:6px;border:none;background:transparent;font-size:12px;font-weight:500;color:var(--muted);cursor:pointer;font-family:inherit;transition:all .15s}
.toggle-btn.active{background:var(--surface);color:var(--accent);box-shadow:0 1px 3px rgba(0,0,0,.08)}

.filter-tabs{display:flex;gap:6px;flex-wrap:wrap}
.tab-btn{padding:5px 11px;border-radius:7px;font-size:12px;font-weight:500;border:1px solid var(--border);background:var(--surface);color:var(--muted);cursor:pointer;font-family:inherit;transition:all .15s}
.tab-btn.active{background:var(--accent);border-color:var(--accent);color:#fff}

.list-row{display:flex;align-items:center;gap:7px;padding:7px 0;border-bottom:1px solid var(--border)}
.list-row:last-child{border-bottom:none}
.list-name{font-size:12px;font-weight:500;flex:1}
.list-count{font-size:12px;font-weight:700;color:var(--accent);background:var(--accent-bg);padding:2px 9px;border-radius:20px;flex-shrink:0}
.bar-wrap{flex:1.5;height:4px;background:var(--surface3);border-radius:2px;margin:0 6px}
.bar-fill{height:100%;border-radius:2px;background:var(--accent);transition:width .5s}
.medal{font-size:14px;flex-shrink:0;width:20px}

.section{margin-bottom:20px}
.section-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;flex-wrap:wrap;gap:8px}
.section-title{font-size:13px;font-weight:700}

.search-wrap{position:relative;margin-bottom:14px}
.search-wrap input{width:100%;padding:9px 14px 9px 38px;background:var(--surface);border:1px solid var(--border);border-radius:9px;font-size:13px;color:var(--text);font-family:inherit;outline:none;transition:border .15s}
.search-wrap input:focus{border-color:var(--accent)}
.search-wrap i{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:15px}

.table-wrap{background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden;box-shadow:var(--shadow)}
.table-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:12px;min-width:540px}
thead th{padding:9px 12px;text-align:left;color:var(--muted);font-weight:600;font-size:10px;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid var(--border);background:var(--surface2)}
tbody tr{border-bottom:1px solid var(--border);transition:background .1s;cursor:pointer}
tbody tr:last-child{border-bottom:none}
tbody tr:hover{background:var(--surface2)}
td{padding:9px 12px;vertical-align:middle}
.status-badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;text-transform:uppercase;white-space:nowrap}
.s-open{background:var(--blue-bg);color:var(--blue)}.s-assigned{background:var(--yellow-bg);color:var(--yellow)}
.s-reported{background:var(--purple-bg);color:var(--purple)}.s-done{background:var(--green-bg);color:var(--green)}
.s-missed{background:var(--red-bg);color:var(--red)}
.reassign-badge{display:inline-flex;padding:2px 6px;border-radius:20px;font-size:10px;font-weight:700;background:var(--purple-bg);color:var(--purple);margin-left:4px}
.desc-cell{max-width:200px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--muted)}

.word-grid{display:flex;flex-wrap:wrap;gap:7px}
.word-tag{padding:4px 11px;border-radius:20px;font-size:12px;font-weight:600;background:var(--accent-bg);color:var(--accent);border:1px solid rgba(99,102,241,.15)}

.stats-list .row{display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border);font-size:13px}
.stats-list .row:last-child{border-bottom:none}
.stats-list .val{font-weight:700}

.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:300;align-items:center;justify-content:center;padding:16px}
.modal-overlay.open{display:flex}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:24px;max-width:860px;width:100%;max-height:88vh;overflow-y:auto;position:relative;box-shadow:0 8px 40px rgba(0,0,0,.15)}
.modal-close{position:absolute;top:14px;right:14px;background:var(--surface2);border:1px solid var(--border);border-radius:7px;width:28px;height:28px;cursor:pointer;display:flex;align-items:center;justify-content:center;color:var(--muted)}
.modal h2{font-size:16px;font-weight:700;margin-bottom:16px;padding-right:40px}
.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px}
.detail-item{background:var(--surface2);border-radius:8px;padding:10px 12px}
.detail-label{font-size:10px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px}
.detail-val{font-size:13px;font-weight:600}
.desc-box{background:var(--surface2);border-radius:8px;padding:12px;margin-bottom:10px}
.notes-box{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:12px;margin-bottom:10px}
[data-theme="dark"] .notes-box{background:rgba(251,191,36,.06);border-color:rgba(251,191,36,.2)}
.box-label{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;display:block;margin-bottom:6px;color:var(--muted)}
.box-text{font-size:13px;line-height:1.6}

.timeline{display:flex;align-items:flex-start;gap:0;margin-bottom:16px;padding:14px;background:var(--surface2);border-radius:10px}
.tl-step{display:flex;flex-direction:column;align-items:center;flex:1;position:relative}
.tl-step:not(:last-child)::after{content:'';position:absolute;top:12px;left:calc(50% + 12px);width:calc(100% - 24px);height:2px;background:var(--border)}
.tl-step.done-step::after{background:var(--accent)}
.tl-dot{width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;border:2px solid var(--border);background:var(--surface);z-index:1;position:relative;font-weight:700}
.tl-dot.active{border-color:var(--accent);background:var(--accent);color:#fff}
.tl-dot.done{border-color:var(--green);background:var(--green);color:#fff}
.tl-label{font-size:9px;color:var(--muted);margin-top:5px;font-weight:600;text-transform:uppercase}
.tl-time{font-size:9px;color:var(--muted2);margin-top:2px;text-align:center}

.agent-stat{background:var(--surface2);border-radius:8px;padding:10px;text-align:center}
.agent-stat-val{font-size:22px;font-weight:800}
.agent-stat-label{font-size:10px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-top:2px}

.report-modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:400;align-items:flex-start;justify-content:center;padding:20px;overflow-y:auto}
.report-modal-overlay.open{display:flex}
.report-modal{background:var(--surface);border:1px solid var(--border);border-radius:16px;width:100%;max-width:700px;margin:auto}
.report-header{padding:20px 24px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}
.report-header h2{font-size:17px;font-weight:700}
.report-tabs{display:flex;background:var(--surface2);border-radius:8px;padding:3px;gap:2px}
.report-tab{padding:5px 14px;border-radius:6px;border:none;background:transparent;font-size:12px;font-weight:500;color:var(--muted);cursor:pointer;font-family:inherit;transition:all .15s}
.report-tab.active{background:var(--surface);color:var(--accent);box-shadow:0 1px 3px rgba(0,0,0,.08)}
.report-close{background:var(--surface2);border:1px solid var(--border);border-radius:7px;width:28px;height:28px;cursor:pointer;display:flex;align-items:center;justify-content:center;color:var(--muted)}
.report-body{padding:20px 24px}
.report-period-bar{display:flex;align-items:center;gap:8px;margin-bottom:16px;flex-wrap:wrap}
.report-period-bar select,.report-period-bar input{padding:7px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;font-size:12px;color:var(--text);font-family:inherit;outline:none}
.report-generate-btn{padding:7px 16px;background:var(--accent);color:#fff;border:none;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;font-family:inherit}
.report-stat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(100px,1fr));gap:8px;margin-bottom:18px}
.report-stat{background:var(--surface2);border-radius:10px;padding:12px;text-align:center}
.report-stat-val{font-size:24px;font-weight:800;line-height:1}
.report-stat-label{font-size:10px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-top:3px}
.report-section{margin-bottom:16px}
.report-section h3{font-size:11px;font-weight:700;margin-bottom:8px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.report-row{display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);font-size:12px}
.report-row:last-child{border-bottom:none}
.report-row .rname{flex:1;font-weight:500}
.report-row .rcount{font-weight:700;color:var(--accent);background:var(--accent-bg);padding:1px 8px;border-radius:20px}
.report-footer{padding:12px 24px;border-top:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
.report-footer .ts{font-size:11px;color:var(--muted)}
.print-report-btn{display:flex;align-items:center;gap:6px;background:var(--accent);color:#fff;border:none;border-radius:8px;padding:7px 14px;font-size:12px;font-weight:600;cursor:pointer;font-family:inherit}

.loading{text-align:center;padding:28px;color:var(--muted);font-size:13px}
.empty-state{text-align:center;padding:40px;color:var(--muted)}
.page{display:none}
.page.active{display:block}

@media(max-width:768px){
  .sidebar{position:fixed;left:0;top:0;height:100vh;transform:translateX(-100%);width:240px;box-shadow:4px 0 24px rgba(0,0,0,.15)}
  .sidebar.open{transform:translateX(0)}
  .sidebar-overlay.open{display:block}
  .mobile-header{display:flex}
  .layout{display:block}
  .main{padding:12px 12px 80px}
  .topbar h1{font-size:15px}
  .topbar-right .badge-btn span{display:none}
  .stat-grid{grid-template-columns:repeat(2,1fr);gap:8px}
  .two-col{grid-template-columns:1fr}
  .detail-grid{grid-template-columns:1fr}
  .modal-overlay{padding:0;align-items:flex-end}
  .modal{border-radius:16px 16px 0 0;max-height:92vh;max-width:100%;border-bottom:none}
  .report-modal-overlay{padding:0;align-items:flex-end}
  .report-modal{border-radius:16px 16px 0 0;max-width:100%}
  .section-header{flex-direction:column;align-items:flex-start}
}
@media print{
  .sidebar,.mobile-header,.topbar-right,.report-modal-overlay{display:none!important}
  .main{padding:0}body{background:white;color:black}
}
</style>
</head>
<body>
<div class="hero-bg"></div>

<div class="mobile-header">
  <div class="mobile-logo"><div class="logo-icon">🚛</div> Kurtex</div>
  <div class="hamburger" onclick="toggleSidebar()"><i class="ph ph-list"></i></div>
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
      <div><div class="user-name">{{ user.first_name }}</div><div class="user-role">{{ user.role if user.role else "Manager" }}</div></div>
    </div>
    <button class="theme-btn" onclick="toggleTheme()"><i class="ph ph-sun" id="theme-icon"></i> <span id="theme-label">Light Mode</span></button>
    <button class="logout-btn" onclick="window.location='/logout'"><i class="ph ph-sign-out"></i> Sign out</button>
  </div>
</aside>

<main class="main">
  <div class="topbar">
    <h1 id="page-title">Overview</h1>
    <div class="topbar-right">
      <button class="badge-btn" onclick="openReport()"><i class="ph ph-file-text"></i> <span>Report</span></button>
      <button class="badge-btn" onclick="window.print()"><i class="ph ph-printer"></i> <span>Print</span></button>
      <a class="badge-btn" href="/api/export"><i class="ph ph-download-simple"></i> <span>Export CSV</span></a>
      <div class="badge-btn"><div class="dot"></div><span id="last-update">Loading...</span></div>
    </div>
  </div>

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
          <input type="date" id="cases-date-picker" style="padding:5px 10px;background:var(--surface);border:1px solid var(--border);border-radius:7px;font-size:12px;color:var(--text);font-family:inherit;outline:none" onchange="setCaseDateFilter(this.value)">
          <button class="tab-btn" id="cases-date-clear" onclick="clearDateFilter()" style="display:none">✕</button>
        </div>
      </div>
      <div class="table-wrap"><div class="table-scroll" id="cases-table"><div class="loading">Loading...</div></div></div>
    </div>
  </div>

  <div class="page" id="page-missed">
    <div class="search-wrap"><i class="ph ph-magnifying-glass"></i><input type="text" id="missed-search" placeholder="Search..." oninput="onSearch('missed')"></div>
    <div class="section">
      <div class="section-header"><div class="section-title">Missed Cases</div></div>
      <div class="table-wrap"><div class="table-scroll" id="missed-table"><div class="loading">Loading...</div></div></div>
    </div>
  </div>

  <div class="page" id="page-reassigned">
    <div class="section">
      <div class="section-header"><div class="section-title">Reassigned Cases</div></div>
      <div class="table-wrap"><div class="table-scroll" id="reassigned-table"><div class="loading">Loading...</div></div></div>
    </div>
  </div>

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

  <div class="page" id="page-fleet">
    <div id="fleet-content"><div class="loading">Loading fleet stats...</div></div>
  </div>

  <div class="page" id="page-my_profile">
    <div id="my-profile-content"><div class="loading">Loading...</div></div>
  </div>

  <div class="page" id="page-agents">
    <div id="agents-content"><div class="loading">Loading...</div></div>
  </div>
</main>
</div>

<!-- Case Modal -->
<div class="modal-overlay" id="modal-overlay" onclick="if(event.target===this)closeModal()">
<div class="modal">
  <button class="modal-close" onclick="closeModal()"><i class="ph ph-x"></i></button>
  <h2 id="modal-title">Case Detail</h2>
  <div id="modal-body"><div class="loading">Loading...</div></div>
</div>
</div>

<!-- Agent Modal -->
<div class="modal-overlay" id="agent-modal-overlay" style="z-index:200" onclick="if(event.target===this)closeAgentModal()">
<div class="modal">
  <button class="modal-close" onclick="closeAgentModal()"><i class="ph ph-x"></i></button>
  <h2 id="agent-modal-title">Agent Profile</h2>
  <div id="agent-modal-body"><div class="loading">Loading...</div></div>
</div>
</div>

<!-- Report Modal -->
<div class="report-modal-overlay" id="report-modal-overlay" onclick="if(event.target===this)closeReport()">
<div class="report-modal">
  <div class="report-header">
    <h2><i class="ph ph-file-text" style="color:var(--accent)"></i> Report</h2>
    <div style="display:flex;align-items:center;gap:8px">
      <div class="report-tabs">
        <button class="report-tab active" onclick="setReportTab('today',this)">Today</button>
        <button class="report-tab" onclick="setReportTab('custom',this)">Custom</button>
      </div>
      <button class="report-close" onclick="closeReport()"><i class="ph ph-x"></i></button>
    </div>
  </div>
  <div class="report-body">
    <div id="report-period-bar" class="report-period-bar" style="display:none">
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

<script>
// ── State ──────────────────────────────────────────────────────────────────
var stats = {};
var currentFilter = 'today';
var currentPage = 'overview';
var lbPeriod = 'day';
var analyticsPeriod = 'week';
var reportTab = 'today';
var currentDateFilter = '';
var searchTimers = {};
var isDark = localStorage.getItem('kurtex-theme') === 'dark';
var pages = ['overview','cases','missed','reassigned','leaderboard','analytics','fleet','my_profile','agents'];
var titles = {overview:'Overview',cases:'Cases',missed:'Missed Cases',reassigned:'Reassigned Cases',leaderboard:'Leaderboard',analytics:'Analytics',fleet:'Fleet Stats',my_profile:'My Profile',agents:'Agent Profiles'};
var medals = ['🥇','🥈','🥉'];

// ── Theme ──────────────────────────────────────────────────────────────────
function applyTheme() {
  document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
  var icon = document.getElementById('theme-icon');
  var label = document.getElementById('theme-label');
  if (icon) icon.className = isDark ? 'ph ph-moon' : 'ph ph-sun';
  if (label) label.textContent = isDark ? 'Dark Mode' : 'Light Mode';
}
function toggleTheme() { isDark = !isDark; localStorage.setItem('kurtex-theme', isDark?'dark':'light'); applyTheme(); }
applyTheme();

// ── Sidebar ────────────────────────────────────────────────────────────────
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sidebar-overlay').classList.toggle('open');
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebar-overlay').classList.remove('open');
}

// ── Navigation ─────────────────────────────────────────────────────────────
function showPage(page) {
  document.querySelectorAll('.page').forEach(function(p){p.classList.remove('active');});
  document.querySelectorAll('.nav-item').forEach(function(a){a.classList.remove('active');});
  var pg = document.getElementById('page-'+page);
  if (pg) pg.classList.add('active');
  var idx = pages.indexOf(page);
  var navItems = document.querySelectorAll('.nav-item');
  if (navItems[idx]) navItems[idx].classList.add('active');
  var titleEl = document.getElementById('page-title');
  if (titleEl) titleEl.textContent = titles[page] || page;
  currentPage = page;
  closeSidebar();
  refresh();
}

function setCaseFilter(f, btn) {
  currentFilter = f; currentDateFilter = '';
  document.querySelectorAll('#page-cases .tab-btn').forEach(function(b){b.classList.remove('active');});
  btn.classList.add('active');
  document.getElementById('cases-date-clear').style.display = 'none';
  document.getElementById('cases-date-picker').value = '';
  loadCases();
}

function setCaseDateFilter(date) {
  if (!date) return;
  document.querySelectorAll('#page-cases .tab-btn').forEach(function(b){b.classList.remove('active');});
  document.getElementById('cases-date-clear').style.display = '';
  currentFilter = '__date__'; currentDateFilter = date;
  loadCases();
}

function clearDateFilter() {
  document.getElementById('cases-date-picker').value = '';
  document.getElementById('cases-date-clear').style.display = 'none';
  currentDateFilter = ''; currentFilter = 'today';
  var firstTab = document.querySelector('#page-cases .tab-btn');
  if (firstTab) firstTab.classList.add('active');
  loadCases();
}

function setLbPeriod(p, btn) {
  lbPeriod = p;
  document.querySelectorAll('#page-leaderboard .toggle-btn').forEach(function(b){b.classList.remove('active');});
  btn.classList.add('active');
  renderLeaderboard();
}

function setAnalyticsPeriod(p, btn) {
  analyticsPeriod = p;
  document.querySelectorAll('#page-analytics .toggle-btn').forEach(function(b){b.classList.remove('active');});
  btn.classList.add('active');
  renderAnalytics();
}

function onSearch(type) {
  clearTimeout(searchTimers[type]);
  searchTimers[type] = setTimeout(function(){
    if (type === 'cases') loadCases();
    else if (type === 'missed') loadMissed();
  }, 300);
}

// ── Helpers ────────────────────────────────────────────────────────────────
function statusBadge(s) {
  var map = {open:'s-open',assigned:'s-assigned',reported:'s-reported',done:'s-done',missed:'s-missed'};
  return '<span class="status-badge ' + (map[s]||'s-open') + '">' + s + '</span>';
}

function caseTable(cases) {
  if (!cases || !cases.length) return '<div class="empty-state">No cases found</div>';
  var rows = cases.map(function(c) {
    var cid = (c.full_id || '');
    return '<tr onclick="openCase(this.dataset.id)" data-id="' + cid + '">'
      + '<td><b>' + (c.driver||'—') + '</b></td>'
      + '<td style="color:var(--muted)">' + (c.group||'—') + '</td>'
      + '<td>' + (c.agent||'—') + '</td>'
      + '<td>' + statusBadge(c.status) + (c.reassigned ? '<span class="reassign-badge">reassigned</span>' : '') + '</td>'
      + '<td style="color:var(--muted);font-size:11px">' + (c.opened||'—') + '</td>'
      + '<td style="font-size:11px">' + (c.response||'—') + '</td>'
      + '<td class="desc-cell">' + (c.description||'') + '</td>'
      + '</tr>';
  }).join('');
  return '<table><thead><tr>'
    + '<th>Reported By</th><th>Group</th><th>Assigned To</th><th>Status</th><th>Opened</th><th>Response</th><th>Description</th>'
    + '</tr></thead><tbody>' + rows + '</tbody></table>';
}
function listRows(items, maxCount) {
  if (!items || !items.length) return '<div style="color:var(--muted);font-size:13px;padding:8px 0">No data yet</div>';
  return items.map(function(item, i) {
    return '<div class="list-row">'
      + '<span class="medal">' + (medals[i]||(i+1)+'.') + '</span>'
      + '<span class="list-name">' + item.name + '</span>'
      + '<div class="bar-wrap"><div class="bar-fill" style="width:' + Math.round(item.count/(maxCount||1)*100) + '%"></div></div>'
      + '<span class="list-count">' + item.count + '</span>'
      + '</div>';
  }).join('');
}

function buildTimeline(c) {
  var steps = [
    {label:'Open',     time: c.opened||''},
    {label:'Assigned', time: c.assigned_at||''},
    {label:'Reported', time: ''},
    {label:'Resolved', time: c.closed||''},
  ];
  var order = ['open','assigned','reported','done','missed'];
  var si = Math.max(0, order.indexOf(c.status));
  var html = '<div class="timeline">';
  steps.forEach(function(s, i) {
    var isDone   = i < si;
    var isActive = i === si || (c.status === 'missed' && i === 0);
    var dotClass = isDone ? 'done' : isActive ? 'active' : '';
    html += '<div class="tl-step' + (isDone?' done-step':'') + '">'
      + '<div class="tl-dot ' + dotClass + '">' + (isDone?'✓':(i+1)) + '</div>'
      + '<div class="tl-label">' + s.label + '</div>'
      + '<div class="tl-time">' + (s.time&&s.time!=='—'?s.time:'') + '</div>'
      + '</div>';
  });
  html += '</div>';
  return html;
}

// ── Data loading ───────────────────────────────────────────────────────────
async function loadStats() {
  try {
    var r = await fetch('/api/stats');
    if (r.status === 401) { window.location='/login'; return; }
    if (!r.ok) return;
    stats = await r.json();
    var t = stats.today || {};
    var sg = document.getElementById('stat-grid');
    if (sg) sg.innerHTML =
      '<div class="stat-card"><div class="stat-label">Today Total</div><div class="stat-value v-accent">' + (t.total||0) + '</div></div>'
      + '<div class="stat-card"><div class="stat-label">Assigned To</div><div class="stat-value v-yellow">' + (t.assigned||0) + '</div></div>'
      + '<div class="stat-card"><div class="stat-label">Resolved</div><div class="stat-value v-green">' + (t.done||0) + '</div></div>'
      + '<div class="stat-card"><div class="stat-label">Missed</div><div class="stat-value v-red">' + (t.missed||0) + '</div></div>'
      + '<div class="stat-card"><div class="stat-label">Reassigned</div><div class="stat-value v-purple">' + (stats.reassigned_count||0) + '</div></div>'
      + '<div class="stat-card"><div class="stat-label">Avg Response</div><div class="stat-value v-sm">' + ((stats.all_time||{}).avg_resp||'—') + '</div></div>';

    var badge = document.getElementById('missed-badge');
    if (badge) { if (t.missed > 0) { badge.textContent = t.missed; badge.style.display=''; } else badge.style.display='none'; }

    var lb = (stats.leaderboard_day||[]).slice(0,5);
    var lbo = document.getElementById('lb-overview');
    if (lbo) lbo.innerHTML = listRows(lb, lb[0]?lb[0].count:1);

    var grps = stats.top_groups||[];
    var go = document.getElementById('groups-overview');
    if (go) go.innerHTML = listRows(grps, grps[0]?grps[0].count:1);

    renderLeaderboard();
    renderAnalytics();

    var wc = document.getElementById('word-cloud');
    if (wc) wc.innerHTML = (stats.top_words||[]).length
      ? '<div class="word-grid">' + (stats.top_words||[]).map(function(w){return '<span class="word-tag">'+w.word+' <b>'+w.count+'</b></span>';}).join('') + '</div>'
      : '<div style="color:var(--muted);font-size:13px">No hashtag keywords yet</div>';

    var glb = document.getElementById('group-bars-lb');
    if (glb) glb.innerHTML = listRows(grps, grps[0]?grps[0].count:1);
  } catch(e) { console.error('loadStats error:', e); }
}

function renderLeaderboard() {
  if (!stats.leaderboard_day) return;
  var lb = stats['leaderboard_'+lbPeriod] || [];
  var el = document.getElementById('leaderboard-full');
  if (!el) return;
  el.innerHTML = lb.length
    ? lb.map(function(a,i){return '<div class="list-row"><span class="medal">'+(medals[i]||(i+1)+'.')+'</span><span class="list-name">'+a.name+'</span><span class="list-count">'+a.count+' cases</span></div>';}).join('')
    : '<div style="color:var(--muted);font-size:13px;padding:8px 0">No data</div>';
}

function renderAnalytics() {
  if (!stats.week) return;
  var d = analyticsPeriod==='week' ? stats.week : stats.month;
  var rate = d.total ? Math.round(d.done/d.total*100) : 0;
  var el = document.getElementById('analytics-stats');
  if (!el) return;
  el.innerHTML =
    '<div class="row"><span>Total Cases</span><span class="val">'+d.total+'</span></div>'
    + '<div class="row"><span>Resolved</span><span class="val" style="color:var(--green)">'+d.done+'</span></div>'
    + '<div class="row"><span>Missed</span><span class="val" style="color:var(--red)">'+d.missed+'</span></div>'
    + '<div class="row"><span>Resolution Rate</span><span class="val">'+rate+'%</span></div>'
    + '<div class="row"><span>All Time Total</span><span class="val">'+((stats.all_time||{}).total||0)+'</span></div>';
}

async function loadCases() {
  var el = document.getElementById('cases-table');
  if (!el) return;
  el.innerHTML = '<div class="loading">Loading...</div>';
  try {
    var search = (document.getElementById('cases-search')||{}).value||'';
    var url = currentFilter === '__date__'
      ? '/api/cases?date='+currentDateFilter+'&search='+encodeURIComponent(search)
      : '/api/cases?filter='+currentFilter+'&search='+encodeURIComponent(search);
    var r = await fetch(url);
    if (!r.ok) return;
    var cases = await r.json();
    el.innerHTML = caseTable(cases);
  } catch(e) { console.error(e); el.innerHTML = '<div class="loading">Error loading cases.</div>'; }
}

async function loadMissed() {
  var el = document.getElementById('missed-table');
  if (!el) return;
  try {
    var search = (document.getElementById('missed-search')||{}).value||'';
    var r = await fetch('/api/cases?filter=missed&search='+encodeURIComponent(search));
    if (!r.ok) return;
    el.innerHTML = caseTable(await r.json());
  } catch(e) { console.error(e); }
}

async function loadReassigned() {
  var el = document.getElementById('reassigned-table');
  if (!el) return;
  try {
    var r = await fetch('/api/cases?filter=reassigned');
    if (!r.ok) return;
    el.innerHTML = caseTable(await r.json());
  } catch(e) { console.error(e); }
}

async function loadFleet() {
  var el = document.getElementById('fleet-content');
  if (!el) return;
  el.innerHTML = '<div class="loading">Loading fleet stats...</div>';
  try {
    var r = await fetch('/api/fleet');
    if (!r.ok) { el.innerHTML = '<div class="loading">Error loading fleet stats.</div>'; return; }
    var d = await r.json();
    function unitCard(title, items) {
      if (!items||!items.length) return '<div class="card"><div class="card-title">'+title+'</div><div style="color:var(--muted);font-size:13px">No data yet</div></div>';
      var max = items[0].count||1;
      return '<div class="card"><div class="card-title">'+title+'</div>'
        + items.map(function(item,i){
          return '<div class="list-row"><span class="medal">'+(medals[i]||(i+1)+'.')+'</span>'
            + '<span class="list-name">'+item.unit+(item.vtype?' <span style="font-size:10px;color:var(--muted)">'+item.vtype+'</span>':'')+'</span>'
            + '<div class="bar-wrap"><div class="bar-fill" style="width:'+Math.round(item.count/max*100)+'%"></div></div>'
            + '<span class="list-count">'+item.count+'</span></div>';
        }).join('')
        + '</div>';
    }
    el.innerHTML =
      '<div class="stat-grid" style="margin-bottom:20px">'
      + '<div class="stat-card"><div class="stat-label">Total Reports</div><div class="stat-value v-accent">'+d.total_reports+'</div></div>'
      + '<div class="stat-card"><div class="stat-label">Trucks</div><div class="stat-value v-blue">'+d.truck_count+'</div></div>'
      + '<div class="stat-card"><div class="stat-label">Trailers</div><div class="stat-value v-yellow">'+d.trailer_count+'</div></div>'
      + '<div class="stat-card"><div class="stat-label">Reefers</div><div class="stat-value v-purple">'+d.reefer_count+'</div></div>'
      + '</div>'
      + '<div class="two-col" style="margin-bottom:16px">'
      + unitCard('<i class="ph ph-truck"></i> Most Reported Units', d.top_units)
      + unitCard('<i class="ph ph-user"></i> Most Reported Drivers', d.top_drivers)
      + '</div>'
      + '<div class="two-col">'
      + unitCard('<i class="ph ph-warning"></i> Top Issues', d.top_issues)
      + unitCard('<i class="ph ph-package"></i> Load Types', d.load_types)
      + '</div>';
  } catch(e) { console.error(e); el.innerHTML = '<div class="loading">Error.</div>'; }
}

async function loadMyProfile() {
  var el = document.getElementById('my-profile-content');
  if (!el) return;
  el.innerHTML = '<div class="loading">Loading...</div>';
  try {
    var r = await fetch('/api/my_profile');
    if (!r.ok) { el.innerHTML = '<div class="loading">Error loading profile.</div>'; return; }
    var p = await r.json();
    el.innerHTML =
      '<div class="two-col" style="margin-bottom:16px">'
      + '<div class="card">'
      + '<div style="display:flex;align-items:center;gap:14px;margin-bottom:16px">'
      + '<div style="width:52px;height:52px;border-radius:50%;background:var(--accent-bg);display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:700;color:var(--accent);flex-shrink:0">'+p.name[0]+'</div>'
      + '<div><div style="font-size:17px;font-weight:700">'+p.name+'</div><div style="font-size:12px;color:var(--muted)">'+(p.username?'@'+p.username+' · ':'')+p.role+'</div></div>'
      + '</div>'
      + '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px">'
      + '<div class="agent-stat"><div class="agent-stat-val" style="color:var(--accent)">'+p.total+'</div><div class="agent-stat-label">Total</div></div>'
      + '<div class="agent-stat"><div class="agent-stat-val" style="color:var(--green)">'+p.done+'</div><div class="agent-stat-label">Resolved</div></div>'
      + '<div class="agent-stat"><div class="agent-stat-val" style="color:var(--red)">'+p.missed+'</div><div class="agent-stat-label">Missed</div></div>'
      + '<div class="agent-stat"><div class="agent-stat-val" style="color:var(--accent)">'+p.rate+'%</div><div class="agent-stat-label">Rate</div></div>'
      + '</div></div>'
      + '<div class="card"><div class="card-title">Period Breakdown</div><div class="stats-list">'
      + '<div class="row"><span>Today assigned</span><span class="val">'+p.today_total+'</span></div>'
      + '<div class="row"><span>Today resolved</span><span class="val" style="color:var(--green)">'+p.today_done+'</span></div>'
      + '<div class="row"><span>This week assigned</span><span class="val">'+p.week_total+'</span></div>'
      + '<div class="row"><span>This week resolved</span><span class="val" style="color:var(--green)">'+p.week_done+'</span></div>'
      + '<div class="row"><span>Avg response</span><span class="val">'+p.avg_resp+'</span></div>'
      + '</div></div></div>'
      + '<div class="section-title" style="margin-bottom:10px">Recent Cases</div>'
      + '<div class="table-wrap"><div class="table-scroll">'+caseTable(p.recent)+'</div></div>';
  } catch(e) { console.error(e); el.innerHTML = '<div class="loading">Error.</div>'; }
}

async function loadAgents() {
  var el = document.getElementById('agents-content');
  if (!el) return;
  el.innerHTML = '<div class="loading">Loading...</div>';
  try {
    var r = await fetch('/api/agents');
    if (r.status === 403) { el.innerHTML = '<div class="loading">Access denied.</div>'; return; }
    if (!r.ok) { el.innerHTML = '<div class="loading">Error loading agents.</div>'; return; }
    var agents = await r.json();
    if (!agents.length) { el.innerHTML = '<div class="empty-state">No agents found.</div>'; return; }
    var cards = agents.map(function(a) {
      var init = (a.name||'?')[0].toUpperCase();
      return '<div class="card" style="cursor:pointer" data-agent="' + (a.name||'') + '" onclick="openAgentModal(this.dataset.agent)">'
        + '<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">'
        + '<div style="width:38px;height:38px;border-radius:50%;background:var(--accent-bg);display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:700;color:var(--accent);flex-shrink:0">' + init + '</div>'
        + '<div><div style="font-size:13px;font-weight:700">' + (a.name||'') + '</div>'
        + '<div style="font-size:11px;color:var(--muted)">' + (a.username?'@'+a.username:'No username') + '</div></div>'
        + '</div>'
        + '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:4px;text-align:center">'
        + '<div style="background:var(--surface2);border-radius:7px;padding:5px"><div style="font-size:14px;font-weight:800;color:var(--accent)">' + (a.total||0) + '</div><div style="font-size:8px;color:var(--muted);font-weight:600;text-transform:uppercase">Total</div></div>'
        + '<div style="background:var(--surface2);border-radius:7px;padding:5px"><div style="font-size:14px;font-weight:800;color:var(--green)">' + (a.done||0) + '</div><div style="font-size:8px;color:var(--muted);font-weight:600;text-transform:uppercase">Done</div></div>'
        + '<div style="background:var(--surface2);border-radius:7px;padding:5px"><div style="font-size:14px;font-weight:800;color:var(--red)">' + (a.missed||0) + '</div><div style="font-size:8px;color:var(--muted);font-weight:600;text-transform:uppercase">Missed</div></div>'
        + '<div style="background:var(--surface2);border-radius:7px;padding:5px"><div style="font-size:14px;font-weight:800;color:var(--accent)">' + (a.rate||0) + '%</div><div style="font-size:8px;color:var(--muted);font-weight:600;text-transform:uppercase">Rate</div></div>'
        + '</div>'
        + '<div style="margin-top:6px;font-size:11px;color:var(--muted);text-align:center">Avg: ' + (a.avg_resp||'—') + '</div>'
        + '</div>';
    }).join('')
    el.innerHTML = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px">' + cards + '</div>';
  } catch(e) { console.error(e); el.innerHTML = '<div class="loading">Error: '+e.message+'</div>'; }
}

// ── Modals ─────────────────────────────────────────────────────────────────
async function openCase(el) {
  var caseId = (typeof el === 'string') ? el : el.dataset.id;
  document.getElementById('modal-overlay').classList.add('open');
  document.getElementById('modal-body').innerHTML = '<div class="loading">Loading...</div>';
  document.getElementById('modal-title').textContent = 'Loading...';
  try {
    var r = await fetch('/api/case?id='+encodeURIComponent(caseId));
    if (!r.ok) { document.getElementById('modal-body').innerHTML = '<div class="loading">Case not found.</div>'; return; }
    var c = await r.json();
    document.getElementById('modal-title').textContent = (c.driver||'—') + ' — ' + (c.group||'—');
    var extra = '';
    if (c.vehicle_type) {
      extra += '<div class="detail-grid" style="margin-bottom:14px">'
        + '<div class="detail-item"><div class="detail-label">Vehicle Type</div><div class="detail-val">'+(c.vehicle_type||'—')+'</div></div>'
        + '<div class="detail-item"><div class="detail-label">Unit Number</div><div class="detail-val">'+(c.unit_number||'—')+'</div></div>'
        + '<div class="detail-item"><div class="detail-label">Priority</div><div class="detail-val">'+(c.priority||'—')+'</div></div>'
        + '<div class="detail-item"><div class="detail-label">Load Type</div><div class="detail-val">'+(c.load_type||'—')+'</div></div>'
        + '</div>';
    }
    document.getElementById('modal-body').innerHTML =
      buildTimeline(c)
      + '<div class="detail-grid">'
      + '<div class="detail-item"><div class="detail-label">Status</div><div class="detail-val">'+statusBadge(c.status)+'</div></div>'
      + '<div class="detail-item"><div class="detail-label">Assigned To</div><div class="detail-val">'+(c.agent||'—')+'</div></div>'
      + '<div class="detail-item"><div class="detail-label">Reported By</div><div class="detail-val">'+(c.driver||'—')+'</div></div>'
      + '<div class="detail-item"><div class="detail-label">Group</div><div class="detail-val">'+(c.group||'—')+'</div></div>'
      + '<div class="detail-item"><div class="detail-label">Opened</div><div class="detail-val">'+(c.opened||'—')+'</div></div>'
      + '<div class="detail-item"><div class="detail-label">Assigned At</div><div class="detail-val">'+(c.assigned_at||'—')+'</div></div>'
      + '<div class="detail-item"><div class="detail-label">Response Time</div><div class="detail-val">'+(c.response||'—')+'</div></div>'
      + '<div class="detail-item"><div class="detail-label">Resolution Time</div><div class="detail-val">'+(c.resolution_secs||'—')+'</div></div>'
      + '</div>'
      + extra
      + (c.full_description ? '<div class="desc-box"><span class="box-label">Issue Description</span><p class="box-text">'+c.full_description+'</p></div>' : '')
      + (c.full_notes ? '<div class="notes-box"><span class="box-label">Report / Notes</span><p class="box-text">'+c.full_notes+'</p></div>' : '');
  } catch(e) {
    console.error('openCase error:', e);
    document.getElementById('modal-body').innerHTML = '<div class="loading">Error loading case.</div>';
  }
}
function closeModal() { document.getElementById('modal-overlay').classList.remove('open'); }

async function openAgentModal(nameOrEl) {
  var name = (typeof nameOrEl === 'string') ? nameOrEl : nameOrEl.dataset.agent;
  document.getElementById('agent-modal-overlay').classList.add('open');
  document.getElementById('agent-modal-body').innerHTML = '<div class="loading">Loading profile...</div>';
  document.getElementById('agent-modal-title').textContent = name;
  try {
    var r = await fetch('/api/agent?name='+encodeURIComponent(name));
    if (!r.ok) { document.getElementById('agent-modal-body').innerHTML = '<div class="loading">Agent not found.</div>'; return; }
    var a = await r.json();
    var rate = a.total > 0 ? Math.round(a.done/a.total*100) : 0;
    var rows = '';
    if (a.recent && a.recent.length) {
      a.recent.forEach(function(c) {
        var cid = c.full_id || '';
        rows += '<tr style="border-bottom:1px solid var(--border);cursor:pointer" data-id="' + cid + '" onclick="closeAgentModal();var id=this.dataset.id;setTimeout(function(){openCase(id);},200)">'
          + '<td style="padding:8px 10px;font-weight:500">' + (c.driver||'—') + '</td>'
          + '<td style="padding:8px 10px;color:var(--muted)">' + (c.group||'—') + '</td>'
          + '<td style="padding:8px 10px">' + statusBadge(c.status) + '</td>'
          + '<td style="padding:8px 10px;color:var(--muted);font-size:11px">' + (c.opened||'—') + '</td>'
          + '</tr>';
      });
    }
    document.getElementById('agent-modal-body').innerHTML =
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:16px">'
      + '<div class="agent-stat"><div class="agent-stat-val" style="color:var(--accent)">'+a.total+'</div><div class="agent-stat-label">Total</div></div>'
      + '<div class="agent-stat"><div class="agent-stat-val" style="color:var(--green)">'+a.done+'</div><div class="agent-stat-label">Resolved</div></div>'
      + '<div class="agent-stat"><div class="agent-stat-val" style="color:var(--red)">'+a.missed+'</div><div class="agent-stat-label">Missed</div></div>'
      + '<div class="agent-stat"><div class="agent-stat-val" style="color:var(--accent)">'+rate+'%</div><div class="agent-stat-label">Rate</div></div>'
      + '</div>'
      + '<div style="background:var(--surface2);border-radius:8px;padding:10px 12px;margin-bottom:14px;font-size:13px">Avg response: <b>'+a.avg_resp+'</b></div>'
      + (rows ? '<div style="font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">Recent Cases</div>'
        + '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px;min-width:400px">'
        + '<thead><tr style="background:var(--surface2);border-bottom:1px solid var(--border)">'
        + '<th style="padding:8px 10px;text-align:left;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Reported By</th>'
        + '<th style="padding:8px 10px;text-align:left;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Group</th>'
        + '<th style="padding:8px 10px;text-align:left;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Status</th>'
        + '<th style="padding:8px 10px;text-align:left;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Date</th>'
        + '</tr></thead><tbody>' + rows + '</tbody></table></div>'
        : '<div style="color:var(--muted);font-size:13px">No cases yet.</div>');
  } catch(e) {
    console.error('agent modal error:', e);
    document.getElementById('agent-modal-body').innerHTML = '<div class="loading">Error loading profile.</div>';
  }
}
function closeAgentModal() { document.getElementById('agent-modal-overlay').classList.remove('open'); }

// ── Report ─────────────────────────────────────────────────────────────────
function openReport() {
  document.getElementById('report-modal-overlay').classList.add('open');
  reportTab = 'today';
  document.querySelectorAll('.report-tab').forEach(function(b,i){b.classList.toggle('active',i===0);});
  document.getElementById('report-period-bar').style.display = 'none';
  generateReport();
}
function closeReport() { document.getElementById('report-modal-overlay').classList.remove('open'); }
function setReportTab(tab, btn) {
  reportTab = tab;
  document.querySelectorAll('.report-tab').forEach(function(b){b.classList.remove('active');});
  btn.classList.add('active');
  document.getElementById('report-period-bar').style.display = tab==='custom' ? 'flex' : 'none';
  if (tab==='today') generateReport();
}
function toggleCustomDates() {
  var v = document.getElementById('report-period-select').value;
  document.getElementById('custom-date-inputs').style.display = v==='custom' ? 'flex' : 'none';
}
async function generateReport() {
  document.getElementById('report-content').innerHTML = '<div class="loading">Generating report...</div>';
  var url = '/api/report?period=today';
  if (reportTab === 'custom') {
    var period = document.getElementById('report-period-select').value;
    if (period === 'custom') {
      var from = document.getElementById('report-date-from').value;
      var to   = document.getElementById('report-date-to').value;
      if (!from) { document.getElementById('report-content').innerHTML = '<div class="loading">Please select a start date.</div>'; return; }
      url = '/api/report?period=custom&from='+from+'&to='+(to||from);
    } else {
      url = '/api/report?period='+period;
    }
  }
  try {
    var r = await fetch(url);
    if (!r.ok) { document.getElementById('report-content').innerHTML = '<div class="loading">Error generating report.</div>'; return; }
    var d = await r.json();
    document.getElementById('report-ts').textContent = 'Generated ' + new Date().toLocaleString();
    document.getElementById('report-content').innerHTML =
      '<div style="font-size:18px;font-weight:800;margin-bottom:4px">'+d.label+'</div>'
      + '<div style="font-size:12px;color:var(--muted);margin-bottom:18px">Kurtex Alert Bot — Truck Maintenance Command Center</div>'
      + '<div class="report-stat-grid">'
      + '<div class="report-stat"><div class="report-stat-val v-accent">'+d.total+'</div><div class="report-stat-label">Total</div></div>'
      + '<div class="report-stat"><div class="report-stat-val v-green">'+d.done+'</div><div class="report-stat-label">Resolved</div></div>'
      + '<div class="report-stat"><div class="report-stat-val v-red">'+d.missed+'</div><div class="report-stat-label">Missed</div></div>'
      + '<div class="report-stat"><div class="report-stat-val v-accent">'+d.rate+'%</div><div class="report-stat-label">Rate</div></div>'
      + '<div class="report-stat"><div class="report-stat-val" style="font-size:16px;margin-top:4px">'+d.avg_resp+'</div><div class="report-stat-label">Avg Resp</div></div>'
      + '</div>'
      + (d.leaderboard.length ? '<div class="report-section"><h3>Agent Activity</h3>'
        + d.leaderboard.map(function(a,i){return '<div class="report-row"><span class="medal">'+(medals[i]||(i+1)+'.')+'</span><span class="rname">'+a.name+'</span><span class="rcount">'+a.count+' cases</span></div>';}).join('')
        + '</div>' : '')
      + (d.top_groups.length ? '<div class="report-section"><h3>Top Groups</h3>'
        + d.top_groups.map(function(g,i){return '<div class="report-row"><span class="medal">'+(medals[i]||(i+1)+'.')+'</span><span class="rname">'+g.name+'</span><span class="rcount">'+g.count+'</span></div>';}).join('')
        + '</div>' : '')
      + (d.missed_cases.length ? '<div class="report-section"><h3>Missed Cases ('+d.missed+')</h3>'
        + d.missed_cases.map(function(c){return '<div class="report-row"><span class="rname">'+c.driver+'</span><span style="color:var(--muted);font-size:11px;margin-right:8px">'+c.group+'</span><span style="color:var(--muted);font-size:11px">'+c.opened+'</span></div>';}).join('')
        + '</div>' : '');
  } catch(e) { document.getElementById('report-content').innerHTML = '<div class="loading">Error.</div>'; }
}
function printReport() {
  var orig = document.title;
  document.title = 'Kurtex Report — ' + new Date().toLocaleDateString();
  window.print();
  document.title = orig;
}

// ── Refresh ────────────────────────────────────────────────────────────────
async function refresh() {
  await loadStats();
  if (currentPage==='overview') {
    try {
      var r = await fetch('/api/cases?filter=today');
      if (r.ok) {
        var cases = await r.json();
        var el = document.getElementById('recent-table');
        if (el) el.innerHTML = caseTable(cases.slice(0,10));
      }
    } catch(e) { console.error(e); }
  } else if (currentPage==='cases') loadCases();
  else if (currentPage==='missed') loadMissed();
  else if (currentPage==='reassigned') loadReassigned();
  else if (currentPage==='fleet') loadFleet();
  else if (currentPage==='my_profile') loadMyProfile();
  else if (currentPage==='agents') loadAgents();
  var lu = document.getElementById('last-update');
  if (lu) lu.textContent = 'Updated ' + new Date().toLocaleTimeString();
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
    is_manager = user.get("role","agent") in ("developer","super_admin")
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
