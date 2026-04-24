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
body{font-family:'Plus Jakarta Sans',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;overflow:hidden;background:#1a1208}

/* Background slides */
.bg-slide{position:fixed;inset:0;transition:opacity 2s ease-in-out;background-size:cover;background-position:center;opacity:0}
.bg-slide.active{opacity:1}

/* Dark gradient overlay - left side darker for card, right shows photo */
.overlay{position:fixed;inset:0;background:linear-gradient(105deg,rgba(20,14,6,.92) 0%,rgba(20,14,6,.75) 40%,rgba(20,14,6,.3) 70%,rgba(20,14,6,.1) 100%)}

/* Card on left */
.card{position:relative;z-index:1;width:100%;max-width:400px;margin-left:8vw}
.card-inner{background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);border-radius:24px;padding:44px 36px;backdrop-filter:blur(16px)}

.logo{width:60px;height:60px;border-radius:16px;background:linear-gradient(135deg,#C17B3F,#8B4A1A);display:flex;align-items:center;justify-content:center;margin-bottom:20px;font-size:28px;box-shadow:0 4px 24px rgba(193,123,63,.5)}
h1{color:#fff;font-size:26px;font-weight:800;margin-bottom:6px;letter-spacing:-.4px;line-height:1.2}
.tagline{color:rgba(255,255,255,.5);font-size:13px;margin-bottom:28px}

/* Stats strip */
.stats{display:flex;gap:20px;margin-bottom:28px;padding:14px 16px;background:rgba(255,255,255,.06);border-radius:12px;border:1px solid rgba(255,255,255,.08)}
.stat{text-align:center;flex:1}
.stat-num{font-size:20px;font-weight:800;color:#D4904E}
.stat-lbl{font-size:9px;color:rgba(255,255,255,.4);text-transform:uppercase;letter-spacing:.06em;margin-top:2px}

.divider{display:flex;align-items:center;gap:10px;margin-bottom:20px}
.divider-line{flex:1;height:1px;background:rgba(255,255,255,.12)}
.divider span{font-size:10px;color:rgba(255,255,255,.35);text-transform:uppercase;letter-spacing:.1em;white-space:nowrap}
.tg-wrap{display:flex;justify-content:center}

.error{color:#F87171;font-size:12px;margin-bottom:14px;background:rgba(248,113,113,.1);border:1px solid rgba(248,113,113,.25);border-radius:8px;padding:8px 12px}

/* Right side caption */
.caption{position:fixed;bottom:40px;right:40px;z-index:2;text-align:right}
.caption-title{font-size:22px;font-weight:700;color:rgba(255,255,255,.9);line-height:1.2}
.caption-sub{font-size:12px;color:rgba(255,255,255,.45);margin-top:4px}

/* Dots indicator */
.dots{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);z-index:2;display:flex;gap:6px}
.dot{width:6px;height:6px;border-radius:50%;background:rgba(255,255,255,.3);transition:all .3s}
.dot.active{background:#D4904E;width:20px;border-radius:3px}

@media(max-width:768px){
  .card{margin:0 16px}
  .caption{display:none}
}
</style>
</head><body>
<div id="bg1" class="bg-slide active"></div>
<div id="bg2" class="bg-slide"></div>
<div class="overlay"></div>

<div class="card">
  <div class="card-inner">
    <div class="logo" style="background:none;padding:0;overflow:hidden"><img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAHgAAAB4CAYAAAA5ZDbSAACD7UlEQVR42uz9d5idZdnvD3+uu62+ptfMJJPeO0lIICR0KYKU0AUEKQIqCIqCGiKCIgoIgkiRLpDQewmE9F4nPZlJJtP7mtXX3a73j3sSQPHZz/7t57f3s9/3XccxRw7IZN3lvK6zfK/z+z0F/xd9pJTiggsuUDo6xoilSxdIwPnq3yuAz2eQzua0L754v/iLNbXB9999V9+5q2mYbR/+VRsQ0uczxLDqgY2nnnpGcuLEGuuiiy5qDQcDdjabxZH/cml1zpz5orR0rFy4cJ4rhJD/t7wz8X+HURcpixY9KmCpfeSNC7Bd6Xvlg8WD1m/cOqaltWt0KpMdlcrZA1NZqzLWmyjKmrlgPJlVrJzpQ7re9/U/sVAUDE2zwwHdCvp9Vjha0JYf9XcXFYQbDcO3q7QgsnPG1PG75p1+Ur2uKinb/ZpN1XkLF7Jw3n9/Y/+3NfC8efPURYs6vmZUKaXv1w8/NW7X7oOzurp75/amMlNjffHqTM5WsqaD5YDrSqQjkYoACQgBwpVSSqSUCKF8+dgCIZEICQKBUEAVCoaq4NcFAZ9GXn5eS2HEv7U4v3DFpDFjlt/xw4u2qoqIf8Xe6rx5C1m06AIX74r/fwP/R/czb948ZdGiRUdelpQy/KuHXjhpxep1p3R2dZ0cS2aGpUyFnO1i2w6OdHBdx8GWEscC6QiEEEIVQtN1NF1D1w2haVq/sUFKieO6OKYtLcvCsiykbUvcfrOpikTzC0XVVVUR6LqOYWgENSgJ+VqKi/I+O2rGlMW//8lVHylCdBy26pw5c7QvvvjC+e+0q8V/Hzd8gbJo0SIHIOD3Mf/3Dx+7bOvOCxsau87sTZg1sWQW23ZwpYNj2ba0LQEI1aeLgsICUVpeQllFKaUV5RSXllJYWEQ4L0ogHMTv09F1DVVRkP0Gtm2bXMYmlU6RSiTp6+6ho72D9o522ps76Wxrp7c3hplJS6R00XSpKJqqKoZQdZf8UIDi/FBHdXnRh7MnjFj4i59c/6kQwur3P+r8+WPkggUL3P+fNrCUUsydO1ddutRzw1LK0MU3/eL83XXNV3f1pGbHMhambSMdx7EtS0rHUvRAQKkcMIDhI4YxdNQwhgwfQmXVACL5eQR8BkLVcBG4roPrut6PdJGeP/jygYVACAVFOfwjEEIgHRc7ZxLv66OttY36/QfYs30X+/bW0drcgplOS6Hqrq4ZuKqiGrpGvl+jJOrbPmRw6d/vv/3Kl4YNm9TRH2dUuXDh/9E4/X/MwF6M9XaslLLw0h/96rra3fXXNnb11aRzCtJ2pGubjmNmFSPkV4YOH860o6cxefoEqoYMJJxXAGjYjo1lmbi2g+u4CAkIiUB6RgRcIb7ywMqRUCm9ax9ebf0h23Pjmua5d03XQEpS8QSNBxqo3bSFjWs2sG/3fjKJtMQwXFX3CVURStAnqCgId44fMejFW6674uGZk0cfPOy6ly5d6vyfiNH/2w08f/58ZcGCBQCulDJ65S2/uWF97e4bm7rjVemsi4Z0cpksCNSqwVXMOm4Ws2bPpGbEcPzBEKZpkTNzuI7jpcSK9xAqAlcoCCRCOl97Mvfwowov8XJcAUhURSK8f/G1d6949kYCrgSJi6Yq+HQD3TDIZNMcOnCADcvXsuKL5RyoawBXuj5/wHUUVfPrkor8YPyosSP/vuCOHzwwvKqq0VvUC9VFiy5w/r/XwPPmqSxa5PgMnYtuuPW7G3Yc+mVTd2ZEKplBFdLOZVKKYRjK5OnTOOmsU5h41CSieXnYtks2m8VxHIQQKEL5H965FF/+ivD2Jpbj4jomUcOHokIqZ2FJDZ+uIuVhI0tvF389lIAEV7q4rkRRBX6/H103SMX72LFpK4s//IwNq9eTSaWkLxhyXOlqPr+P6hJ/59Qxg//4wp/ve0gIYQKqlPJ/m9sW/xt3rQvw+HPPHfXi2yvu3X2g4+S+jIsiXNtMx1XdMMSMY6Zx1oXnMnbSRIQqSGUyOJaLomgoype77MuSR3zjA8n+P10JjutiChCmRYVfMKWmCM20sGyLYDDInj6TfZ1JFM0PuMhv8KJSyv7riyMu3XVdpJSomkYgEAQJdbt38+6iN1nx+XIy6ZQ0QnmOdKUWDiiMrC7dct63Trjj5zde9qEjvx6i/q82cH/8saWU+kU33PaLjdsbftnYk9KRiuNmc8KWpnLUzGnMu/RCxk6djIMkk04jXFAU5UgydHh3/bOBPSfret5XguWCY9toUhA0FKJBg5BPocCnMTig8ugzr/D0ok/ImTmOGlrJ3b+6mWSokPUtSXx+P4YqUJEgZb/L9xI0oaiA/DJmf+XjOhKJxBcMYGg69Tt28vrLC1n+xQpcS5V6MOQ4Tk6rLg4xbeKIJ1/9yz0/F0L0MGeOxtIv6/z/qwwspRRCzFVhqf36Rx9NfPSpNx7ftKfl6JRpowrpZJNJdeiYUVz6vUs5es4xuIognUp7MVBR/vVG+3frP79gBYktFEzHRnEcykI+hhSFKPUJwKW3q4/WlhYS8RSfLd/IPQ+9wKgTTyKvvJiNn37GxMogaz5+mW4XGmNZmuMZkpaDrmqoqkZ/ruZd6ci15TfmS162LgkEghiaytZ1m3jx6Reo3bQNIxB0XSSGLpSpQyvrL/vOSTddd+XFHwLK/Pnz+X+rpBL/b7pkVYHrfvq7qz5fvfnhfe2xkK4qdi6dVQNBnzj/kgv4zkXn448GiSeTKFJ8o2H/wxsXAst2UKXDqGI/I/KDZFMplq5Yz9ufrmD95i20t8XISgXhC6IYOv68KFbOxkwlEFISa2/mO6fP5dxTjmPWtIkMGFRNnwvbW+N0pkyE7kNVVFTp4iL6F9rXvcnhBfjP7jsSCuFYNp++/zEvPP08vZ09+IMR23RMbUh5iBNnTrn7hYfv/XU6k2HewoXqogv+6xOw/3IDH84UpZTB8665/c8rNu/9fmcsQUBXnXS8Tx03dTLX3HYjo8aPIdGXxnUcVFX9xtj3z3H1ywRKIqWKbeWoiurMKItwsL6Bx55ZxMIPv6AvC8PHjmLY+AnkVQ5Cj4QxhY+MLUlmbRKZLNlEL3YyQy4Vp3F3LX0NBwmJHHOmjOay80/mjJNmk/MF2dTYTZelEtD0I3X0YTzb893i68nYV+5aOi6KEATygrQfauaphx5n5ZJlBMIBN+cI8kJhZc7UIR+9+efffF+EQs1z5s/Xli5YYP+3NfDheFu7b1/1Tbf/4aXN+9tnZ03Llo6tuq4lLrniQuZdcTmOXyObSKMr6r/cwVeTJynwcGIJEnkEWpYIXDfDtPICCpwcv/r9ozz+zOsUDa7hjEsuZvTUqSS0IM3dGZp7UsRSOSzbwkGiSAWhCoTuQxcCTbioiiAVi9GxZQMNKz+DZAcjayq4/YbLuPTic2mI22zsTKFpWv9OliCUr4WOf8m6v/JxHAe/z4emaXzw+pv8/dEnyZkS3e+zFEXqU0dVHbjl2ksvPO/kuesPv8P/dgY+fGN/fvqFMc+8/vH7uw/21kihWLlsXC8tKuamX/yY6XOOIZZIgeOiCeU/E8dxpYtQVXyGgSIEluNg5dKcNiiPxroGzrrydmJpi3Nv+gEDJh9FXXeauqYOkhkTXWj4NA3HlaQcUN0swvCjKgLVtXCFiitBSIkqdCzTJlW3leTujfgi+RzasZW5Ywby2P23U1lTw2d1nVi+MAFdI5Pzyja1Hw37j4ysALaU2LgU5EXZtWU7D//2fg4ePEQgWmhnTUubOKQ0fdkZc8+97YYrPp4zZ762dOl/zU5W/yu+ZOq11+qr33jD/t0jT879+6KPF++oby8zDNXJJOLa+InjWXD/AkaMH0NPXwINBfWbVv3hP10XR0qEKggGA4TCISzbpr7+AE1NzaRTKb41spIDe+qY/Z2rGDtjJjf94R66jCi7DnWy41APGgZC07AcSco2KQoKTqj2cfXUcgqCktr2HIam4wKKVBFCx0aiqw4ykI9jqkhpMvXc77Jtz0GefOgpJo6u4lvTxrJh/wEamtrILyggLz8PTVOwHQfb8bJt5TCg8pWgchghUxGkM1kGVFVy4slzaDzUzIHd+5RwKOQeaosZh5pbLrv55puan3n8tg1Tp16rt7ZudP+PG3jO/Pna6gcesO+8/8G5L77+6ft7G7vCQZ/PScX61Lmnn8yd9/yaYF6ERCqNpmr/6jIk4LpI6aIYGsFQkGDAj5VJs3vHLj59/0NWf/gR8fr9dO7ZQYliM33EEI45/RLGzjmBC2/9OY29cQ62xmnpyxHxa+RsC93KMqnUz42zKjl/bAkzqkLMGFTJkvputrebBBQ/Lu4RJ+ZhYBJdF7halGRbIz0dXQw7/mRkIMSjv/k9I4cPYPrwQbzy9N9Zu3oNe/bU4zoORYUF5Bfko+kGju3iWrZnXsVDW76Kf6uKgpXLYQT8nHjKXJLxNNvWrxehUMBt64rJhobGs79/3bXNr71074ap116rt278XzOy+K9wy7975K9zn1r46fv1zbGg36e7mXhMOfe7F3PlzTd4sKJtowqtP6i6Hu4rvXNYXdfxBwPYCnR1drF32072bttKsr2FquJ85kybwJzpk6kaUHHkupfeeCefrNvJL597nleW76S0IEg2J+hLmziuy/E1IW44tpKaSJgle1rY0NDMeZNHkh8JcMkrW2jJBAgoKhK7/w24/SCGB0nGuuLkWptJ1m3GXzqS4tE15A7tZuc/nuTTt57kpNnTaWltZeWGWpZtqOVAZy++gmKGj53A8InjKS8rQUWSyWWwTMvLI4Q4Eq+FlLiujVAUwuEw/3j6RZ5/7EkCkTyZNS13YFm+euk5J1xz7203PPW/GpP/Hxt4/vz52oIFC+xnXnh57h+fffv93Y2xoE9T3HSiW/nuNd/n4uuuoi+VQpN4h++uBNdFKALdMAgE/AhH0tHezs5tO9i5bRtmTzsjBpRy3IyJHHPUJEpLio5cz7QtVEVlb30D40++jJ/+7j5aopVs2NuG4fODEGiqinAtKiIKxwzwUx9z0J04d582hfyAn9VNHVz/Vh26L4TiSs+w4isBQoKiqph9CXqbW8js34QaLUEG8qkaXk1n7VrcXavZ8Ok/KCnIP3JvXT29rNlUy4o129jR3IaSV8jI8RMYM34sFRVlKIpCJpvDzGWPGBvhgqtgYxPNK+Ctlxbxtwcfwh8ulKYl3eHVxepPrjnzmusuuuCpw+/6f5uLnrdwofrYTTc5Cz/7bORDTy1aWlvXHfL5fW4mnVCu+eH1XPT9K4nFkwjpgX8KYPj8BKNBFFWnramRNcuW88m777J77SrKhMV5c6Zwy5Xncdapcxk9fAihUBDHcb5ErKSNpuo88vRCDsQynHHFFSypbUQYAVSh9CNZEiGgN+uwqd2krjvH2IoCjhscxa/pfLijnaXNJiFdQ3ElUnH/ZblLJI4L6b4kMpfAzeQIlpST6EpSPXk6ezdvQyZ7Oem46WRNE4FLOBRixJBBnHTcdL5z/HQGRAwa9+5m5ZKlbFi7ga6uLvSgn7ySIsLBEEKoOI6D40qEUMnkskyePoWSwkJWLVsjfIGgaOvucTs6us9+6KE/bbzxmu/tnr9kibb0uefc/9d38GEQo6ura8AZV/90+dodhwYH/CEnk+hRr/zB1Vx2zZV09/ShKAJ/wI9mGKQyORoPHGTPtm00792D38kyYXgNxx89lemTxqD7fEe+37ZtRL/BjpRLgMDBlYJJp1zOxJNPY8BxJ/Px+gMYft1rt+qHNKXwYp+i6GgSelJJRuW5/GTOYJ5c08KmDoewoXlOWXH7s90vX4ODi7RtuuuasBM9OB1NRAaPwcxm8RcXYaQ7aV+8kG2LX6CspNgrhb2jMaR00FTtyPdZtsWmbbtYsnYjW/ccICl0yoYMZ+zECVQPHkwoGMA2LcxsDtvOUlpYwNuL3uXPf3yEYCgi06msO2PCoMzDC246ZcaECasXLlyoXvA/CYb8TxnYgx+FIqU0vnXZTYuXrN81S9d8Tqq3W/3OJfO48ee3kbZNQj6DTDpD/b6DbNu0icZ9e4kqNrMmjuKEWVMZP3YkivjSeZiOjSLEkZ14+LaOIEOORNUUdu/bz5TTvsdPH/ozm80gLX0mYd0ACbadw8nlsDMZcrkMip0DoaP6dFwEmuHDiETR9QAW4FoWjmMhLIlUHO8M2RU4igKORff+JqRpkj20h9Cg4VjCRclJSgYVs/GZB3nil9/n+5dfiG3bnlH7Xb2XM0pcKdHVrzvIHXv28fmKjayp3U131qJ06DAmTprMiFFDCIXDZHMmPp+fF594hmf/8gyh/KibyaSVk4+d0PTRsw8dI4RonC+lWCDEf3ona/8zBp479y5VVYR96Q9/+ZdVtfWzhKrbqVivNuf0U7n5rttoaYtRv2sfuzZvo7O1iYqIzjEThnPbWZczatiQr6wnF9uxUfprSEPV/rX+dSWK4rkwkDiOw8oNW4gUl1E+fBTdn6wj09RMe0sTqe52nGQM4WbQpUQ4LkgXywXbEQjVQPr8aKEAofwiIqUDCFYMwCgoRfjDSNfBsR1sbKRwvYRQCFRNBaHg2CZCM7DcLBnTJW/oCD5Zto7vX37hESBDURUvkQKvzj78pK7EdT20buzI4YwdOZwfAnUHDrB83RZWfv4Ri9/OkFdaztjxkxg2aijX3Pw94rEEbzz3DyVcUOB8sXZH1VW3LnhFSjlbXHDB4Y0m/0sN3I+V2rf99uErX3p/+VVpU1gyZ+qjJ43hwssu4qF7HiHb282oAQWcNXkEx117JuXlZV85dHex7X6jCgVVUf8F7Tvc86YoAlUVWI7N/oZ6QobBwKoqtu46gKMYPP/HP7Bp+RqK84JUDh5C2fghBEpK8IcKMA0/aRRAxZUulmWRzqTI9vaR6u4l2d1Fx7b1ZJa/B5pGsKyGouHjiQ4Zji9ciCIcLDOLtB3QVFBcpJUB1QBFYGUsokNGsWPPKtK5HH6f4dW+/4RLf1n/eomblB5G7UjP2EMHD2bo4MFceSH09nazcu0Wlm3ZzEuLP0ILRTjprG/TdOAg61atVY1gnv3usi0z73r4ifvEokW33XXXXRpeg/d/jYs+HHfXbt489vu/eHDdzv3tPs3QlKBPFX9+4iGef+p59GyK44+bxvSxIygtyUNVFFQBAb/fAyz8Brqu/gdr6stY2B3rY922XXR19zB98lgUIfjL06/w3CvvEy6pYMzUMUyceRwFg4Zh+iLEsxaJrE3GtrFcB9dyyeQs0rkstu0iFRWhGqiKgpAObi6NGesh3nSQ7h3b6G1vQdF9FA0aR/6ESUjFR6KrG3DJHqjFKChCiZbj2jkMI0ggCG2fvMCGN59g0569REJhpowcStGRzFr+h6/Xtiwy2RzJdIZMLovluNiOoDeRYsuOfXy8eBVtVpZbbv8pP73hVno7+mROus6o4RXa/b+4+pwzjjvurf9sPP5P7GApFuy8QEgp1TOv/unzexo6goZPdbKJpPjJL+6grGYQBi6/vvVqWrtivL92C9KG0sJ88vKCBEMGfsOHX1UwdAVdQCAQIBwKEPL7yIuGCfl9ICV7DzZRW99CXWMTY4dWc8bJx/PMC4u4/6GnCZZWcfZNN5JfPYrelMLa5lY61n5GX1s7lp1D03SCoSCR4mLyKirIKyggL+DHDSmkczaJVBbHtFEUBVVVIVJCwcRqSqccSy7eQ+/u7bTVbqJr0SZC5QMIDR6FP6+MjOrDdQUGYCsalm2RH4iQTOfo6OhG80f4bP121u3cT2VxPlNH1DBicDVSCDLZHKlUhlQmSyprkspkiSfSpHIWOUdg2xauA7msTVdvN9lcminjx/CLW6/i1w88QTA/j1vuuIWf/+iXIuAPKjv3t7qPP//uk1LK1UKIzq82Uvw/NvC8eYuURYsWObfM/9OvVm2tn4Ki2plEXDvjvDOZ/a1TaOvqIeg3GF5dxuYtWzGsDJu27+fz7hiOC7ruoyDsozA/n2hREdH8KEFDJRIOkp8XJZvJ4NMUuntjOEYYQ4Wzjz+aQRWVnPfdm1i1aR8nf/cKgjVD2Lp1P40f/J1EVxtWLIYqHIqL8on6A14nZCZDs+lgqgZqKELegEGUDhlBUVUV4eJSnJBBLpvFyZloigZWBtsS6L4wZUcdS8HQCTRv20TPjrWkvniPyLCR+H0RZCCCpL9p3pIIXceRGgdbWjn2mJmoaKQyGdqSaRau3IH62XoGVBRj2Tb50TyymSzZrEk8laarN0FPbx+9sR7SqQTZrIlpemfaaTNHQ109j937c/KjIdp7+pgyawYXXn4B/3j6H4o/L9/+fO2e4lt/86e/6Jo6b+zYser/0g6eP18qCxYId1fdofEX33Dnr3p7ex1NoA4aUsMVN1xLKpNBuC6G32DHnjp0TeP6eafyxcASECq2K0mnTTp64iQTKbrj3Wyv28PkcWOI5AXo7e1j9oyJJJIZ4hlJpcwyd9pE/IUFzPjWJfTp5cy95efsqN3Dobcewc30guMiHZNjjj6KucfPZUB1JYbPh+NC1rSI98Vpb22lvq6evXsPUPf2OvbqIYqGjaRy4mSKBg3G9flI9caxLRcLiXRMsokcTiaNXlBC2awzybY30Fe3i2S6hfxAAH90KK5p4ZhZDx/x6fR09xIM+Ajn+amMBqmq30RzYTV7DB9DBlUTDhg8t+gj1mzZi2nZuI7tQZWWha5F0BU/hj+C5vchhINPzbJq0x7qG5pQFA0hIJnIcOn3LmPrlm3s2rJLc1XV/mDZpvPf+Xj5vNNOnLXof+Sq/0MD79x5gfD7DHf+Hx9/eMehdj3oNxwzkxHX3nQ9kbx84skkqgJSuuiGj4eeeoPCggJcM0dzcxtCERiGRmnQYFBBPhXlQ/nWad/mQHs38WSGR558idNnjaVmxCB21h9iVNVAiovymXvZD2mReYyecwrLX38bs2Ufuk/DUnUiER8XzfsOR00/Clu4mKZJxrSQQqDoCsXlxVRWVTDl6Glks1kOHWigduNmNmzYyNba1URqxjPoqFkUVg/Ckgrp3gTZRBzp2AgVbEeiBkIUT5xJ5YhR1H6wiNYVHxMpG0rBhEkoehApdWQgj0Qyg1DBzbnkdB0RLaJacWiJ+Jk8fAg9sW4WvvUReYXFqDgEA35CwRBFReNR9SiWk0HXdYQQdLa1k+qrxXYcspaFqmr4FAXXdZB+H9fdeC233XgrQlWVPQ3t7hOvvPMnKeXH4q67kv9RVq39D7Jm5y8vvHHxfY+/PFe42Jl0Rjvl7FM5au4sEvEEhqYiTS+lUBWVA81drK/dz69/9F1SGZNcLktfrA8zlyWRStHVk+Dnv3+UT1duoisWRwiDay79DhWKgm1nGTdjKs+/tIgNO5oZddoFbHnzRcjE0IIBHEcQDkruvPVGSioG0NkbA0USiYaIhEP4NBVVEVi2S85yydk2hmEwatxYJk6ZyrkXzGPzpq0sXbqSus/fo7WikrIRoymqqIKCQqTllWKO5WClYrSsWkPHnu0MLMrj9B9cye4tW1ix5HWCQ6YxcPhQDF+IjGWiC68Zz9Ic1KnHYefisGMXqiJJJNPkRaLouCRTSYQQaLokZ5kU5IGBn+7uNJrm0tfXgG2lUYT00D9FQcE7nEgmk4yZNJ7zLjmXl59+WQmEI/aK2v3VD7/4xp0sWHD7Is9VO/9pA0sphbjrLimljJ582S33trT3SJ+mK6HSQi65+gpyZq4fafIyRkUKJA6K4uIP6Ph9fg42NJCXn8+QQQPIWQ7BYJDn3lrKQ0++QigcRlV1IlEfmuYdHwYMHZ8qWLt5O77SKmJ7tyByfei+ALbrkMskOfmY45g0uobWWB9Hj6+mqCCf3r44LW3tNB1op72zi85YrD8kOORyFqZleQiTphOORCkbUElvPEn7vloSB/agBQL4wyFU3YfMmeSSKWwzSXlJAZedfQqzjj2K4oJ8jp4+hRkza1m46B0Ofv46bqwTRVGxbBdX8SBSN5fAscF1VEwpsaSC49gEAn6MQATVF6IvnaC9axV5TQUIBA6SdK4P00wSjURwTQWnn5Hh9AMnhqKRSqc5/5ILWbFkFR1NrWpne9J955MVN0kpnxJC7JdSKuIbAJBvNPDcu+5SWbDAvr9s2A279jXV6KrPTqeT2sXXXELlwAHE+uKoqvplk6n08GahaF4juZQsXrqOdWvWEQ4aTBwzlut+cBWWaxIpKiHk92NaNo5rYzsupoSs6dKXTHHmycfx3Kvv0qYGQDrk9DTlA8rpyGU5ZfY0hlYVMWpoBb29ce7746N8smwjLW0dSNvqP61SQPdq1n+pBl0HFBVfIIBuaCBN3FSGZKILUHBtm+LCfL573ZWMHTsKHZMR1SUk0ha7uluZdvQUKgZWc+99fyZ7cB9FxYUE/QY4FsI1PDSs/x07SCzH8bq3lMNIl8B1XLLpGNlUu9fIJ1Q0w4+mGwjdQGg+NFX7kn+hKAjhYls2kfx8Lrz8Yv44/x7hC+c7G3bUB3//5It3K0JcdMEFi5T/1A7u9+eOlDJ6+hU/ubm1u8/VVKlUD6nmtLPPJJHOoPSDFPQfJriuC65EoHg3KgSZYVPZvHkfva3d5M2sAikxNBWhKthSomo64GBZFq5rYygquxqaOOvkOXz62uN88sU6fH4feZEQi1dtZXVfLScdPZ6iwny27NjDJdfdTmNdB5GKMsaOHU88mSCTyWKaOWzHRgr133RmeiiZlC6gomhaP1ChgOHSm0rz7DPP84OrLuWy805BdT0GRX40zO59B3nwwb9QlF9AqqyEp575B8cfPZmxwwezq64Jw6d/eS3prTFFUXBcByuXwadJFOmgaiqKCONKx0vYHBdFZNEIYqsKigBNUT0jHwF/FOKJJLNPnsv777zD3u171D5Tcz/9Yt15juuOFULs+KZd/C9Wv+uuu1RAPvHi6zds3X+oTPUZrmlmlfMvOI9oQQGOZaEgcN1+no8U/V3/AtuxcYVESvBlYowoyWP0hAkQ8gMS05YIM4dwTRTXQlEVNM1AV1R01UOfdh5sYszYMfzhzh/y46sv4JV3P2bH9h2U5IXIL8jDcSS/+/OzNNY1MXTCaC67ZB4jR48gbZkkcxaWVHA9CmE/5On+08/hHd1PNnNdXMcGXIRtoykK3bEEf3niaXo6WtH9PnyagpQKjz/+d46eNJLXnv0zr/3jr/gCUeaedgnpvjilJflYOQshQFMFuqqgCRXTMlGkiaE5OGYWVVfRdJ+HjWs+dN1AUUAKQTwep6+7EyHUfg5Vf8+akAghETj4/QYXXHIB0nGEz+9zN+w4oN3/5Iu/1FSFCy5YJP7DHfyV3Rv4zlW33tTW2iM1TVNqhg5hziknkk5nPArmv+x6z855PpV4Vxwh4PKZIzln4iB6+uJkrDQIBSG8eChdG9My0YXXP6wpAqGqaIpC1hXsPdBI4bhhzPv+z6gqL+eGKy/m939+imhhIZu27WLZ+lqM0gomjBvD9q2bWb5uA5HSASAU7EwK1WfgSvlvcCRxZDdbWRM96EdVFLLxBGpAQ7iCUDRMT1eMNz/8gp/9+FqSqTSbN2+lp62Jh/7xJ4ZVF7Bj53ZiikZH0mXB/Y/zt0fuoqe7F4THVES6WLZDIBCgs6uLTMYkGo7gRyGZzOAIEEKiui66dPAH/YwbXMXUMceTy+VwpdtPp/ryKVRVIZ1KMeOYoxkzeQK7tu3SchlXLluz9XzLdn4phKj/512sfcPutV/9cPG52+qbBqi66pjpjHrKWacSKIwQjyU8FAhx5JxcuhLRf+R2x61XEwkIPl6ynPxohGg0SkFeHuVGOcl0BpmTZDIO6axDWX4eNVXlZE2TnJT9rsjbUdXlxRxqaWftlp1sXvo673y8hGgoSFBR2LW/kWQ8QfmgwbR0dLF+1QaOvuACJpx/IZmuXj5+6I/EDjWi+vR+N8w3umorl2PA6BHMufYaslqQDc+9yMH1y/EFgkgXhGGwZddeTDOHruvs2LmLSRPGU15aSkdfnPsfeobh37mEY86+gDcfvJ9MXx/50ShdfUksy8IBkpkU13/3HI4eP4yPP1/Btr0NOFISDYbIi4SIRkKUlxQyYnA1xYV5VJQVsre+FaU/i3Zc6XkYKVEVj8XhOA5qSOf075zOjo3bMAJ+d+2OA9pTiz64HvjpXM+G32hgsWDBAldKKa792d03NXckUIH8AWUce8IJJJNZFOGRtIR0UBA40sHn15Cayrsff0bVwIFcfN6Z6ELDcV0ONjXS1deLtHoxLZOAH35102UMrK6mYmAFm7btJZVKY1kS0/Y6pCSS4miUz1aspbi0hPy8fLbsrKc4Pw8BxPriIBVwJfv31RMdOpRf3/I98qM6z7aUMvHss1j80J9Qhe/wMvxXA0uJhcLl11/KZadN4E+7kxx19TW076vFSWa8QwYk3T1J7+BBqLS1tzOipprmvhwNe/fR6fo449hjkH0J0jmL+oZWissraOuJewco0it3dE0ye9pkZk+bTDyVIZFKMqC05BsQeEhkknzw7Os4lklOul6Pl+ug9n+XkMJr8U2lmDFzBtU1VbS0dCldnV1y5bqN35VS/lYI0ffVuviIgRcuXKhccMEFTtpKH71938Gjbct2XctUv33yXCoqy+mNJVA17TBHA0eRFORH2LtrD1vWbaMh3kr58OFs3b2X0rwiigvzQFMYUF3JiMEDGViYT9WQgVQXFtHa3cvu1jg5VxAMhQgHdGzXxe5PflRNp62ji7xoHi6Qzln4A37v7Ng0AUEqmSKbTFA+bBRxoZKLZ0hlBXkV1ahG4N8ycQUC17EJ5uejFpZjxtOIdJpAQSHRkjLaYnsIaEGEopDJWDiuxHYk8USCouI8/H6dxo5u1JJyhKHTmMhgaT66euKUV1Xj4iI0nWA4hE8Nks59ucgMTUE6Dol0lt5YH3UNTRxsakEXgu5Ygr31h/hszVYiQYPO3izfvtwkFI6S7EugeYwpEALbdsjPz2fuKSfw/BMvCM1nOFv3NZRtq2+YBzz1xRdfqIdPm44Y+NEdOwTAY08uumzPoU6pKYqrhMLKnJOOx7ZM7ySmP+A6ikrQp/DG86/QunYVdx09iBNHHUfjwWZaGw8Q27mdWCpLn+2wzrZZYtrYhoET8JPR/XQnMwzOy6PVyvHpOwFO//ZJRIuLsF2PIy2FIJPLoUrQNYGiqdjuV6mcXhu8lBJVwIedCuks+PM0ECoKsr9H8psjsONKgprK7ozOH/aYuDKAUCTSESju4erTW2ze5SSO7eL3BUEK+hJpFN2gpSdBWyKNqqlI6eAIieXYuK7kpp/9nt6uNhw0rt+3h0jYTzyVJZ7KYhg6ulDw+zQMn5/CSJCCojxOmjONG648l9HDhvL6B4v52x8f4KLvf4+asSO9DdZPgFNUjYxlc+yJc3lr4VuYps2euhb50SfLvqtr2lPHH//F1130V5Kr0PnX/Pys3lhSgKtMnDCRIcOGk83kvmTX4RD2+3jsT49R1byHBWGVsp0bsXaspSprMdh10PvPQIWh4QZUTMsllYkT73UxUekIhLB7etBMuLMhyeKiUi4571QUIVCkiyttgoZOPJOlrjWOdBUs1z3SetrffYzQwIz14NcUUj4DTVfJJftwcjaa4fMa/f6ZIIZE0VRyySRkM8jCAbjZNMJMYyZjqJqGFN4i0oXbL9xio+oGqmbgKmA5kEplae1KkbOyKI6LP+TDsRx0TSWVSrPovcX88a4fM/eoyTS2dRD2aUSiUYrzQ+RFIh7l9N98HMdm3pmnMG74EG6+9y8cd/6FTJ01k0RfAq2/jLKyOaoH1zBh4nhWLVuluki5YdueGaZlDRdC7DucbCkAi/pJ7TsPHDqpobO3WqiaI21HmTFrBprP8LorpMB2XULhIM/85UnGdR7kx0YGZe1KRLwPN5XBki4ZRZBAoc+R9CRNehM5UimTYMaiIhxGxYbGRnYcOESyt4sBUZVgUEdVvCxaFQpmzqKmupyeeJLtB3tw0Ekmk/1uTgMhcQHdF6Kr6RBNq1ZQFAyhx9Ps+ex9FCERivY13tCRHawIVF0jl0qx64N38Ms0gbCPxi8WE+/sQDV0L35Kiaap/YRz8AcCNLV1cqilA5/fwE6lMFM57K4ulFyS4oJ8Ulmvod62LKqrBzBo8BDa4znKqisZP2k840YMoby0jEAggGnmSKbTWI7jqQU5rodg9Us99fTFGT1yGM/84U6WvvoqOzdtIRoOY7u2R5fFRVGEx8wULrquO7sPNPvW7dh3EcAXX3yhHNnBj869SwB8tGTVtxpauqSmGtKX72PKzBlkbROhKliuTV5emM/e+5TIro1c7bfo3FzL8AFRso6Fhtey4qFJXo8xgYDXQRiLkS0upK65m63dfVi6B1H2IVEsE+n0lzSuRKgavYkEE0ePQHNNDjY2kV9Sxv5tG7GBwvwoaB7D30VFkZIvHnuEyuVLSHd20llfjx4Ikon3ofsMNN3/laRSYmWy2I7EF/Kz/aOP6G1oQA35adm2HU1Xj9THuC6hYNA7/bEdovkR9h5ooKWjl7LyQrCzJJvbOLRqJQNL86gaUM7OA+34dA0hBYbhRwoFM5ejrculpTPG4LJ8hg+oQACarmK5LvFEivxoBLWf4C77mRGhcIiDLa3UVFbw+D0/4Xs//z2Ft99JQUkBmBaKIjBzWcYfNZHCokLSibQ40NLFuvXbTtE17e7jjz/eBVCklGLp0gW2lNK3fceuU3t6Y8K1TGXY6BEMHFyFY+ZQkOgadLV3sOWDd7m10KB53WYGlEdxbBtVehClkBJXCIQLjq4hywrAzuIfWUN9V5zarm6WF1TwRmkVKSOEJiXCdbH6OzGQLpqq0hVPUFlexonHTuGFZ54jHAqQSNv0JZMMrqki4A/i2hKBjVAErpmjbtUKOur2oxo6LpLxx88hEM0j3dtNJt5HKp4iFeujZOw4Zpx7Po5lYQQMWnbtoHHdek8WQtGRqChCRbiS8tJCDF2jL20zqKqKhvpDdPYmKCgt4uyjR9Pw2Ts0r1/OxfPOxPAHsXImmqr3Q5QSy3RwkPh1A0M1aO6Os/1gI5Z0EQhC/gDBgI/2nm7v+fuJ5wA+VaU4P48DTS0MGTSQW6+6kJf++ld8Ph9Of++XZVmUl5czZvxYTNtVkskku/fUH2VaVk2/BoqiLFp0BM0aW9/QNthxHGmbaWXi1AnohoF0HBzpEg5H+OzDxZyTpxFq2I8/6COoShx0pOLVlo5QQLFxzCzmgGqczi60AcX0ZFxa23oYHA7jCwU5UDiIQz4fLjbS9ZgOh9k8riJwHMH+lnYemP8ToiLNY4/+lb60SUtzO+OGDWRAeR6m43oH8NJBKgJ/MITm8zJtK5dl4MhxXPzgY5z409uZfNElzLj8Ks69+17O+81vELqKa2ZBgu4PYIRCR3Q4AKSiIqXL+NFDUVSNWF+CsePGkk7FWbNmI/WHehk1dBBtW1cwfWwVt1x7KQca29A0HSklpmWBa6NqLg2t7XTGelAVh0zWpSNlsbexHSlVjyzu85EfjtDc0YPtgpCOhytISTgYRDNUOnp6OevkOYyvjLLso4+J5EVwXbefGaIx+ajJuLYthBTOjvomf28qMxvgC1CUHSVfCIAPl68+pqUzjVBUR/NrjBw3mpxlAR5jINbTQ3zvDk4PmnR39lFUFMV1QLVMlJwFuRzCyqH0mWQHDkO3LZREFkcN01C7G7/fR1gIvp3oYGxvM52ugoVEl/0yDdJzpNK1MVSVxp4+XEVl5dvP8ugvr8e1TdZv3UNpQT4XnX0CTne71/ko+j2H+6W+huH3897TT7Hkb48QCgQZPuNYBk2aQCaR4L277mHtolfRgyGk4yIdC9e2vd5q10VXBGYuQ0F+kG+fchyZnEUinaG4pJDTz/wWbyx8nbt+dS/33Psg1192Nu+/+AjxZIrevpR3ACM9SFQTKpFQmA1bdnL9rXdRf6iVkO7S2tRKd8rhUHvHkWb9oN9HNBSkqb0TFM1bbP2aFKWFBaQtE8e1ueXay9iyfAWZeBJN0xBCYFomI0aPwufX0VQh9zY2s3Lt5jkAX9x1F9rOxx6TiiLYt+/g3I54GiEExSXFVA6qJpezcG0IF4RZ+877TDLjFPU2oYZ11Jpy8AeRiQROOofjSnBARIKo+X6UHfvwDyin4VALqbRJ0B8g5UrKXYdr4830OiqO8JNSBcKVOP3eSXpfQ0Dzcai9l7hP54YrL+CLdVtZ+N7HnHnmCXzv4nNobe7k6RffgWAU1e/HUGwUVUfBIxSHVUn98iXsW/YZmm546namiWoYBMKeaAqKeuQlO66DbbtkYkl0meX3v7+D6kFV1DV1kkplcW2bs844lQkjhzP/13ez5PWnmHvMVHY0NBPrS6JrRv8hRj+ZTddxLZdRwwdTsiaf3z/wV9565iHyggFW79iLOaQKQ1epLC5CSofCSIh0JkNHby+lBQX9yZbApxnk+4MkkimqKyo5bvxQVn/2Caeefx6xrl6yWYviAVWUlJXQ1tKq9PakqD/UMEtKqQkhHGXRokWO47j+xuauoxKpJFKi1Az2OgTtXA7hN9jf0MTGd97jjNIgSU1FrSjGbevAqq/DjnUj3SxCcRF+FSUZQ9+0Fc00iWcztDe2EtT9aI5EQ5LAxXAlAeESR2C6Xs3pYcein9XgrV5NN+hIZemIJfjeBWfw8SfL2bp5G1kM7v31Lbz02AJOmjGG4ogPXEj1JEh09pLo6iXRE8NEwVH95ByB6Sq4egDLgWQsSbI3TrK3l0R3D8m+OI5lURRQOHXmOF596j7OOP14GtuT7G9sx+w33JDKElatWcfcmVOYfcxUvti6m87eFIqqeydqR3qhXRzp0tUbY+SQav72wG/42Y+uobG9mxFDaxg9qIINO+uoa4uRzOU8RoZjU1FSSDqXJefYR5JOKSXBYBDX8fQ1Lz7nTLav3UpfPI2jehTZcF6EwcMG47iKkjFzNLb2DAMGAvIw0DGwobmj0rVzEqEpw0eOwO83iCcSBPwGm5et4uyow9DW/cQTJm4ggB01kIrqlRXCRbUtpGMicwpuuBCpCJI9JsFQFM1Q6E3m6MiaZBQFTdExVEmJ41CASvrwTvoKgCeFB4kGND/1jW2cdNwMTpo9g58ueIRXn3mIhvYUp55+InPnTOeND5czZuhA6huaaGrtoLW9i1hfH4l0jqxp41omQvYzCJEYhkEoFCY/GmXggBIqSouxBRw3bSLl5aVkHUl7b4Z9jU209iZwpcK4EYP56LOVfLJ4GTs+fY6O7h6kq2L4dBwJKk4/jUX2N0GAgk5PTxxV0xg9agSmZZLO5Bg5sJyuZJK6Q20Uh4OMrilDqBoqDsXRPFLpNHokghACV4IiHKJhL6MfVOpSU6BRu3UnE6dPJh3P4DNURo4eyfIl60GxnIaWHh0YB9RrAF2xxJjWrm4N13JQUQfWVKEIQTjgJ8+v0t3cQEddI7synURzJnlhBWNYFOEL4UoFkRbItMBWwSz0Qb4fmVJoTXZwyDRpSti0m5LRhqDEkGRsm6yqoQkJmkDTdRQhcaSNi44qQXFdbKEgNAfLVejuSvOPx+9lxPGX8ovfPcIvbr2eTTsPovt8dGclQ0cOY+zECQhkPzznHWB4yYiXQEkBrrRxpIIQGiieqJkUgrc/XkleUTE9iSy9fQn2NXfQ0h1H9xkMH1TFymWrueeXd/Pi039g9MjhrN13iJAvgClzCOkBLy5ef5oQYPiCFBfn07j1EKlUiuLiQhAu3RE/iXiSgN+P5vOzpb6R6op83FQLgWgJ4WC0n8/TSaqzAVc6ZNO9aHmjSMog+2s30B3LkN1bx1HHTEU6Fo5jMnBINaoKLqo82NTMgYbWicA7GsDevfWjOmNxUDRpaArlVeWYmSx/f/hvTJg6icK8Yj6MVLEsXEk4FaMwlaZwf5aBWo4CYVOYMylwLfIsBV+ZRsSv0tmpsbolQTcumqITlBDCoVg6vGDkExcKZ9txQEEBD+RQlH5ARaIaGgoKSiqGu7mWlSUlzJx1FOvfe5LpJ3+X9uYWrvvhNYQKS2mNxVi9rY7y4igBXcOv6aArCMVz94pUEEKC6NeylB6LIufa5EwHIVTqW3tYU1uPaZl093mtQkMGVqO6Ds899QJvvvI6f3/8bi447zQ+XLIC/8EGQpMmo4YiWNLFtSyEUFGF8BoihIsmXPbsa2DZqnXc8dMbKSkuJJax6Ykn2V3fzJABFdiuSVcsQ3L/RiyjFCdQSSzWTVtLE+2tLfRkLJpbu4nnVpE1BAFfMfs6UgwIHCAgBKIgjNQUissr8Ps1zFxOtHZ0U9/aPuoI0NHa0zO6J5EGVRGhSISCigpaO3pYvWojfYkk+QE/J1xwHnFHxXQSpFMW2xubeXPHTuxEHz5sDOGSrzr4ukzyLEm1myES8NNYUE0vDse21OGXAseVxHWdbk0hkXOwpOMdtgsPxQLQNQ23fi9pNURvPElnX45uGeeZX/+Va86aze41r3PR93/CdVfdxGlnnUWoMI+W9i7UQIhsLoNAosgvZQkVIVBVEIoED5JBOjY5yyKT9VpZU6bFpr31jB0+hLLScpLxNB+9+xFvvL+YAflBNi99BSMQ4IIf3cP4kYOplCb5BxooKSkmnEwhBg4iK8BQRL+nEJi2S852iGcyJBJ9VA4opbKsiIKon6deeZ+3uj/nxBlTEKkET7y8gq54H8lMFsuxURUfquHHsdJUDqxhxKgayvILiJREEX5By4H9NOyrY/FHixkzeQJDR48mFAlhZrIinsyyt65uhJRSaFJK5YEnXhicTucQrqSwsIC8SJSOVDv+SIjW1i6CgyowDA0nFkOTgnR3J10drYQiQUQkjCslLoJO6SBx2W9ZiAN1lAcE26uriVhZMu1NqCKLcMURBqEjPb6SsFykA4Y0CQqXVMxk7/KNdFUP4kAyx87dB9i7cyfdyQxfLF/OzVeey8t/f5jVK9fzp8ef5eNP2llRtoZJ44dSUTmAvPwCgqEQoXAIzTAwVBVVKLhInMMyl66LIiW25WDmTFRbUrtlF4f27GXbtp00HDhIZWkpd//oUq68+EzeXbKBW3/7MF19Gdau3cjw0cOYMl4yOm4SaD7AAF8+BRURr83VlshcjqwL2azlNQQpBhKJJl1UCaaU1PekiH+2nLNOnE53OoWtBYgWhvoPTADHQVUCHDVtEk1NzaiGTtAKEwqHsS2bLz7+mJqifPZurWX4xAkUlZbQ2dmD5cDe3fsq1374cEQD2L+vody2JdJyRFFhAQHDQFMEqitpPNDEzJlH4aoKvT0x2hqaaG9r6yeQKUis/rgn+tmCoLk2A6RNuxHGSvRSUJRPQlOQtqdmU2VnyXfB1/+iXcfBkml6PtnCoeJiOopK6Bo8gtrd++nqidHZ0UU6a+E6Jo4rueeRF9i8YxeP/fZnLH/nefY1NPDAU2/ytxfeRPb2QihCqDAfVdcIRyMEAwHvXqXEdS1kv/Sw6Tok0zlsB1LJNCRToDhcfN4pPPmn25k1YQwHE1muuvsRXnvmNdSiUvKjAbKOd1a8eft+DkXbGTdpOGayl56tB4h2dGJZSUzXi401VWXUHSw5UrciBa5UkLkshpUh7kIslqA0HOJATwpF1ZCui6JALp1ixsyZWLZNOplCim5c1yUYDKEYYfbvO8CZ37+YrlUbEYpBcXEp0t4uHF1Sf6Cx4Kk9jUVq6uDOsr19zq317bGga+UYd9REcfRxx6L7dNqbm9m+bAVVkRCbt+9iV/1BMmYO3edD0TWEooAiPHgSxWvxUgSKaTOyr4MDoTBtUsNO5Sjq7WI4FqqqU+Y4TFYFUijss0GMmUBVWTkNluBgTrB4yQpee/MD2ls7GD5iFHNPPY36g/sZWZLHw3fdzIJbr+Lbp51EcV6UNz5azO2/+Ss7d9ZRXhJh4qzpFJQUkcnkmHH0DJoaD5FKpkinUmSzWbK5LNlMmmw6TTqZoLp6ENmszYjhNRx19CQMw0dLczur12wgmpfP6OE1HDttIqfMPYp0qo9Nm3czacoEzr3kYnpjMVZv2synH6+hvasLywiSiIbo7ozTmzCZPWMShUVRjpk5jUjAh+b3UVQQobWji9279vOjqy7hky9WUlFSSNAfoKEnSSjsA8XAcW0GDx1C1aAatm3ZRqwnRndnN80HD9LReIiKvDCbN26hND9CVzzHhNmzqN24mX21O4XmC8h8QyiNB3a8qe1v7wp1Si0kECBUCotKUYQgGPCTX1DELceOYW5zLZ81NNMaiNCih4jpfrK+AKY/hOILoBoKriZQFYmuBLATHahmjkzaQrezpKIBb+e4Noquoxg+ujExXfBpPlozGRo7u/l81Vp2b6slkcyRV1REND+PN9/7iIa2Ln578/c46ahR/OONj3jwsedJmDlChsb6rXu59vLzuejbJ+KP5rG/K8We/Y3cc/d95NJJZhw1mdKSUtra28hks7h46FfQ78exTZLpJLt2bOfKS87mmGOmEuuJk8hkWL96FZfddCfnnDKbvLDBsTOn8/KfF/DS3E95fc1mPvnwY7asXM6U8WPo9Qfobm5mj5VDmzSKukMN2Oj4DB/xeBxF1dGDQSzTxnEkpmkRjAYZPqgSxbFJZnMcPX4sH6zeTDYVRCgQ0kCkkrRs38yIggAlQysozI9SWVrIwMpSJowZwYtvfMrvH32Wm359O3l+KCkqBEVHFQq9yZSa6uvL05q6ElqfFu5n3KuEAn4ieQqffbCaxLLF/HHWKLo/P8jsgcXkbIdON0W7FaclCW1JhV4E7WqIHlXHliopCVMVk5n5AbpkjrijUXOghXGuxFQEKeGQzmTQBWQkRI083l63hXVrNmJaLhWlZTiiB0NXmD5pNMlYimNHlHH6rAnMOu0amrtjnHf+mdhqgN3btqIYBjdceQ7DB1ZT3x6n1LSIlxVw0cXzWPTKq6SzOYaNHEl1dRXhcBQFyJom7V0xDu4/SDLRxenfOplBQ6pobmnDUFUCPp2f/PAaGuoP8MHKLcycPpnFDz7P48+9xkuP3sP5Z59Ae1cvAfUGFClJmw6m7ZDNpCmKBtl/sIl7H19IJpujpb2LkcOHk0imCIdDHgdaCBzHpSuRJBLwsXLlForCYf506/cozo8S8BuUFhdSGA1RXFhMMOQ/0thjOxaa6rXn3nTleazfuYtINA9dg4K8fOhP8hLxDOmkrWt1dQeH6QPCfghI3Jzw+3wk0hkWL3yd+0bX0L10OT7TIacDqqASlaqgwjQpkbZLxmeQKypA6ejBTidIqD62SY3GlEO7aXO+28mJOtShoFg6ri2ICAVbtUhZKrucFFKLUllZguO6FBVESWazGJpCWVGUo0ZUcsvV53P+1T8np2lsWfEm7R09PPLCIk4/61T2H2jk2Vc/ZsGtV+M6NoPKyokGIowaMpCzzjiedWs3UFu7lz31DSRTcXAluuGnIL+AE084mmlHTWDAgHKEAJ/Ph2tZ6IogHovhC0YQvhB55RVMK67g5RcWMeGESzlh1gR6+5KkMlkyGdOTMMbrl0pmcjz9wB3cf+e11Hem8IfDuAIc08V1vJaD/GiEooJCXlj4LsfPOZb2rjipjMn++oOoo0Zw9qlzv3aGnTNN4qkUQb+Bqui0d3dQkBcl6PNz57WXctM9f2PU1LGoAQMcBwXcXC6nptPZMVo8FceftVACEbBtCsuLWLlsK9OcHCMaDtLR106kIA8hXCwHTFdFuN5KyoSjCC2I3tyGo0KtkcdTaZ2eRB+T4imuKAgwISLY7Pj52NWZ5/RhqAodDuzKwkYRIBzwM1xL0mUGCPl8TJk0hvbOLhRFxXZg+vRxtDS3s2T1Zu6+5w6eeOlNOroThKJF1NbuJhgKsqV2N0IRaJqLtHMMqixmw6YtfPL+p8QzMGPGeO66/Qf0JS1smcOvGwQNg4DvS9VaVddxXJegT2fL1l1cdP0vKCoq5KhhA4kdbCQSDfDDa87FMHQQCtFoPuFIiIJomEjIhy0dSkqLeOL5t7nj7od5+a+/49HH/syUaUdRUlxGJOxHCBtdUxBWlrmzZyEEVFYW094To7Ozm/179nL/w0+yfd8+fnXDVeSFQ0jXwadp5EUidPf0EAoHKS8uIpVOgc/P6JHDmD1+GJ9/tIL8gmKwLYQCpmN62LuEEVq08hKMMEIRoqKyjLXvfMiFRRqi/RCaLWjKOrQ4kpCQRFwH/Cq56gpUV8Np76TbsvlHQuPPGY38TI4Z2RyjBxRSGRGkY0kOSUG7VBiruLQ6gs81g2wkjxPCBqepkr2Wyx4JVaUlTBg7jP0HDpHKuqh2kpuvvZh/vP4xze29jJowgaVrt3L8SScyevQw+uIptu7YjSpcLjnnW2Qtm0g4yIL7/swv73qQoQNrWL58HTt27uJn119MaUmUwkiQiE/H51NBUVA1DVVVcGzbK50QXHb9Lzh2yjieemg+R8+YwDEzJnDU5LHU1FRTWVVJaWU5kbwwuqFjOw4Z0yGdc2jtTLJy1QZqqsuYNmUUKCrdbd18sngpZRVl1AyqYmh1KYl4iudeXsSIUTWomkFnbx+ZXIpjZ0zljFNPpbu9m4xrI3wa0XDA0/xQVCKhEIl0klhfkn2NrcRSadLpNEWFEV55/V3KygeweuVqVE2XmGnF7Gn5XAMfnoqiwFYNWtev59TSAItSkjGzLyIchsb9zeRZNpNCNiU9zRS0tVDS3E2vK2hUfOzuSZPLZPmuYfGt6jDaqEksq2tjy+5DTAxBJJdBsVSeD/oJR8PMCPoYY/Wh5NLEszmkCKG6EkU4+AX09aZoj3Xw8z/8iLbOXv705ItUlg2grbOTcFCnvbERmUvT09tDJBykL5GmL5WhoDDCnx99gVdffZuP3nqaWCrDu0sW8/Qf7mXXgVZ+evtv+NnN1zNm3HBS6TSG0BGqi+MKfD6DSDTCO+8tJtaX5vLvXcyDL77FhnW1fOv045FODk14/VBSKB5nCE/jWkpJPJkimUyzeesmfvvcg6xeu5VBQ0fw/csvYNuWrTz5j49ZumwNZ540k96ePoaOHodrhJHoXvOB66ArGloQjj9xFlnTob0zQSyWYuygKmKxbho7ukkmsjR195IX9LNrVz09liASDJBvBHj2scfRgxGvxbY/ZmuGGsQwNDISSv0+fjllCImGfWwvGsyb69bj2A5KwE8oEKJ76GgyWpTqUDWTatejxZK0x23CuJw1IMTIwWWow0ewaU0tfdsbMKIG78VT7K2ooTA/zJRMnHGY5Md7sbMaPU4av+pDqD4SWZtsxmLJ4mUEi0K8+6f7GVxTybCjL+QPv/oJW3bvYfFnq5k5+2g27toJtRAO+Dzl16wga0vURJK/v/Aad9/1E5SQnx9cdyeBgRN4+vPNbHhnPslUjh/f+yjjZs8ml7ORQsFRveb9sLT43XXn8+Kr73L8nJm4rqC7q5sBlcUE/Qa2Lb1Oj35+kxAuQmjYlqSzsxfD52PV6vVMnzCCkSMGc8ef/s6PfnAVn62tZc6kMbz6l+k8/MyrrN6wlWTaJJFK0NbVxeBhgzi4dRtXXHAWo6uLcFxJbypHc0cvrtTRDY3PVm+idnc9puKjpzeGRHCsSLG7K0YDAVwzwfCBFcyaOoY77n/GEzHXVDRNk1okoirRSIhYLMPoudNJKyZtdQ0EpwwmPxwknsjiU8BJ9PLJW+8wYOAgSkfX0JnKonXFqcgPMmnSEIprKogPGMiqN5eg1x1iytAyliQdmo8+keuvuJC6zetIvPQSmb4klgBNekw+N2CQbDNxFR+HWjtoNXTuuecnnHHMVIbOOIvvnHksP73uEjbuPsQF193O6hWrGTG8BkP30dfTw8H6BkaNGQ4COjp60Ywgo4YP41BLN7ZjEaqsYsOOOsaMHsHMo2fy8LtfIE48F8tysV2LkGWhBv0c3L6N2x94goMdXVxz3eUcampm0sSxaLpCJpPzqJ7SPTLFRVVVzJxFLOZRdVKpFFs3b+S9Zx7gyZfeYf2m3VQXBMkrLWHJpp1MHzOU00+Zw9AhdWzaVkdlZRk7arez+N19PHDHJeR66jnQEaG6KERZJEhhKMDuQ22kHBtLUYjnLHZs34pQJLFEkuKJE+ixJVLamLakob6RO3/0XRa+s5i1tYcojAZw/JqmDKzIawiGgjlFCEEsIev0cjqFgSJUXEXHslysnMSSOkWl1ZQPriIvmSbQ2U1NsZ+Jk4aQHlDGlmghn3+0gnBdI9mqgfy1x6UnI5gcCbPps89ofWkh4d6E18QXMLAL83g7bvHLHgXnqOP53vcu47zvXsiC++7i6hNnceENP8fMZXjyD3ewr6WbksIQf7n/DgYURajfvpMVS5azdMV64n1JSktL6E1kCIdCRIJ+2jv7KMwv4s7bf4h2YBuFPU389LYfcKjxIEKROIqCIiUGOX4xLcKPJkQpKinhg/eWMmnkCPIjQbp7+3Bsh0wqh+eM+xsKXYF0FeKJLJ3dcXKmjc+vs7W2lumTRnPUtIks27Kfo6ZN42e/e4wXX3mLiuI8mnpi7K47SGlRCX3JDPv2N+APBZhzzATuefwDFq1pxXKgpTMBCHRFYdjAMsK6iqLpuI5FMBSioKiYcChA3Mgj6/eDk8YxbYSm09XSxuRRg5GOLSIBH5FIaJ9WXJGfSOmKVFSJaDlIc9cBwgkTkcwhpQ1OzutVxkXV8wgZfiIHm6gI+wmMH8XGKccSnDiWpo/fRmzciVlWxIL2LPsi5QxPdVH14fv4TJOBmSyZvCixolL2x+N8lMghjz2RsdOmUVYWZFjNMMrKipgzKMpr733O2+8sZsuyt/CFwqimgoNKRovwsztup3btOu76/SMUl5bQYXdw1JSJmEIlGA7T0dNFc0c7RQMqqKqu4OEHF6CpGvG+Ppav2UTRuJm4UuDYJqoS4LW9SVwjh9OXQBcuJxw/i/pDzdhCIKR7ZOYSuP0NcQrpdI54PImmavj9ARShs37zLq698AyKIxG+/6NrqW/uoq2lg+UrV/P5yge49MKzGVAYZujAEOef92321R1k9bKV7N7XyZufrCISMojkFTBn1hQ27t5DXsBg2KBKxlVV0BtL4UiBdMGxXRTVRzzejZnKoojDQsgOBw+1MWroQMAWkXAIJxzo0wqFawfz803b6vJPVmGM4rAWA9eQVOQVk0uZ3vo1UyjCRXVdIr1NyHCU10vHoucVIZaup2rFZoxwiL8kDJqjBkPybeIihNPdS1RRyJ58Is7gwbz89sekBo5mzCnfoqx8ANFIgCHDBlAQjlLsc1FSSX72+8eYcdxs/vHmh6xctwVUDUPTkT4/kyZP5K03P+SMM06hr6sDFejt7uLe+/5Mb1sbRQVhBg2qoaW9m1AwRDLRR188zisvvUOHG2XOGeeRMU0UoWApLpviOoV5QdrWruCoiWMYWDOALbV7UX0+pNuv++w4KAIcxyWdM1m+ZhO7duxAlZLSsgrQdYLBCK+99RGXnH08M8eMJqf5wadxXMEZtB9q5sX3luDLZTh64nAmTRpPcXEpFZUlLF7xEQOrC5Fmmtff/oDRQytJrHmNwuO+w7adBxgzpJoRlWW4ltc/rUkXQ1fBsVGFi4pAcVyCgSBra3ezbdde/LpCMGC4WU011e37m9Nrt2y7au2OusI5AUte4rNEo6qy2fTR0NmLKnSQkMmkKSwuRLcsIrt30mSZ1PWmyWzegr1rJ1bKpiMQYnXGombcOKZPncCO2v1odpbBZ55AezTCs4tXEpo8jePPu4Di4giDqksZPmoohj9ALp1kTk0hDz76LG8uWkxRURG6ajN6xFAqSwvRBPT1dLBs6WoaW3ooKMgjk8mxu3YX9fUHCPh8bKvdzcO/+wVbdh8kmbF45/1PePbZRXz42Sq6A4Ucd/0PcUtLsc0siu0ic6bXidLRQe3Lf+cHl5+HUFX6EllUcXikgNeuk8s5ZDMmrR2dLF+5jjtuuZarLzmTvfv3YWXThHRJLJnhQH0dl807g7aMQ1lFKT7VBl1l8MiRpEyXNWs2kurtI+LXOdTSxvrNuxg7pIopE0ezdOUGZsyaSbmWprxoAAUjJ7Bv5y5UTaEva7N9114Cfh3HlhTk52PlcjguKIpKvLeTktJSRo0ZKZev3SxGVZeYA0O5ezXAHTy0piuo+ofut3W2xfuolGnKhM2WzhhZoeL3hVBSDs3du5hQEkYoMFbzMSvTR8SnEQxpSDNDJuvwtm4w+7gZlOUXoMiPCaoqH3y2kp1aARPPPp/ho4dQWOhn5LAhhMIBspZDMpdl/IA8Nm+q5e77/8qtP72KO265isJg6OsKcUA8neGhx57j7geeQw/5OebosUSDfppa2qgeWMmStTsZN2E8O7Zt5aMv1lIz91RKywZROnQYMVti7Tvg0V5MiWs66H6Nus/fZGBhlIE1g9hZV3eEa+RKgUQhm82RTCbRdJW9+/aT6I1T19bDiHGjyQ/n8Ztf3swbb73P8y9/wNZd+2msP0BZfiUtCZMhA6spLS1hX30DY6ZOYMDAajYuWUb96x9SU12BqitUVJZw7lkn8cXKzXz6wSeMHz2cvveXEY5uwjah/uAh2nuT6IpGrDeJbbn4fX2YORufrhING4yYMJ2Tjz+OPXX7ySSTjBheE6/Oar2aEMJdumbzgWBxZMZ+My13BfOYlezi+x31zBlQSb0Isl/Rac/q9NkhBrom/q4eziopJKDGydkKKVRaE70c1PJwo3kYQYMdBxtwI3msSylUTpnCCbNmUVAUYdDAKmoGVOBik7FsFAWCqmRExMcljz7HpGlHceutNxFP95HM9OFTFQK6gmXaZBWN+x79O8+88BZVVSU8+adfcMKs6eiqwnuLV3DFzb/l3Xc/5JOl6+iK9VEyYCBl+SFibfU0HNjlxVRVRzU0TyJBuLiqTuOWjfzgirPpjifJmS4+/TDCpZDJpkmnM/j9ftZv3EZjYxPfPuM4ln3+KW8sfBXLsiitGcymTfs4fs40amv3sHd3HVNOHUxrwsQybXRdYeyokTR1dLJPCGZ++ww6DjWwf9tWspYgZ7nEYr0UFhczZtx4jj9uMg1NbdiZJK6iEfTpGKFOOrvilJeVUlGWT1FBlAGVFYwYNpiH//YMsUSSwuIi9n2+Skp0MWHs6JZLz/95jwYwcFDpnpqKCtav3yrdojy2Zf1EU70M6OhhgqIxI+BHCWoIf4hQJku4pIBe12Zpn0o66qPGtXGERi8KCeEQ7+7BzJrgCzDnisspLM6jqCDKoMEDvXkMttXf0+zpZYR9GumsydYde7n+hqvAdYgnTIRymGjuUhgN8OmS1Sxeuom8ojLu+vElHHfsTA60tDOgrJg9+w9w7OzpPPfgr3j46UX84dHn8RuCn31rGlVl+aSyXo+3KxyP5IZKRWGIZ9/6mIa1AaZPncie/QdRFRXHkbgScpk0luXRVRVVobWjhxGjx/Dd719B1B/kD3ffw6iRg0l2NrFu1SpufuoB6g4209sXx696A0Zc1Zv6gsxRWVJASUEeBxtaCPlURo4ewUdvf8jeuoN09oylN95LXVMzp0TnUDFIoAsD6ebIz4vStSxJc3sbP77p+wyoKKCts4fOjm7e/WwlPRmXcF6AW355H0tWbpbV1QOZMH7cfiGE11VZU165q6I4DyEdtveZ+GYew+BJo9l9qJV4Ux2BrjYifTGK+hKUiRwjXIET0MlUl1KgSPoO1CEUjXpsXKmyZechujt6qaiuZPDAMiorK6ioLMN0TG8QVj9TUUjpZYfSJZMzsS0X03HpjicJBPrH6LgS2zYRhsGGTbVEggE0YTH72BkeG8BxyLpQ39jO2tWbePaDNVQOrGbqhJH0JnKs2LyNn914Bb29cRTF61I83IDtCxi89d5STj5hFqaZxbQyqLof25Fk0lksx0GoAkdKFMckFA6yds1GEn1xRg4fQtyC7155NfmRAJ8v30xOCrp6E+Tn5ZG0ZL8KkUTp15ySrouhCsaOHEJlaTFNzU0MHT2UJbt2sWFbPZmsQ2tLB72dfYR8Gn5DQdcCWJkMpeUljJ0yjj898RxNze30xuPYio4SDFFSWk77rlq2r1kLAnnUxBEMGlC256v84J0VhSFXuqiq5qD6YPSIUcQGDkKqM7GyDul0knQqw7aubpbs24evL4Ew02R6ulEClVho7Ij1YiWT7KjdjhoMc831VzJm/BhUTXjaWv1nsUcmg/VzedM5m1BZkLKyQmpr9zN58kS6e3spLyhE01WktMnZgqTtsv9QEyOGDaa5M01pcZS8SIjGxhaWrtnMOafMZtGLz7J1615qhgyhJ5FE6BrpVBrHtvsbE8BybPLyIrz74VIONDTxg2uvpO5QG0LRsUyLbNYTCRWKiiNdHAmWJTlUf4DzLvg2pflRXnlhIWg+7v7Tw7S1dFBSWUFrR4zu5kYmTR7Hob4sqJ6YmSvUIzMQJZDN5YhEQ4yOjCQvP5+62p28v3gplgmLl6zkUEsrxcWFngSx8HIPB5WCghJC+SFKx01gWEER/nAYQ9GIhoIsX/wp+4IhzHSS6qpiouHwtq8a+MDgqopOYRhliuvKtgMNIhj2YyJxU0kMmSPoM3ADQSgvhXGjyNou0pQI18GRNo50GdDYSLyzg1DQzylnn8qwUSPJZrO4lqeE56F8XxkLKyUqgqwjyEnBzVdfzBU/+R0nnXI8wbwotYc6cB0bISVBXxfVNUPpjb1DPGnS1Jukoa2TCcNr+OTzNaQswa0//zG5VIIzLrqBnGVzzHEzmTppPL2xZL9us0DBU8OLJZI8/dyrHH30VHKOTTyeQdPUIxPShOZhzAKPs6wKwfChNSz+8BNuuOEGxk+ewLyzvkXtjp18/vFKfvf7O/nDXb/n8vNPo6iyjA11Pfh8fq90dr+q3StAUbAcF+m4VAwo5yd3/YwP336f+n0HieYVUTqwAk31hnYpmo6uGfj8PnSf4bl928Zx3SM6Wj6/j8YDB71GeWGow6oGuIBn4Pnz5yuaqiRf/3DploK84KnxeNrp7urWiqJhxoweQSaTxjJzpFMZEokMfckcmXQSVTi4hts/e1tFN1SGDp5OVXkpZeVlWJZNNpnx2H+4OK7HPFS+HPzH4eGwiq6xrSXGOed+i89WreO679/EVdddzdHTJxLJL/EkllyXIdXl3Pqja3nssafZs3cfoyeMY/OBdl57/wsyySzX3PxL8qJ5mLbD7Xfegi8YoKM3TVdfgzfxW1UQrovf72N/3SF2HWzjFxedQ3NLB7brYOcsUBSk0q+e8ZUFaVsWkyaPx5Euv/rVbyitKKO0soqN6zdQVVPDb+95gDFDSvnNL29hXUsMTdE9So7XM9rf1C+OCNZICY5QMDMWqs/PRVdeSrwvQWtLO509vVhZs9/LgehXxlc1QTgYJhoOEgn58Qf8GMEg2USavo52hGu5/oCmDKmqaADqAbS5d92lLFiwwJ04ZujSmsriUzf3NZGMZ2jYu4dRo6uBAMFwhIKiL4dESseTKrAc25v2JSVBQ8cwvJbUnG2jCR0j5HUSOuLrQxuFOCz/5NHFfEKSsiUbe7L87U/zmTVlAn966hU++HAxZaUl5EejSAUy2TTxvjQZC37/2z9wwYXnk1eYT1d7K3997D5Wr1zDHx9+nF//6g6K8yPEEn3ohu8IJROp4GCh+ww+/WQZk6aMIT8U5VBDK6j98lDSRdrya4o8ol9VIOBXGTFyCNt37qWnu4fnn3oeIxSkqjTC9eedyi9+cT174jY5QuRF9COKd477Ffmmr0hPHR52KSRIy6YwL0pJQT6m45DN2ViOREV6MxM13Wv97Rc5EACOJBQSrNm2g67OGAjhDizNU0YNq14jhMgtXLhQFVJKtV++4djLbl6w/KU3Pnd94aBSXllG1aCB5EwLtz8pEf1jZ1S82OBKsMzDgxcVhBQey69fh09+Za/+q7K7J+7lyal5YILt2EQ1wcCyQuKJFJt31tFwqA0c23sLjgW2Q82gKmbOmMyq9Rto7YgRzc/nlBOPo+ngQTZu28nYceOxHBPZ7zmE2/9GpECqCsJxaNhfz403XuVpbiQTnuz+YYmII6N8ZL9upMS1LWzLYtnKTRw81ITfr6I4knDAz9DB1QwaVElnLE6PBaqmHhGKk4fHavZPeEMIFFWCqyBRUFQP4xZIkDaKquAg+4dOex7nS62QL9XkDy88v6HT3tLKoYZGcqmMffpJM7X3n/79D4QQj0spNe0rEWLL2OGDOoRCqaIIt7GhSWnYW+/p8R2mMx5ePqYJQsXn9zGwuopdjc04mZwneKod3jHOv5ke8E/GFv8iMA+2BE3F8PsIBvyei3QsiiJh7vzxNYwfP4Z4OsWEKWNYt2EXb7//Cf/4x5ugKuh+P+vWb/ryfiVf0fbot59lUlpaAkKnq7cL27ZxLIdsLkc6lyWZypBKpkgmvbacXDbnyRZnLUzbQTEMTMdFQaErlaN1005WrN4CitY/zs49ogD45WS1/v9wMpB0IBwAXf/SuwgJjgLJJBgK+EMgnS//nq8/B19djLpOIOiXOI46aVSNAyw/zIPThBBy3sKFqhAi+eGydZ9WVJRe0t4VcwM+Q5F+35eDM/qZf450qBhSQ8PBRo6dPZ1rr/kuf338Gdav3U5xeQGtre3oegApna8KU30p73CEb/71KdpC8RgIUnr1o7dqvVWqqCpmoo8f3nI9xZXlfLZuG9MmjGTssCEseu1DhGoQyYvgmBaOdAjmRb723Yr0EhYvaZKooRDJTJpnnnsZRVVJZ7NYloPt2NiO4/GZDg/JEZ5mhqooqKqGoXmkc6/UE54WVsCHEvIWolQE0pV4j+/dv9sfhW3T4txvn8m4EYN44rnXaO2KYfh9IF1s6eKTkiuuPQ/Xgadfff+I8M1XHPs3TkXHlTimI0MFecqkUUNrgZ2H3aQGcENJiVgEzJk17eNJI2su/aB5lRA+TxfyiEtQIJvNMnrUYH584zU0HGwgHAlxqLGBb58+l5NOOI5QOMSjDz/FwaYWfD4/dn8zvJBOP71SIPkG9TmhkE2nIGchdB2f3wcoOFJiJeLggBExKCot5bZfP0jjgQZu/MF3+fYpc1E0gRNPkjBtNL+BIiDdm/zS6yheDaoafny6iu3YpJMWuC7pvrav/x6eUq3uN7DS6f5d6Hkj57ACgN+H4jq4pgmqjj/kw5WgSoVsNo3MmYCCL+j3RgIJbwiXrkgs2+bzpSs4btZEfnPHjfzw9nvJ2Ta6omCl0txx2zVMnTCWny54kGw2RzAUwLa9IdPfPMZE9o+vk1hZ050wcpgyfdqkz4QQcsmSJZoQwta8eUhzHYCAyodjxwzs/mDp+iKJ6glHiS9XkCMFjU1txOI9RPJDrF27hZbWHgbXVDJj+gRiPd00Nx9CoiGFVxZZpoXrguE3PFfluv/icRzTZsygCoZXV3CwpZWd9S1INURAdTnxuMkEgj52Hmjijw8/g1+FwuJiVq7ZxJw5s+ntjXPssRMpKshj5eZdZLM5Tjp+MqrQsR3vmDOTzbJ7/wE6uvoIFRYwc8JwAn4/uM6Xsdax0TWdzt4UB1rbmXH0aHDBtjwWhAR0Q2PfwWZUw8eYoQNIJDJ8sXE3iqZiZjPUVJQxfugALNdl2fptWC5omo9sOsXs42eia/DRG++wpXY3F5xzKlVVFeyua0PxgT8UZOSY0WzasY9d2/cy7ZjpDBxYwRtvfkwgFPLyoH83OkdRcG1HmTxuuDOovOQVgM7Ozi+V7oQQcs78+ZoQouutTz7/8LnSpZd298QdXUOT0ptGZjtQXJDP9IkjwHZZunwdny1ZjWlLtm3fjWtZTJk0hmNmz2BjbR2xeAaJw6DKCgKGxr4DjbgoGJpypO3FK+NUMvFevn/R5dxyzaW88PqHXHHj3aBneej+27nqonPYvGM3Z1x6G8FQgF/dehW/efApzjnnTF78x1s0H2xk8Yt/pKaqiuMv/AH76lt4/9kH4J/khBtb27jrwWd4+c1PePeZ+4j800HG4c+SVZv5+W8f4L2n//SNf//np1/mr8+9xat/uRtd0/jhnffxl6ffoqgsymuPL2DK2JE8+vybfPzFRnzhMC4e5Nra1MitP7mOU4+fRVlFKalkmoFlBezath9TGFSXFyCdHGNGD+OPD/2S0sJinn/5ra8lfv/uY0nXCUR96tETR24O+H0bAOXwHIcjSndf3HWXFAsWcPbJx7/0txfevuzDJZsVfzSA7YKiaGTTKaYeM4Xrr7ucXXv3c+hQCznLJByJkoznaGppZ8aMCVzz/e/S9cenCXTFsHJJTjvlGEaOGMo99z5MYXERrW1d5CwbVcgvs2tFYPXL/QpApmPcd9/Pueqic9i2Zz9nf+9WWg8e4vXX/sr22h3U7zvAyy+/ze59+wkHQuQylicVbHuy/KlsDp+h8/wr79De3cfkiWP41pyjefoPv6B+/15+95dnGVgxgJLCEOecdjxZ0+b5197HsR021O7C6Z+8lkoneemtj7EdiRQqPlVl5Za97Nm6nxvv+D1P/OFO7v7ZD/jgk2Vccek5TBk7ktUbtvCz3z6KFgzj9nsHw2+wp66RF196jWnTJ9HRG6OkuIgzzzyFtrZuDja2cM2Vl9OTyJHo6yBr5li/cRcrVm/GFwx+TT3gn4dvCUWQS6XktCnjOfW4o1/N5kyWLFmiHJZR0r6S4x6elvzZjEkjd3+yqnaUg+KCq0jpYhhBtu2o45nnFjFz1lQGVJaza/cBkokkus9HZVUFlu3y9DMLaWlu4brvX0x5ebF3giPg9ttuwB/w8dcnnmPX3kOofn9/MtU/TFL1ivm+WC9XX3MeP/vB5RxqaeOiH9xJY5eJiERJJ+L86OqLOXionWf+8RGhsmIUxzwCBBzWfhbCg1B+88izNOxqhqDg8zf/xpzpU5k5Ywa/u/dvoKpMmz6G8844mXQqxe33PEa8JwHSZeasKSiKIJlIctMd9+MkXVBtcFVEpIDAwAE8+Y/3mHvMVC45+zTeefHPDKwqI51K8YM7HyBtSYIhj+fsJWIOqs9gycqtLF2+CU2TfOv0kxk/aTzXfO9CPvnwM+oaW5EtPby58DXiySxSSAy//2uC3vKfdrE3bVWV0pHqjHFDE5XlpS94Imhzv0FtVgg53wvM1rqtux5f9Mmah3bua5R+vx/XdVGFSlt7Nx99+jkTJ4/lmGMm4TN02jq6Ka8sZdK4kcR64ixdvpFcKklPdycDBhTz2efr6OntZfYxM3Bsh+62riP19JHMUMojicSpc4/mkvNPx7Ytbrz9bnbVHiSvqoK+jiS27ZIfjTJ25GCEclg27MtSSyieoQ/rLJeXldLRlaSmspCyojwURZBIpFDy/Cj+MKFIpD9MSAoL8sgIH46VRfRTQ4qLClj30Qs4Zg5NVensS/G9n9xNdyyHESrg1vkPM2XMCMaOHIaULtf/9F627jhEqCDiyTwdzuJdgRAOgYDfqzpxee/9zzxAZco4jjnhOD7+4GNWba7H0QT+kN+roaXzbz3zYWNbOcspKSvXZh814XUhRMu8eQvVBQuE88160XPnOgtATJsw6oWZE0betWPPoTxFePJmrmIjkAysrqKoMJ+eWIxZM6fg2GDZJpmMSSQvTOWAAs44dR7jxo5k6+Za3nh3MU7GQrpwzlmnctttP+SpZ19mb0Mrft2bFwQuWj/5e9jQIUcMf+G5p/HRF9uwHAuQ2LaFlJJ01kToElWR4DpHdKGlkF4yLBU0ReXtv99PJpkhEo1QVBClrqmN9xZ/geoPYZkWjmsdcXeO6+DaJq7j0l/jeDMGqwegCAdNNSjsi6EqBq5Mo2gGiUQv6VSmv1KRCE31To0UUG2vT8orlVRvPoP0xuylk2kqSgrp7Whn+1bB9OkTOec7Z9IZW0Ttrv0QCqIIHRSJ+MqIZyHcr+1iVVHIpZLqjJkTmfftk/4CiHnzYNGifyPpL4SQ8+fPV4UQPSfNnv5cRVm+yJm2czjI6z6Dg02tPP7E89z/x7+zZNkaKiuLWPzZCu5/6En+8tcXaWqN0dbRQyaTQggI9A/IMHQNobg0tTWTylr9p0reC0CoR5KuWF+SPzz2DLFEgsvOOZMfXzePdE8MISCRzAIwuLoCN5Mh0d2DrgnyCvKQ0D9PQkPBBQnJVJZw1E9RQZTG5lZOu+h6DnYkMQwvg/6mHUF/HQ7Q3tnLiJnfpnj8GRRPPJ2Rsy+kpaMbza+T627n7tuuZ8qkcbR2dSJdm/t/fTMTxlWRTmY8tOqwLOKRhFIjncoyqLKQX9x8FRd99zwamxrYtnk78ZzLxZd9h6OPmkxBKIh0bRxb+Zri+5cIlodLOK7rhMJhcdyMsZ8Dm+bPny/+eUjWN03qcAFx4bdPun/q2KFJO5dTVEWVHr1Ugqqze18zaSvFxq3beeLZV9m6bTeDB1ZRUVaIa5q89do7NBxsZOyEMVxywZlcetFpnHD8MZimwwsvvEFTc4ennXxYkV0KLMsDGT5fsZbbb/0t9zz8rDd97ZarOGHmJGQ8zZpt2xFCcPYps7np+suYMrqa3//8BsqLCjnY1MK+fQcwfAa2ZZKzTc6+8mamnHwZjZ29FBcWMGH0CMik0YTrjY2VAttxsJ2vG9uVeA3/imDCuJFMmTCaqeOGMWviWMaOGESmrZszTp3GjVdfiGlanH/Nz3jl/SVEggEe+OWN6FYGy5ZfM46iKKTTKSaMrOa3v7yFksoSerp7aGvvYdFbH7Cvro6gP8o1113Mn353O7f+8HLCfgXH+aZxSB7EmctkmTBqMNd/74LfCCHk2LFjxb/+5j99FixY4M6bt1ARQjSfMnvG08XlxYpp2o53auQdEBg+DZ/hJxbPsnrZOq687Dyu+t75nPudb/HjGy6nqCiKavg5cLCJESOqOG7WdLo6e0mmspSWl/QPofhqNugSCPrRVJWMmUUUlvLgk6/wytsfEgmFeeWJ3zJm8mjeeXcJr7z7CZFIhEfuvo2Nn7zK1Redg+W6LPjj30j1ZdF8OuFIHj7dIBTJo3F/G3ff/yiBgJ8X/vJbJowdSiprgvAEujVVJT8/ilDVI55EVSWqplJWWsKnr/yV1W8/wbI3nuDz1x7jntuvYXB1lJf+eh+GofPHx59l1YpafvPA07R39XDiMUdz/69vxEmnj9TYihDkcjkGV5cxY9pktuzcQyyR4/mXFtHaFkPzh3jjnY/IZpLYuRwfLV5KMBRk7uzpmJkUQsivIYMScKXrBEIBde60MV8U5+ctnT9/vvJNI+6+cbjhjh0LxYIFC8QHby3c+tmyVVftO9AS8BnaV9IZ75jPdV0mThzN7GOm8bcnX+STxcuYM/to0pkc736wjKXL1xAJB9E0Hw898iQbNtfS1d2Foun9uZU8Mjc4GPCTSiT5fOUGttc1IowAqzZsQSoKDYcaiafSbN/bzHtfrKSpuQnLMmlsaeaLleu57Xd/4+3F61DDEVThYqiwsXYXny5fSwKFXfvrcaWkrbWV7r4U2+sbUXSBTzfw+3WWr97EZ2u2YNke8KGrKpqqsWHrTtZv3c26LTtZv3UnW3ft4ZOlawhF8xFS8v6S1dz3t1cQoQgdnV3sPdBCPJ7AkrBlVz3JdAZVVT1lWDPHyOE1lJUWkkynWL5iLZs37yUYjiDwUK7jZh9NZ3cvj/3pKXbUHUJVVdo6e71B1V/56ALSGVtOGlUtH/7tDy+773d/aLrxxhuVRYsWyf/0/OD58+drCxYssB/527O33fP0W/d39KRsQ+ufmIhEKAq5nMXM6VM4ee7RPPzY8/T2xfnlT69h38FGXnntQwx/AL+hoyFIZbK4woPdFFVBlZ5EsQe1Kti5FI7pgKESNDxep+1KzFQa7Bz4A0QjYVxHkuxLgrAQmoa0FVA0Qnl5uNJGOhbZeAzQMMJhlH7N50w8BdJCCYYI6Aquq+EiySX6wDHRwxE03YNILSuHnUx6r0dRPJzWMb3oZQRRNBU3nQYEgUgAV1FQdYNMIoPM5kB1CeUVevg3oKtg9etpHjt9Epl0ivUbtuMPBT1CuFTJmVnmzJrEmWeeyttvf8iKlRsQmo56WNvjyz4YXImj64p6zbePfemhe++87Px589RFixY5/1MDoqWU4q677hJ33XWXdtoVP97y0eo9o3y6KoV0lcPHm1JKHNPi5huuJBKJYFkWhXlRfvfAY/TGM6jSxUqmwGfgD0e9EXNCkEplwLbQwwaq8IyZNbPe/zMCaP2Avuu45HIZNH8AVdHIpeKg+4gGPTWbZDoD0tNethOJfoOooPaXSrrmaUfalpcZi35Nar8fITTcTBIjFMVv+MjYlod7qyq+UAhNVXCBTCoJEnzhMIZ0sYROLhlDWjb+UABFUcm5Ck68FyUYJhgK4jgumb4+7z6QYEuUcBRVWljdPR4y5Q94sK0GgUge0nExrSxDaqopLiykducebKlAP47Qr9mOokiZzrlyyojy5PLnHxwbKi5umT9/Pv9ujvC/HU7Zf8qkCCHMp19770f7Gzo/3d/c7fgNVSiuIw6LboYCAZ578VXGjBlBcVERK5atRLpen/SUicO5/bqLefHtT3jjo3UEikpI9bRxy/fOZdZRE7j1d0/Q1B2HdJLLv3MyZ518LI88vZAVm/cgdcGIqiLuvu3nvPD6R2zbuZ/7//gz3vr4C15641M0v4+fXXs+QwdW8sIbH/Gz67+LbdnksjkMn0EkHOTZ196nML+AE2dN9tpdpMSnKix45Dk0BW647Bzu/+tLbNjeQEF+iPv/8FP2Hmjij08tQgYCBHX4829/jColt973JFlHYqb7uP7i0zh6ylju+MPfaGlPMaS6iPvuv5U3P17Gy28txl+Qx83XzuPc048nYKh8uGQtDz/xMiPHjOAnV52H5jH/0BWN9bXb+dUDL2AEguhqkP0Nbeyvb8H4/1R35mFSFWfb/1XVOae7Z2PfZJAdZBBRMaCADrIvohIYUIS4oCIxMZjoZxJjCHEJGmNETaKJ5s3i9opfXIICgsuwi4JhBwFx2GdgmKWnp5dzqur74/QMaPSLMWqSui7+G7rPqburnqrnuZ/7jnkgTKN9fEPmIBWgWzXNdwafc8ZPclu2PBDeez+nveyCyZN1SUmJum7y+GUXTbvpiUMVVTMCowOFcBqWfyYIqEtbVr29pVF4LKyASbp26sCEcSPof/YZrHtvJoeOHOW8M3tw/4+/g5SKux95hn2HjtE0P4cffXcG3TsWEsvJYdWMW9G+Q5sWzZg0bgQbtrzP3rIDTBo3jHFDB1K27yArX1/H2GGDOe/sIl5YupIO7duRF3Pp1rkTFUcrqKisIScnyrAL+nHpiCGs3bCJTBD6TSjlcVqndky+ZAy9enRnaMkskkYzY+ollL69gfsee5pMveSS0edy3dSvA/DKinX85dVVYAyD+/dl6oSxtGzalHHTbsXL8Zh48Qj2lx/jqadCRbwp44ezbdce4vEEd3znGlL19Wzbs5evjxnKll17Ka+opGleDvl5BSBCJoyxmljUyfK4stqL4sTWbBFaWu10aeGtnT/3locOb1+nFiyY/P91AJf/yEH6ueeeM8ZY+cs5c77XqV2L/cYIZRrYp9m/iTgSz1VEPBfPdbIfK0JymNa0b9eGH3zrCky8knuz4MbrE6FCbbyGiWOK6d6xkIWLljFqSH/6n9ULkUhghYvWmmQ6NHkMdOgk9vSv7qbVKS0pLz9KRsO6DVs5a+AEps76AVob7nzgMfoMnMDjT75M1HGprqnm4itnM2rabIZPmcXyt94mGgk/u0+vbvzm57djM/Ucr4kTr09nJ0Yz+7rL2LxjJ3/btJlbZ16JKzRISSYT5r7HjriAH99yLdXl5WitqTxezeDhg5kyfjiPPPEcvS+YwrmXzmT0VbN59JklRKI5aK2ZO+9hRpRcz8jJM7nlzkdwI272lBw2mFlzEsHiRCy1WhtaN83JjD63zwwhhC4qKrJ8qoHQZwRYCGFLSkpE167Na847vceM5rkRobUxQgh7gq8UxuOGfw0sAWsNSilWrFrNFZNG8/tfzeWs3j14a9VqYpEIOvCxSjNr+gTe2bydyd/8IfFEkm9PuxSbSYd1VhUaeWkTyv2/uXwlhe3b8cTDdxBxBFZrhHKQ+c2I5BWglCSWW4Bo0hIZzSEIfJo2acqedYuo2bqMfe+8SpvCVvgm7PFdtOxNJo0Zwo9vmkY6WY/rOIh4Hf37dGZgv7788GeP8MN5v+bcs4ooPu9MRDxOxPWoT9Tz5oqVzL31OmZOHY9SEikMXzujF8YYnn5hESqaT9PmbViyfDPHK+PkRF2UUjz96M+p/3ANlTtXcNWUsaRra5DK+ftC/sltO1boqCdVYfPobXPn3r6tuLjY+bS4+5m36MatesECXVxc7Dzx0JylPQeN/2ldxv2x1r4vhHRP/pkZYxq1nsn6E1lg/h8W8L0bCrj6iok8/Ptn2bfvAEMGDaS2NkG/r/Wh3xlF7C7bzwM/uRWJ4dKxw2jd8RQyiUR4yBYQmtnCS8tW8eLrq5k/9/9Ql0xSU10TdkiQ9VyyIGyA1RkwoVFlIpng+u/eSdpKHMelvCqF60WxwD0P/4Gkb7ntplnhbrF5OzZTx/XTwq352ssmoLN30OumT2TZwtIwNrous277OQt+35Yfff8mrAVfh93+Uko6djyFNSvfpdpYCvKj1NbVkEyGJMV7H36UtzfuIbcgn1UbNuNFcsLCCx+jFZ9gcQRSek6+l/7LO4ufe5DiYqe0tDT4LNhJPuMoLS3VFBc7u1YvnJPv+q9bJ+IaawOs+bsSlsGCNAjlIIC6hOG2eb9h/dbtzJ3/P7jR3MZHv+nqyRit2X/oMOf068OWnR8QjUa5asoYEplQ59F1Iwg3FwHkNW3JQ/c/znMLl5IXi2ElSDcCMoJSkWzt2oAOU46huqykti5DdW2cZKKOzoWtsqQ4MCqHmbfcza79B1FKkYxnaNXlVCaMG8a+wxW0bteGwvbtOHDgEBcNH0xhz84kM2lyohEqk4Zps39CvC6FEALPdVm8uJRj1dX8+p7vc+0NU5k4egCvP/sgN1xXQl11FUJAdX2ayuOVVFYeo0thO2TMQzdqTfORHdFYDMJzcqT/YY8OsRkWJKWl+rPi5vDZh6V0iDGUisJ8OT1+NLlGC68jVhshwkpBI9AARiJsyK3KzVMsWbieoVNmU1tejqtCau0ZvTpSMm4Uf1n8BiVX3wrRXKROs3bhE8yY/vUw8Y4N/RyCIFRSz6QRkSjfvv0+TuvaiTZtWmKNAisIAj/kVOksWU1a6jIZYrEIr/z5gcYXueMXj7Kv7ADGGKLScOxABTNn38Vfn/wl6Uw90yeNp2leHld9ey4vLXwTlGX4kAEseeZXfGPKWFLp8FmaFUTZtGEXs394F4/d/1Mcz+VYxXGm3TiHh+/+Hr+b94PG/PqhA+XIiCLQmnt/9N3GDNMHZQfoM+YbBCIacsbFSdu0NRbp4Bi/viBWd1npS4urKSlRLFhgPitogn92hF+gm5zap18m1rpUSy8qbCCEkLKRIIgNXTbzcmjfphkHD1eQqM9gpIMwmlbN8mjWJI+q2jjtmzenrOI4VXU+juOgUwmaN4nQumVzyiuO07ZVE45Vp6mNx+l6aguOVCeprE2DNbRuGqNVQRPeLztEgCEn6nBq2zZUVNVSVZ3EioB2rQpoWlCA0QaBQhFwsLIGnc5wStsCDhyuIqnBTybpcmoHMtbgOB5NcqPs2rOfINtdANCjUyvSmYBkSlMQddlZth8rYuh0gh6d2xNPJCmvriOV0jTNV/Tu2hGpJLs/2M/hI9W0bJNP+1bNCExo3KWEJOVnKDsS/xipzmZ91FWghHGVf3Rq7a71z1Bc7PAZt+bPDzDQ8EUF3QaNCNyC13wwyhpBg+WltVgZdsTrTIDrukiVtaOTkkwqia6vx81rih8YXM/FaajvCoGv0wSJemKR0IswbQQyEiWoS4CfBumGiQtjwFVEC3JI1fmQTIaUXi8GjoBoNHzDmnj4tyab7GjejIhySfuZMFOVSiFiMYRwsFJjtYXqasjPw40o/NpawIVkOsxX5+SiHIsb80jVB+EzpVLgejj5zfAiiiBIk0mHOW9hwXGd0FLPD04Q/YxAyHBr/0jcDXnQvnSkKzNV30nsWvMQ/fq5rF/v/7NQfT6ATwI5r+v51+pIk99ZtMYa2UDctVmivJQydCMBTKDxkwlatmpCUa/TWLVqHcLLQTleeM+zoKTF14bWeQ6vPvUQ8+b/nmdfWoaTm0e/07tRMmowCEHEdfiwbD8LFi6n7NAhxo86n0FnnR5OpFK4jmDxmi0cOlDONy69EJ21qNu77zDPvvAaVemQEntOUWcmjirm9bUbWbpmE67r0SzP5earJ/Dmu9tYu3YDs2dMIicaJeK5VB6v4qWly9m0qwKhHEYNPIPLLhpKNCfKmg2bWbBoJRWVtShXonVAJhHnzL6nY61g49824+UVIF0nBN6cyMU3XC2FsNjsoUqmj96f2L361s+zcj9PDP74qSuguNipKy19PO+0oWgV/Z3BaGmFJBRAaJTOFyh8P033bp3o3bMrubmK3j170awgH61cSkvXkM4ESKWQysWvrmLWN6+i86ltWbxiLV5BC9K11RR1PYXvzpzGsaoqEvVJOrY/hWumT2LgyMsZc8FAZl1dwocHj6B1QG40wu4Dx4i5MHvmdI5UVhKvS9C946lMumgYl1x7G7XxFHd8+2rGDhvEyCH9WTnxmyQzhqZ5UW694RvE/vA8Gzb8jR/ffD3pdJr9Bw7Tscup3DxrOsMmf4tWLVrwyp/uY+++w9TW1FJy0VD2HjzCi0vWEnEiDB44gIin6HVad1whad++LbXxJGvXvYuKRrAfucU2MEBkIBzPkToVghuGxODzwiT5V0ZpaUC/fm7djjceFzpznSWijFVGoA1ZXlRo8hSS4dLpJOedexYRL4dlr6/izLPPpn271tTHqxHZNq10JkPz5jnceGUJv/3j/6WqMkkk6oI1xOMJAq2ZNut2OhX241u33U2vrp0YNvJCjlfVEASa0ZOvpduAi+l87njmz38CYSyB1lxx4+306DSQ2+56kAsGnM3pPU+jR6dCRg85l2Ur13F6r54MHdgXXVuFNoZAaxK1CTKZMFnzy8f+TPdugzhv3JU0yctn0tghFA/oA0i+c/vPOHPwRFr3Gc0bK94jkhsjHY/jKUPxkPPZvXc/7+/ay5DiIXgRSZBKoazKNgKIhn4ta4Xjo6KO0qn7E9uWhCv3nzhQffEAA6xf79Ovn1u/Y9njTpC6TkihDFJaY0zDqTrjp2nbtiXjRg7h8KEjPL1gIUvfWMnLi16jTetmjB4xLHQrUeBXVTJx7Pnk5ebwmydfROQ1AW1AuaBcHKU492tncMHoIZw3oG/IvKiowot4OI5ixctPcnDja+xdt4iivkUkUwGOUgz62pkMmzCe4oH9ATh4cD8zpo6nuq6Wr0+/ke3v7+LGqyaDTmKNxlEK4Uhcx0NIwelF3bjw4jGMGXpB+J3Hqnh5yXJq6up5+c+PsH3jEq654hJcGaATcXr17s6ZfU/nnTXv8PJLi3nhlddZvfZt+p9zNqf36UMqWXdyF4oFYYRSrgpqT4AbXofsvwKPwxcx1q/3KS526kuXPR7reWGFFrFnhLI5ShMglKOkRFtDMpUm4sZCgp0JQpl7KUgGYf+wNgIvx2P2ddN4+sXF7Hn/Q6Jt2mGsDnuhIuHj/ujmG/jJ924A4MEnnmX18nVccckIAB7943OUV1bj5UQpO1JJr55dAfjpzTPhe4JUKsktP3uEeH2CGZeNJ5Wu59ZvXg1GMnzQufTq25OamjosYXun40r8IOCi4UO4aHgo8/vK0pU89cIbHK2s4rTzv87oCwdy8cjBzPv+LAoLW/Ltm+fh+2lSyRTRWAShHKw1OE6WuWIsyokiURh8g5DSIBR+7XdSO996qOGm8q+C+8Ws4I/F5OTON1+Wxh9prdxn3JhjrQ6UIzlacZw3S1fTrGUTJk8ax4gxxYwddSE1NXUsX/02wvXIJHzGjRhEUbeOzH/8f5GxfITWWVa/DbUugJt+9DOm3zwHgD17yzCVx9DZqVi9/m+8vvo9Vq7dRKsmzXDd0LDyu3N+QZ8hl3P6sKn8Yt6vuXziGJoVFFBTU8f0yydgnfAQdv20S0n7Wca2Dsgk64i4Hk8+v5BB468imc6QSic4WnaAaVMvYfa1k3njrbe598HHSaXTFHXpCJEou3fvZfOWrfQ5ozeXXjKaCZeMZMCAc9ixczc7du7Ci8UwBIFxI9IKWW+Nf0XmCwb3i1vBHwM5Vbp0VaTnqGIrMn+Srns+2milpJBKytIVq2lWUMD5gwdRWrqKaDRCbo6Hn7HgJ5l93RReX7GWDZt2EWveAqMDVChxjfEzABwoP85fn1/E5ReNYP6dt7J4yZvE49Xh6nrq142/2j+/sJhnX3kDgPcPHmTLpt24LZqS17Y1My+7mK3v72HARdeSxEHoDK89+QA3XHYxLy58E99YXCmwJtxHy6sTrF60irsefoK7b5lFyRUraB4R3HbjNdx24zUAHCo/yj2PPIn0Ijgu1CUyLF64lNP6nIZUDn995TUyaY0TjVptfS3cHEcYZ5cwqSv9nYvWZGNu8EVCIvgyxolfoYoUjZknpHOLMD7aD4JMOuU0b9aErl278O76jSAs0ViMdCLDhYP6suzpBxk97SaWrNgUsiV0eDc2WtM0P0bnwraUHT5GVXUdTfIi9OzUjrJDxzDap32b5vgmlDVw3QjHq+uoqY3TrbA1uw8foy6RDh1ipKBnx7ZUHKviSGUcL+qR8g3tWjahS4e27P7wIC1bNaWqKk55eQV9enSksjrOwSPHUcrSp0dn6jOCLVt30KFDW3p2KwRr2bL9Qw5VxonmxrDaoIOAIJmkW/fOIAS739+NG41pGYkqqTxMoJ+PiMzM2m2vHf9XrkJfPcAh6UfCXAtYr/eYiQbnEQfbVhqjtQ5EJghkJBrJipNY/LRP7x6d6dahBa+uWI+Ph4OPtbKxS01rTZDxcVwX5Si0NgSpdOifiAgTIUGQfSsLOTHcWAw/4+M6Cj+dDpm6KqTiSKXCLkFjQldxP4B0BtmkSRgWFOH/S6YRjsLzXKyFTDINQhLNiZJKpyGdAUvoguq5JypqWVOuTDoD1tpIJKKNVI6xJim1vT2549VfZleEggX6y0DhSwQ4+/nFxYrS0oDu49p7KviFkO6UsGXYBqCVtdkErDSkUxnIGLz8nEaDR3tSX7jI6lWQ7fYLuz5Do+UgCOjZqydtTmkXKgwYKPtgL/v3fhgq10nJWef0o90p7Thy4DDvvbMBBJwzoD/SVaT9NK7n4iqH9955l/p4AqEUOkvKb5A1bADNZr0Rw77mrAyDodEW9qMHHaGRUhkk1gbLldA31W99bSNz5kjmzrVfVLz98mPwJxYoSgMoUexacDADl7k9R76M9O5UUnbB+GCttkIpDMQiEYg5aBNkrenEyYIWIcsAzUdkXKxGSYmfqueCkUMoHj6MDe+EwqgT2p7Cb++9l52bt3Lb3T+hV+/eHDi4n84dOvK39e/xm4d/xZAxwylo1Yr85s2JV1VhUin27NxFTVU1XtZL0ZyQUMnmb8yJ1aFtVmE9JA82NoVZAViNsNIqVxnrH7dB+p7MzmUPAJbiYoe5c4Mvef4/mTb7xY9tWbTmSFP5+00mt9efhGOERZ6J40Wl8QGhLQhrTVbeTvzDveZEh50kSGc4o//XQEju++Ectm/cTPGIoezcspXOp3VnzMXjuOsHd/D0Y4+zY9sOSq6cSu2xY/x23n28v2MHxUOH8Yf5v+Kp3/yWVOAjhfwnN0JxsjyUFsKA4ygtpNDC/5NvMpfpHW8sapgHyv6ov4qZl3x1w8JcQ0mJouylan/7wu8LafujzVMIGQjlqmwhKpBZI+1GxSUhPrKHncwkOXmS08mAZu1P4c5Hfsmc++8i4sIH23fRt+9Z7Ny2na3vbiCvaQs2r9vAxs3b6FjUB4TCKEk0KsCxGGE///uFvBsrlKOs9KQ25jWj00OCLUuuZPubZRQXO43z8BWNrxLgBnqIzsZmJ7P11e2Z7QunaehvtP+UFcK3rudoJcO9FxsqHYoToP5dr05WhT2rk8DhAweZd8vt3DH7+xw7VsXYSZfwYVkZbTt0oFnrNtQdOkCLNq1pW1jI0SOHwUoETrbQEXKoT2AsGosAn35csQZLAAhURFmBsCazxBo7IrNt0ahgx7JSSkrCH++XcEr+d8fgfxCb50jYJvztC94Dpnm9x96tTOqaAHW5cLz21mZLfJYgRFLIT51pKciLRejcsZCxUyaAcmnVqh07gs289coiLhg1gtt/fg9b16+nT//+iHSa5a++gnAt1mTwcgpwnGjjvffvt2B7MofGCoSxWEdIRyKUtCZTK3TyeSH4bXL70rdPLKA5sGCu/jfN81cVgz81M2LD+DxHQmupj75UERz9YGleqy5/8K39QFibC7QXjusipMQagbVaWGFCJecTsmAWyM2NodNp3JwIbtRj7fJSXnvxZRLxBBvffY/8ggLadOzIvvd38cdHH+PI4XIcz8MV4Loe2zZuoqamGqnUybHVCiFsmPIQRgihpHKEUNkKtmWdtXa+FM6Nqe2L/xwc++BgCGyJhG0mfMd/3xD8R405kuK35MlbmVc0ukgiJlrDcCT9hXSiIqxDZkuRRmcJ2fiZtNS+H+7XNuyz9GJRlPIIMj5+OhPuuMYgox4RLxLSe4whnUpbx/NQjmOxxgohQ2KyEAqhQjdxLNb4VljWWyHf8q14Ptj+6tsfTfAUfaUx9r8M4JOeq6REEjZTNU5WpGhkN0fKc7UxxVg10Fp6SuUosvfS0BvFgtBZjTVhTcg2sEKedOXKMj+NMVk7FYGUrjAnOEfhfRuL1T7AB0LYdcBbUoo1iS2LN30C+cEA5j9vIv/jR+Oq/ngCXkR6X9pFEhRZbc+0gl5AN6CtFLaFhJyGepyV4qOvnP0UYxucVCxYUW8RtQJbDmaPtWyTkk1ayC2ZaPnuv6PLFBc7lA4x/0mr9b8U4E8Au6Gw8QmjebfRBYkct4XrZwqNUgUyMF6gTNFHLg5Z5SCj/Q8k1GpBGtfb42VUbXzHi5Wfml+vqBD/DaCePP4fYEYBNs4TU5UAAAAASUVORK5CYII=" style="width:72px;height:72px;border-radius:18px;object-fit:cover"></div>
    <h1>Kurtex Dashboard</h1>
    <p class="tagline">Truck Maintenance Command Center</p>
    <div class="stats">
      <div class="stat"><div class="stat-num">24/7</div><div class="stat-lbl">Monitoring</div></div>
      <div class="stat"><div class="stat-num">Live</div><div class="stat-lbl">Updates</div></div>
      <div class="stat"><div class="stat-num">100%</div><div class="stat-lbl">Secure</div></div>
    </div>
    {% if error %}<div class="error">Authentication failed. Please try again.</div>{% endif %}
    <div class="divider"><div class="divider-line"></div><span>Sign in with Telegram</span><div class="divider-line"></div></div>
    <div class="tg-wrap">
      <script async src="https://telegram.org/js/telegram-widget.js?22"
        data-telegram-login="{{ bot_username }}"
        data-size="large" data-radius="10"
        data-auth-url="/auth/telegram"
        data-request-access="write"></script>
    </div>
  </div>
</div>

<div class="caption" id="caption">
  <div class="caption-title" id="caption-title">Fleet Management</div>
  <div class="caption-sub" id="caption-sub">Photo: Unsplash</div>
</div>
<div class="dots" id="dots"></div>

<script>
var photos = [
  {url:'https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?w=1920&q=80&auto=format&fit=crop',title:'Fleet Operations',sub:'Keep your trucks moving'},
  {url:'https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=1920&q=80&auto=format&fit=crop',title:'Route Management',sub:'Every mile tracked'},
  {url:'https://images.unsplash.com/photo-1519003722824-194d4455a60c?w=1920&q=80&auto=format&fit=crop',title:'Open Road',sub:'24/7 driver support'},
  {url:'https://images.unsplash.com/photo-1494976388531-d1058494cdd8?w=1920&q=80&auto=format&fit=crop',title:'Highway Logistics',sub:'Nationwide coverage'},
  {url:'https://images.unsplash.com/photo-1615799998603-7c6270a45196?w=1920&q=80&auto=format&fit=crop',title:'Maintenance Ready',sub:'Zero downtime goal'},
];

var current = 0;
var bg1 = document.getElementById('bg1');
var bg2 = document.getElementById('bg2');
var activeBg = bg1, inactiveBg = bg2;
var dotsEl = document.getElementById('dots');

// Build dots
photos.forEach(function(_, i) {
  var d = document.createElement('div');
  d.className = 'dot' + (i===0?' active':'');
  d.id = 'dot-'+i;
  dotsEl.appendChild(d);
});

function updateCaption(p) {
  document.getElementById('caption-title').textContent = p.title;
  document.getElementById('caption-sub').textContent = p.sub;
}

function setDot(idx) {
  document.querySelectorAll('.dot').forEach(function(d,i){ d.className='dot'+(i===idx?' active':''); });
}

function loadPhoto(idx) {
  var p = photos[idx];
  inactiveBg.style.backgroundImage = 'url('+p.url+')';
  inactiveBg.style.opacity = '0';
  setTimeout(function() {
    inactiveBg.style.opacity = '1';
    activeBg.style.opacity = '0';
    var tmp = activeBg; activeBg = inactiveBg; inactiveBg = tmp;
    updateCaption(p);
    setDot(idx);
  }, 50);
}

bg1.style.backgroundImage = 'url('+photos[0].url+')';
updateCaption(photos[0]);

setInterval(function() {
  current = (current+1) % photos.length;
  loadPhoto(current);
}, 6000);
</script>
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
  --bg:#FAF8F5;--surface:#FFFFFF;--surface2:#F3EFE8;--surface3:#EBE5DA;
  --border:#E2D9CC;--text:#2C2416;--muted:#8C7B6B;--muted2:#B5A898;
  --accent:#C17B3F;--accent-bg:rgba(193,123,63,.1);
  --green:#3D7A4F;--green-bg:rgba(61,122,79,.08);
  --red:#C0392B;--red-bg:rgba(192,57,43,.08);
  --yellow:#C17B3F;--yellow-bg:rgba(193,123,63,.1);
  --blue:#2E6EA6;--blue-bg:rgba(46,110,166,.08);
  --purple:#7B5EA7;--purple-bg:rgba(123,94,167,.08);
  --shadow:0 1px 4px rgba(44,36,22,.06),0 4px 16px rgba(44,36,22,.04);
}
[data-theme="dark"]{
  --bg:#1C1810;--surface:#241F15;--surface2:#2E271A;--surface3:#3A3020;
  --border:rgba(255,255,255,.07);--text:#F5EFE6;--muted:#9C8E7E;--muted2:#6E6050;
  --accent:#D4904E;--accent-bg:rgba(212,144,78,.12);
  --green:#4CAF70;--green-bg:rgba(76,175,112,.08);
  --red:#E05C4B;--red-bg:rgba(224,92,75,.08);
  --yellow:#D4904E;--yellow-bg:rgba(212,144,78,.1);
  --blue:#5B9BD5;--blue-bg:rgba(91,155,213,.08);
  --purple:#A07CC5;--purple-bg:rgba(160,124,197,.08);
  --shadow:0 1px 4px rgba(0,0,0,.3),0 4px 16px rgba(0,0,0,.2);
}
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{font-family:'Plus Jakarta Sans',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;transition:background .2s,color .2s}
.hero-bg{position:fixed;inset:0;z-index:0;background:url('https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?auto=format&fit=crop&w=1920&q=80')center/cover;opacity:.08;pointer-events:none}
.layout{position:relative;z-index:1;display:flex;min-height:100vh}

.sidebar{width:220px;flex-shrink:0;background:var(--surface);border-right:1px solid var(--border);padding:20px 12px;position:sticky;top:0;height:100vh;display:flex;flex-direction:column;z-index:50;transition:transform .25s,background .2s;overflow-y:auto}
.sidebar-logo{display:flex;align-items:center;gap:10px;margin-bottom:24px;padding:0 8px}
.logo-icon{width:32px;height:32px;border-radius:9px;background:linear-gradient(135deg,var(--accent),#A0622A);display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0}
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

.mobile-header{display:none;position:sticky;top:0;z-index:60;background:var(--surface);border-bottom:1px solid var(--border);padding:12px 16px;align-items:center;justify-content:space-between}
.mobile-logo{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:700}
.hamburger{background:var(--surface2);border:1px solid var(--border);border-radius:8px;width:34px;height:34px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--text)}
.sidebar-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:48}

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
  <div class="mobile-logo"><div class="logo-icon" style="background:none;padding:0;overflow:hidden"><img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAHgAAAB4CAYAAAA5ZDbSAACD7UlEQVR42uz9d5idZdnvD3+uu62+ptfMJJPeO0lIICR0KYKU0AUEKQIqCIqCGiKCIgoIgkiRLpDQewmE9F4nPZlJJtP7mtXX3a73j3sSQPHZz/7t57f3s9/3XccxRw7IZN3lvK6zfK/z+z0F/xd9pJTiggsuUDo6xoilSxdIwPnq3yuAz2eQzua0L754v/iLNbXB9999V9+5q2mYbR/+VRsQ0uczxLDqgY2nnnpGcuLEGuuiiy5qDQcDdjabxZH/cml1zpz5orR0rFy4cJ4rhJD/t7wz8X+HURcpixY9KmCpfeSNC7Bd6Xvlg8WD1m/cOqaltWt0KpMdlcrZA1NZqzLWmyjKmrlgPJlVrJzpQ7re9/U/sVAUDE2zwwHdCvp9Vjha0JYf9XcXFYQbDcO3q7QgsnPG1PG75p1+Ur2uKinb/ZpN1XkLF7Jw3n9/Y/+3NfC8efPURYs6vmZUKaXv1w8/NW7X7oOzurp75/amMlNjffHqTM5WsqaD5YDrSqQjkYoACQgBwpVSSqSUCKF8+dgCIZEICQKBUEAVCoaq4NcFAZ9GXn5eS2HEv7U4v3DFpDFjlt/xw4u2qoqIf8Xe6rx5C1m06AIX74r/fwP/R/czb948ZdGiRUdelpQy/KuHXjhpxep1p3R2dZ0cS2aGpUyFnO1i2w6OdHBdx8GWEscC6QiEEEIVQtN1NF1D1w2haVq/sUFKieO6OKYtLcvCsiykbUvcfrOpikTzC0XVVVUR6LqOYWgENSgJ+VqKi/I+O2rGlMW//8lVHylCdBy26pw5c7QvvvjC+e+0q8V/Hzd8gbJo0SIHIOD3Mf/3Dx+7bOvOCxsau87sTZg1sWQW23ZwpYNj2ba0LQEI1aeLgsICUVpeQllFKaUV5RSXllJYWEQ4L0ogHMTv09F1DVVRkP0Gtm2bXMYmlU6RSiTp6+6ho72D9o522ps76Wxrp7c3hplJS6R00XSpKJqqKoZQdZf8UIDi/FBHdXnRh7MnjFj4i59c/6kQwur3P+r8+WPkggUL3P+fNrCUUsydO1ddutRzw1LK0MU3/eL83XXNV3f1pGbHMhambSMdx7EtS0rHUvRAQKkcMIDhI4YxdNQwhgwfQmXVACL5eQR8BkLVcBG4roPrut6PdJGeP/jygYVACAVFOfwjEEIgHRc7ZxLv66OttY36/QfYs30X+/bW0drcgplOS6Hqrq4ZuKqiGrpGvl+jJOrbPmRw6d/vv/3Kl4YNm9TRH2dUuXDh/9E4/X/MwF6M9XaslLLw0h/96rra3fXXNnb11aRzCtJ2pGubjmNmFSPkV4YOH860o6cxefoEqoYMJJxXAGjYjo1lmbi2g+u4CAkIiUB6RgRcIb7ywMqRUCm9ax9ebf0h23Pjmua5d03XQEpS8QSNBxqo3bSFjWs2sG/3fjKJtMQwXFX3CVURStAnqCgId44fMejFW6674uGZk0cfPOy6ly5d6vyfiNH/2w08f/58ZcGCBQCulDJ65S2/uWF97e4bm7rjVemsi4Z0cpksCNSqwVXMOm4Ws2bPpGbEcPzBEKZpkTNzuI7jpcSK9xAqAlcoCCRCOl97Mvfwowov8XJcAUhURSK8f/G1d6949kYCrgSJi6Yq+HQD3TDIZNMcOnCADcvXsuKL5RyoawBXuj5/wHUUVfPrkor8YPyosSP/vuCOHzwwvKqq0VvUC9VFiy5w/r/XwPPmqSxa5PgMnYtuuPW7G3Yc+mVTd2ZEKplBFdLOZVKKYRjK5OnTOOmsU5h41CSieXnYtks2m8VxHIQQKEL5H965FF/+ivD2Jpbj4jomUcOHokIqZ2FJDZ+uIuVhI0tvF389lIAEV7q4rkRRBX6/H103SMX72LFpK4s//IwNq9eTSaWkLxhyXOlqPr+P6hJ/59Qxg//4wp/ve0gIYQKqlPJ/m9sW/xt3rQvw+HPPHfXi2yvu3X2g4+S+jIsiXNtMx1XdMMSMY6Zx1oXnMnbSRIQqSGUyOJaLomgoype77MuSR3zjA8n+P10JjutiChCmRYVfMKWmCM20sGyLYDDInj6TfZ1JFM0PuMhv8KJSyv7riyMu3XVdpJSomkYgEAQJdbt38+6iN1nx+XIy6ZQ0QnmOdKUWDiiMrC7dct63Trjj5zde9qEjvx6i/q82cH/8saWU+kU33PaLjdsbftnYk9KRiuNmc8KWpnLUzGnMu/RCxk6djIMkk04jXFAU5UgydHh3/bOBPSfret5XguWCY9toUhA0FKJBg5BPocCnMTig8ugzr/D0ok/ImTmOGlrJ3b+6mWSokPUtSXx+P4YqUJEgZb/L9xI0oaiA/DJmf+XjOhKJxBcMYGg69Tt28vrLC1n+xQpcS5V6MOQ4Tk6rLg4xbeKIJ1/9yz0/F0L0MGeOxtIv6/z/qwwspRRCzFVhqf36Rx9NfPSpNx7ftKfl6JRpowrpZJNJdeiYUVz6vUs5es4xuIognUp7MVBR/vVG+3frP79gBYktFEzHRnEcykI+hhSFKPUJwKW3q4/WlhYS8RSfLd/IPQ+9wKgTTyKvvJiNn37GxMogaz5+mW4XGmNZmuMZkpaDrmqoqkZ/ruZd6ci15TfmS162LgkEghiaytZ1m3jx6Reo3bQNIxB0XSSGLpSpQyvrL/vOSTddd+XFHwLK/Pnz+X+rpBL/b7pkVYHrfvq7qz5fvfnhfe2xkK4qdi6dVQNBnzj/kgv4zkXn448GiSeTKFJ8o2H/wxsXAst2UKXDqGI/I/KDZFMplq5Yz9ufrmD95i20t8XISgXhC6IYOv68KFbOxkwlEFISa2/mO6fP5dxTjmPWtIkMGFRNnwvbW+N0pkyE7kNVVFTp4iL6F9rXvcnhBfjP7jsSCuFYNp++/zEvPP08vZ09+IMR23RMbUh5iBNnTrn7hYfv/XU6k2HewoXqogv+6xOw/3IDH84UpZTB8665/c8rNu/9fmcsQUBXnXS8Tx03dTLX3HYjo8aPIdGXxnUcVFX9xtj3z3H1ywRKIqWKbeWoiurMKItwsL6Bx55ZxMIPv6AvC8PHjmLY+AnkVQ5Cj4QxhY+MLUlmbRKZLNlEL3YyQy4Vp3F3LX0NBwmJHHOmjOay80/mjJNmk/MF2dTYTZelEtD0I3X0YTzb893i68nYV+5aOi6KEATygrQfauaphx5n5ZJlBMIBN+cI8kJhZc7UIR+9+efffF+EQs1z5s/Xli5YYP+3NfDheFu7b1/1Tbf/4aXN+9tnZ03Llo6tuq4lLrniQuZdcTmOXyObSKMr6r/cwVeTJynwcGIJEnkEWpYIXDfDtPICCpwcv/r9ozz+zOsUDa7hjEsuZvTUqSS0IM3dGZp7UsRSOSzbwkGiSAWhCoTuQxcCTbioiiAVi9GxZQMNKz+DZAcjayq4/YbLuPTic2mI22zsTKFpWv9OliCUr4WOf8m6v/JxHAe/z4emaXzw+pv8/dEnyZkS3e+zFEXqU0dVHbjl2ksvPO/kuesPv8P/dgY+fGN/fvqFMc+8/vH7uw/21kihWLlsXC8tKuamX/yY6XOOIZZIgeOiCeU/E8dxpYtQVXyGgSIEluNg5dKcNiiPxroGzrrydmJpi3Nv+gEDJh9FXXeauqYOkhkTXWj4NA3HlaQcUN0swvCjKgLVtXCFiitBSIkqdCzTJlW3leTujfgi+RzasZW5Ywby2P23U1lTw2d1nVi+MAFdI5Pzyja1Hw37j4ysALaU2LgU5EXZtWU7D//2fg4ePEQgWmhnTUubOKQ0fdkZc8+97YYrPp4zZ762dOl/zU5W/yu+ZOq11+qr33jD/t0jT879+6KPF++oby8zDNXJJOLa+InjWXD/AkaMH0NPXwINBfWbVv3hP10XR0qEKggGA4TCISzbpr7+AE1NzaRTKb41spIDe+qY/Z2rGDtjJjf94R66jCi7DnWy41APGgZC07AcSco2KQoKTqj2cfXUcgqCktr2HIam4wKKVBFCx0aiqw4ykI9jqkhpMvXc77Jtz0GefOgpJo6u4lvTxrJh/wEamtrILyggLz8PTVOwHQfb8bJt5TCg8pWgchghUxGkM1kGVFVy4slzaDzUzIHd+5RwKOQeaosZh5pbLrv55puan3n8tg1Tp16rt7ZudP+PG3jO/Pna6gcesO+8/8G5L77+6ft7G7vCQZ/PScX61Lmnn8yd9/yaYF6ERCqNpmr/6jIk4LpI6aIYGsFQkGDAj5VJs3vHLj59/0NWf/gR8fr9dO7ZQYliM33EEI45/RLGzjmBC2/9OY29cQ62xmnpyxHxa+RsC93KMqnUz42zKjl/bAkzqkLMGFTJkvputrebBBQ/Lu4RJ+ZhYBJdF7halGRbIz0dXQw7/mRkIMSjv/k9I4cPYPrwQbzy9N9Zu3oNe/bU4zoORYUF5Bfko+kGju3iWrZnXsVDW76Kf6uKgpXLYQT8nHjKXJLxNNvWrxehUMBt64rJhobGs79/3bXNr71074ap116rt278XzOy+K9wy7975K9zn1r46fv1zbGg36e7mXhMOfe7F3PlzTd4sKJtowqtP6i6Hu4rvXNYXdfxBwPYCnR1drF32072bttKsr2FquJ85kybwJzpk6kaUHHkupfeeCefrNvJL597nleW76S0IEg2J+hLmziuy/E1IW44tpKaSJgle1rY0NDMeZNHkh8JcMkrW2jJBAgoKhK7/w24/SCGB0nGuuLkWptJ1m3GXzqS4tE15A7tZuc/nuTTt57kpNnTaWltZeWGWpZtqOVAZy++gmKGj53A8InjKS8rQUWSyWWwTMvLI4Q4Eq+FlLiujVAUwuEw/3j6RZ5/7EkCkTyZNS13YFm+euk5J1xz7203PPW/GpP/Hxt4/vz52oIFC+xnXnh57h+fffv93Y2xoE9T3HSiW/nuNd/n4uuuoi+VQpN4h++uBNdFKALdMAgE/AhH0tHezs5tO9i5bRtmTzsjBpRy3IyJHHPUJEpLio5cz7QtVEVlb30D40++jJ/+7j5aopVs2NuG4fODEGiqinAtKiIKxwzwUx9z0J04d582hfyAn9VNHVz/Vh26L4TiSs+w4isBQoKiqph9CXqbW8js34QaLUEG8qkaXk1n7VrcXavZ8Ok/KCnIP3JvXT29rNlUy4o129jR3IaSV8jI8RMYM34sFRVlKIpCJpvDzGWPGBvhgqtgYxPNK+Ctlxbxtwcfwh8ulKYl3eHVxepPrjnzmusuuuCpw+/6f5uLnrdwofrYTTc5Cz/7bORDTy1aWlvXHfL5fW4mnVCu+eH1XPT9K4nFkwjpgX8KYPj8BKNBFFWnramRNcuW88m777J77SrKhMV5c6Zwy5Xncdapcxk9fAihUBDHcb5ErKSNpuo88vRCDsQynHHFFSypbUQYAVSh9CNZEiGgN+uwqd2krjvH2IoCjhscxa/pfLijnaXNJiFdQ3ElUnH/ZblLJI4L6b4kMpfAzeQIlpST6EpSPXk6ezdvQyZ7Oem46WRNE4FLOBRixJBBnHTcdL5z/HQGRAwa9+5m5ZKlbFi7ga6uLvSgn7ySIsLBEEKoOI6D40qEUMnkskyePoWSwkJWLVsjfIGgaOvucTs6us9+6KE/bbzxmu/tnr9kibb0uefc/9d38GEQo6ura8AZV/90+dodhwYH/CEnk+hRr/zB1Vx2zZV09/ShKAJ/wI9mGKQyORoPHGTPtm00792D38kyYXgNxx89lemTxqD7fEe+37ZtRL/BjpRLgMDBlYJJp1zOxJNPY8BxJ/Px+gMYft1rt+qHNKXwYp+i6GgSelJJRuW5/GTOYJ5c08KmDoewoXlOWXH7s90vX4ODi7RtuuuasBM9OB1NRAaPwcxm8RcXYaQ7aV+8kG2LX6CspNgrhb2jMaR00FTtyPdZtsWmbbtYsnYjW/ccICl0yoYMZ+zECVQPHkwoGMA2LcxsDtvOUlpYwNuL3uXPf3yEYCgi06msO2PCoMzDC246ZcaECasXLlyoXvA/CYb8TxnYgx+FIqU0vnXZTYuXrN81S9d8Tqq3W/3OJfO48ee3kbZNQj6DTDpD/b6DbNu0icZ9e4kqNrMmjuKEWVMZP3YkivjSeZiOjSLEkZ14+LaOIEOORNUUdu/bz5TTvsdPH/ozm80gLX0mYd0ACbadw8nlsDMZcrkMip0DoaP6dFwEmuHDiETR9QAW4FoWjmMhLIlUHO8M2RU4igKORff+JqRpkj20h9Cg4VjCRclJSgYVs/GZB3nil9/n+5dfiG3bnlH7Xb2XM0pcKdHVrzvIHXv28fmKjayp3U131qJ06DAmTprMiFFDCIXDZHMmPp+fF594hmf/8gyh/KibyaSVk4+d0PTRsw8dI4RonC+lWCDEf3ona/8zBp479y5VVYR96Q9/+ZdVtfWzhKrbqVivNuf0U7n5rttoaYtRv2sfuzZvo7O1iYqIzjEThnPbWZczatiQr6wnF9uxUfprSEPV/rX+dSWK4rkwkDiOw8oNW4gUl1E+fBTdn6wj09RMe0sTqe52nGQM4WbQpUQ4LkgXywXbEQjVQPr8aKEAofwiIqUDCFYMwCgoRfjDSNfBsR1sbKRwvYRQCFRNBaHg2CZCM7DcLBnTJW/oCD5Zto7vX37hESBDURUvkQKvzj78pK7EdT20buzI4YwdOZwfAnUHDrB83RZWfv4Ri9/OkFdaztjxkxg2aijX3Pw94rEEbzz3DyVcUOB8sXZH1VW3LnhFSjlbXHDB4Y0m/0sN3I+V2rf99uErX3p/+VVpU1gyZ+qjJ43hwssu4qF7HiHb282oAQWcNXkEx117JuXlZV85dHex7X6jCgVVUf8F7Tvc86YoAlUVWI7N/oZ6QobBwKoqtu46gKMYPP/HP7Bp+RqK84JUDh5C2fghBEpK8IcKMA0/aRRAxZUulmWRzqTI9vaR6u4l2d1Fx7b1ZJa/B5pGsKyGouHjiQ4Zji9ciCIcLDOLtB3QVFBcpJUB1QBFYGUsokNGsWPPKtK5HH6f4dW+/4RLf1n/eomblB5G7UjP2EMHD2bo4MFceSH09nazcu0Wlm3ZzEuLP0ILRTjprG/TdOAg61atVY1gnv3usi0z73r4ifvEokW33XXXXRpeg/d/jYs+HHfXbt489vu/eHDdzv3tPs3QlKBPFX9+4iGef+p59GyK44+bxvSxIygtyUNVFFQBAb/fAyz8Brqu/gdr6stY2B3rY922XXR19zB98lgUIfjL06/w3CvvEy6pYMzUMUyceRwFg4Zh+iLEsxaJrE3GtrFcB9dyyeQs0rkstu0iFRWhGqiKgpAObi6NGesh3nSQ7h3b6G1vQdF9FA0aR/6ESUjFR6KrG3DJHqjFKChCiZbj2jkMI0ggCG2fvMCGN59g0569REJhpowcStGRzFr+h6/Xtiwy2RzJdIZMLovluNiOoDeRYsuOfXy8eBVtVpZbbv8pP73hVno7+mROus6o4RXa/b+4+pwzjjvurf9sPP5P7GApFuy8QEgp1TOv/unzexo6goZPdbKJpPjJL+6grGYQBi6/vvVqWrtivL92C9KG0sJ88vKCBEMGfsOHX1UwdAVdQCAQIBwKEPL7yIuGCfl9ICV7DzZRW99CXWMTY4dWc8bJx/PMC4u4/6GnCZZWcfZNN5JfPYrelMLa5lY61n5GX1s7lp1D03SCoSCR4mLyKirIKyggL+DHDSmkczaJVBbHtFEUBVVVIVJCwcRqSqccSy7eQ+/u7bTVbqJr0SZC5QMIDR6FP6+MjOrDdQUGYCsalm2RH4iQTOfo6OhG80f4bP121u3cT2VxPlNH1DBicDVSCDLZHKlUhlQmSyprkspkiSfSpHIWOUdg2xauA7msTVdvN9lcminjx/CLW6/i1w88QTA/j1vuuIWf/+iXIuAPKjv3t7qPP//uk1LK1UKIzq82Uvw/NvC8eYuURYsWObfM/9OvVm2tn4Ki2plEXDvjvDOZ/a1TaOvqIeg3GF5dxuYtWzGsDJu27+fz7hiOC7ruoyDsozA/n2hREdH8KEFDJRIOkp8XJZvJ4NMUuntjOEYYQ4Wzjz+aQRWVnPfdm1i1aR8nf/cKgjVD2Lp1P40f/J1EVxtWLIYqHIqL8on6A14nZCZDs+lgqgZqKELegEGUDhlBUVUV4eJSnJBBLpvFyZloigZWBtsS6L4wZUcdS8HQCTRv20TPjrWkvniPyLCR+H0RZCCCpL9p3pIIXceRGgdbWjn2mJmoaKQyGdqSaRau3IH62XoGVBRj2Tb50TyymSzZrEk8laarN0FPbx+9sR7SqQTZrIlpemfaaTNHQ109j937c/KjIdp7+pgyawYXXn4B/3j6H4o/L9/+fO2e4lt/86e/6Jo6b+zYser/0g6eP18qCxYId1fdofEX33Dnr3p7ex1NoA4aUsMVN1xLKpNBuC6G32DHnjp0TeP6eafyxcASECq2K0mnTTp64iQTKbrj3Wyv28PkcWOI5AXo7e1j9oyJJJIZ4hlJpcwyd9pE/IUFzPjWJfTp5cy95efsqN3Dobcewc30guMiHZNjjj6KucfPZUB1JYbPh+NC1rSI98Vpb22lvq6evXsPUPf2OvbqIYqGjaRy4mSKBg3G9flI9caxLRcLiXRMsokcTiaNXlBC2awzybY30Fe3i2S6hfxAAH90KK5p4ZhZDx/x6fR09xIM+Ajn+amMBqmq30RzYTV7DB9DBlUTDhg8t+gj1mzZi2nZuI7tQZWWha5F0BU/hj+C5vchhINPzbJq0x7qG5pQFA0hIJnIcOn3LmPrlm3s2rJLc1XV/mDZpvPf+Xj5vNNOnLXof+Sq/0MD79x5gfD7DHf+Hx9/eMehdj3oNxwzkxHX3nQ9kbx84skkqgJSuuiGj4eeeoPCggJcM0dzcxtCERiGRmnQYFBBPhXlQ/nWad/mQHs38WSGR558idNnjaVmxCB21h9iVNVAiovymXvZD2mReYyecwrLX38bs2Ufuk/DUnUiER8XzfsOR00/Clu4mKZJxrSQQqDoCsXlxVRWVTDl6Glks1kOHWigduNmNmzYyNba1URqxjPoqFkUVg/Ckgrp3gTZRBzp2AgVbEeiBkIUT5xJ5YhR1H6wiNYVHxMpG0rBhEkoehApdWQgj0Qyg1DBzbnkdB0RLaJacWiJ+Jk8fAg9sW4WvvUReYXFqDgEA35CwRBFReNR9SiWk0HXdYQQdLa1k+qrxXYcspaFqmr4FAXXdZB+H9fdeC233XgrQlWVPQ3t7hOvvPMnKeXH4q67kv9RVq39D7Jm5y8vvHHxfY+/PFe42Jl0Rjvl7FM5au4sEvEEhqYiTS+lUBWVA81drK/dz69/9F1SGZNcLktfrA8zlyWRStHVk+Dnv3+UT1duoisWRwiDay79DhWKgm1nGTdjKs+/tIgNO5oZddoFbHnzRcjE0IIBHEcQDkruvPVGSioG0NkbA0USiYaIhEP4NBVVEVi2S85yydk2hmEwatxYJk6ZyrkXzGPzpq0sXbqSus/fo7WikrIRoymqqIKCQqTllWKO5WClYrSsWkPHnu0MLMrj9B9cye4tW1ix5HWCQ6YxcPhQDF+IjGWiC68Zz9Ic1KnHYefisGMXqiJJJNPkRaLouCRTSYQQaLokZ5kU5IGBn+7uNJrm0tfXgG2lUYT00D9FQcE7nEgmk4yZNJ7zLjmXl59+WQmEI/aK2v3VD7/4xp0sWHD7Is9VO/9pA0sphbjrLimljJ582S33trT3SJ+mK6HSQi65+gpyZq4fafIyRkUKJA6K4uIP6Ph9fg42NJCXn8+QQQPIWQ7BYJDn3lrKQ0++QigcRlV1IlEfmuYdHwYMHZ8qWLt5O77SKmJ7tyByfei+ALbrkMskOfmY45g0uobWWB9Hj6+mqCCf3r44LW3tNB1op72zi85YrD8kOORyFqZleQiTphOORCkbUElvPEn7vloSB/agBQL4wyFU3YfMmeSSKWwzSXlJAZedfQqzjj2K4oJ8jp4+hRkza1m46B0Ofv46bqwTRVGxbBdX8SBSN5fAscF1VEwpsaSC49gEAn6MQATVF6IvnaC9axV5TQUIBA6SdK4P00wSjURwTQWnn5Hh9AMnhqKRSqc5/5ILWbFkFR1NrWpne9J955MVN0kpnxJC7JdSKuIbAJBvNPDcu+5SWbDAvr9s2A279jXV6KrPTqeT2sXXXELlwAHE+uKoqvplk6n08GahaF4juZQsXrqOdWvWEQ4aTBwzlut+cBWWaxIpKiHk92NaNo5rYzsupoSs6dKXTHHmycfx3Kvv0qYGQDrk9DTlA8rpyGU5ZfY0hlYVMWpoBb29ce7746N8smwjLW0dSNvqP61SQPdq1n+pBl0HFBVfIIBuaCBN3FSGZKILUHBtm+LCfL573ZWMHTsKHZMR1SUk0ha7uluZdvQUKgZWc+99fyZ7cB9FxYUE/QY4FsI1PDSs/x07SCzH8bq3lMNIl8B1XLLpGNlUu9fIJ1Q0w4+mGwjdQGg+NFX7kn+hKAjhYls2kfx8Lrz8Yv44/x7hC+c7G3bUB3//5It3K0JcdMEFi5T/1A7u9+eOlDJ6+hU/ubm1u8/VVKlUD6nmtLPPJJHOoPSDFPQfJriuC65EoHg3KgSZYVPZvHkfva3d5M2sAikxNBWhKthSomo64GBZFq5rYygquxqaOOvkOXz62uN88sU6fH4feZEQi1dtZXVfLScdPZ6iwny27NjDJdfdTmNdB5GKMsaOHU88mSCTyWKaOWzHRgr133RmeiiZlC6gomhaP1ChgOHSm0rz7DPP84OrLuWy805BdT0GRX40zO59B3nwwb9QlF9AqqyEp575B8cfPZmxwwezq64Jw6d/eS3prTFFUXBcByuXwadJFOmgaiqKCONKx0vYHBdFZNEIYqsKigBNUT0jHwF/FOKJJLNPnsv777zD3u171D5Tcz/9Yt15juuOFULs+KZd/C9Wv+uuu1RAPvHi6zds3X+oTPUZrmlmlfMvOI9oQQGOZaEgcN1+no8U/V3/AtuxcYVESvBlYowoyWP0hAkQ8gMS05YIM4dwTRTXQlEVNM1AV1R01UOfdh5sYszYMfzhzh/y46sv4JV3P2bH9h2U5IXIL8jDcSS/+/OzNNY1MXTCaC67ZB4jR48gbZkkcxaWVHA9CmE/5On+08/hHd1PNnNdXMcGXIRtoykK3bEEf3niaXo6WtH9PnyagpQKjz/+d46eNJLXnv0zr/3jr/gCUeaedgnpvjilJflYOQshQFMFuqqgCRXTMlGkiaE5OGYWVVfRdJ+HjWs+dN1AUUAKQTwep6+7EyHUfg5Vf8+akAghETj4/QYXXHIB0nGEz+9zN+w4oN3/5Iu/1FSFCy5YJP7DHfyV3Rv4zlW33tTW2iM1TVNqhg5hziknkk5nPArmv+x6z855PpV4Vxwh4PKZIzln4iB6+uJkrDQIBSG8eChdG9My0YXXP6wpAqGqaIpC1hXsPdBI4bhhzPv+z6gqL+eGKy/m939+imhhIZu27WLZ+lqM0gomjBvD9q2bWb5uA5HSASAU7EwK1WfgSvlvcCRxZDdbWRM96EdVFLLxBGpAQ7iCUDRMT1eMNz/8gp/9+FqSqTSbN2+lp62Jh/7xJ4ZVF7Bj53ZiikZH0mXB/Y/zt0fuoqe7F4THVES6WLZDIBCgs6uLTMYkGo7gRyGZzOAIEEKiui66dPAH/YwbXMXUMceTy+VwpdtPp/ryKVRVIZ1KMeOYoxkzeQK7tu3SchlXLluz9XzLdn4phKj/512sfcPutV/9cPG52+qbBqi66pjpjHrKWacSKIwQjyU8FAhx5JxcuhLRf+R2x61XEwkIPl6ynPxohGg0SkFeHuVGOcl0BpmTZDIO6axDWX4eNVXlZE2TnJT9rsjbUdXlxRxqaWftlp1sXvo673y8hGgoSFBR2LW/kWQ8QfmgwbR0dLF+1QaOvuACJpx/IZmuXj5+6I/EDjWi+vR+N8w3umorl2PA6BHMufYaslqQDc+9yMH1y/EFgkgXhGGwZddeTDOHruvs2LmLSRPGU15aSkdfnPsfeobh37mEY86+gDcfvJ9MXx/50ShdfUksy8IBkpkU13/3HI4eP4yPP1/Btr0NOFISDYbIi4SIRkKUlxQyYnA1xYV5VJQVsre+FaU/i3Zc6XkYKVEVj8XhOA5qSOf075zOjo3bMAJ+d+2OA9pTiz64HvjpXM+G32hgsWDBAldKKa792d03NXckUIH8AWUce8IJJJNZFOGRtIR0UBA40sHn15Cayrsff0bVwIFcfN6Z6ELDcV0ONjXS1deLtHoxLZOAH35102UMrK6mYmAFm7btJZVKY1kS0/Y6pCSS4miUz1aspbi0hPy8fLbsrKc4Pw8BxPriIBVwJfv31RMdOpRf3/I98qM6z7aUMvHss1j80J9Qhe/wMvxXA0uJhcLl11/KZadN4E+7kxx19TW076vFSWa8QwYk3T1J7+BBqLS1tzOipprmvhwNe/fR6fo449hjkH0J0jmL+oZWissraOuJewco0it3dE0ye9pkZk+bTDyVIZFKMqC05BsQeEhkknzw7Os4lklOul6Pl+ug9n+XkMJr8U2lmDFzBtU1VbS0dCldnV1y5bqN35VS/lYI0ffVuviIgRcuXKhccMEFTtpKH71938Gjbct2XctUv33yXCoqy+mNJVA17TBHA0eRFORH2LtrD1vWbaMh3kr58OFs3b2X0rwiigvzQFMYUF3JiMEDGViYT9WQgVQXFtHa3cvu1jg5VxAMhQgHdGzXxe5PflRNp62ji7xoHi6Qzln4A37v7Ng0AUEqmSKbTFA+bBRxoZKLZ0hlBXkV1ahG4N8ycQUC17EJ5uejFpZjxtOIdJpAQSHRkjLaYnsIaEGEopDJWDiuxHYk8USCouI8/H6dxo5u1JJyhKHTmMhgaT66euKUV1Xj4iI0nWA4hE8Nks59ucgMTUE6Dol0lt5YH3UNTRxsakEXgu5Ygr31h/hszVYiQYPO3izfvtwkFI6S7EugeYwpEALbdsjPz2fuKSfw/BMvCM1nOFv3NZRtq2+YBzz1xRdfqIdPm44Y+NEdOwTAY08uumzPoU6pKYqrhMLKnJOOx7ZM7ySmP+A6ikrQp/DG86/QunYVdx09iBNHHUfjwWZaGw8Q27mdWCpLn+2wzrZZYtrYhoET8JPR/XQnMwzOy6PVyvHpOwFO//ZJRIuLsF2PIy2FIJPLoUrQNYGiqdjuV6mcXhu8lBJVwIedCuks+PM0ECoKsr9H8psjsONKgprK7ozOH/aYuDKAUCTSESju4erTW2ze5SSO7eL3BUEK+hJpFN2gpSdBWyKNqqlI6eAIieXYuK7kpp/9nt6uNhw0rt+3h0jYTzyVJZ7KYhg6ulDw+zQMn5/CSJCCojxOmjONG648l9HDhvL6B4v52x8f4KLvf4+asSO9DdZPgFNUjYxlc+yJc3lr4VuYps2euhb50SfLvqtr2lPHH//F1130V5Kr0PnX/Pys3lhSgKtMnDCRIcOGk83kvmTX4RD2+3jsT49R1byHBWGVsp0bsXaspSprMdh10PvPQIWh4QZUTMsllYkT73UxUekIhLB7etBMuLMhyeKiUi4571QUIVCkiyttgoZOPJOlrjWOdBUs1z3SetrffYzQwIz14NcUUj4DTVfJJftwcjaa4fMa/f6ZIIZE0VRyySRkM8jCAbjZNMJMYyZjqJqGFN4i0oXbL9xio+oGqmbgKmA5kEplae1KkbOyKI6LP+TDsRx0TSWVSrPovcX88a4fM/eoyTS2dRD2aUSiUYrzQ+RFIh7l9N98HMdm3pmnMG74EG6+9y8cd/6FTJ01k0RfAq2/jLKyOaoH1zBh4nhWLVuluki5YdueGaZlDRdC7DucbCkAi/pJ7TsPHDqpobO3WqiaI21HmTFrBprP8LorpMB2XULhIM/85UnGdR7kx0YGZe1KRLwPN5XBki4ZRZBAoc+R9CRNehM5UimTYMaiIhxGxYbGRnYcOESyt4sBUZVgUEdVvCxaFQpmzqKmupyeeJLtB3tw0Ekmk/1uTgMhcQHdF6Kr6RBNq1ZQFAyhx9Ps+ex9FCERivY13tCRHawIVF0jl0qx64N38Ms0gbCPxi8WE+/sQDV0L35Kiaap/YRz8AcCNLV1cqilA5/fwE6lMFM57K4ulFyS4oJ8Ulmvod62LKqrBzBo8BDa4znKqisZP2k840YMoby0jEAggGnmSKbTWI7jqQU5rodg9Us99fTFGT1yGM/84U6WvvoqOzdtIRoOY7u2R5fFRVGEx8wULrquO7sPNPvW7dh3EcAXX3yhHNnBj869SwB8tGTVtxpauqSmGtKX72PKzBlkbROhKliuTV5emM/e+5TIro1c7bfo3FzL8AFRso6Fhtey4qFJXo8xgYDXQRiLkS0upK65m63dfVi6B1H2IVEsE+n0lzSuRKgavYkEE0ePQHNNDjY2kV9Sxv5tG7GBwvwoaB7D30VFkZIvHnuEyuVLSHd20llfjx4Ikon3ofsMNN3/laRSYmWy2I7EF/Kz/aOP6G1oQA35adm2HU1Xj9THuC6hYNA7/bEdovkR9h5ooKWjl7LyQrCzJJvbOLRqJQNL86gaUM7OA+34dA0hBYbhRwoFM5ejrculpTPG4LJ8hg+oQACarmK5LvFEivxoBLWf4C77mRGhcIiDLa3UVFbw+D0/4Xs//z2Ft99JQUkBmBaKIjBzWcYfNZHCokLSibQ40NLFuvXbTtE17e7jjz/eBVCklGLp0gW2lNK3fceuU3t6Y8K1TGXY6BEMHFyFY+ZQkOgadLV3sOWDd7m10KB53WYGlEdxbBtVehClkBJXCIQLjq4hywrAzuIfWUN9V5zarm6WF1TwRmkVKSOEJiXCdbH6OzGQLpqq0hVPUFlexonHTuGFZ54jHAqQSNv0JZMMrqki4A/i2hKBjVAErpmjbtUKOur2oxo6LpLxx88hEM0j3dtNJt5HKp4iFeujZOw4Zpx7Po5lYQQMWnbtoHHdek8WQtGRqChCRbiS8tJCDF2jL20zqKqKhvpDdPYmKCgt4uyjR9Pw2Ts0r1/OxfPOxPAHsXImmqr3Q5QSy3RwkPh1A0M1aO6Os/1gI5Z0EQhC/gDBgI/2nm7v+fuJ5wA+VaU4P48DTS0MGTSQW6+6kJf++ld8Ph9Of++XZVmUl5czZvxYTNtVkskku/fUH2VaVk2/BoqiLFp0BM0aW9/QNthxHGmbaWXi1AnohoF0HBzpEg5H+OzDxZyTpxFq2I8/6COoShx0pOLVlo5QQLFxzCzmgGqczi60AcX0ZFxa23oYHA7jCwU5UDiIQz4fLjbS9ZgOh9k8riJwHMH+lnYemP8ToiLNY4/+lb60SUtzO+OGDWRAeR6m43oH8NJBKgJ/MITm8zJtK5dl4MhxXPzgY5z409uZfNElzLj8Ks69+17O+81vELqKa2ZBgu4PYIRCR3Q4AKSiIqXL+NFDUVSNWF+CsePGkk7FWbNmI/WHehk1dBBtW1cwfWwVt1x7KQca29A0HSklpmWBa6NqLg2t7XTGelAVh0zWpSNlsbexHSlVjyzu85EfjtDc0YPtgpCOhytISTgYRDNUOnp6OevkOYyvjLLso4+J5EVwXbefGaIx+ajJuLYthBTOjvomf28qMxvgC1CUHSVfCIAPl68+pqUzjVBUR/NrjBw3mpxlAR5jINbTQ3zvDk4PmnR39lFUFMV1QLVMlJwFuRzCyqH0mWQHDkO3LZREFkcN01C7G7/fR1gIvp3oYGxvM52ugoVEl/0yDdJzpNK1MVSVxp4+XEVl5dvP8ugvr8e1TdZv3UNpQT4XnX0CTne71/ko+j2H+6W+huH3897TT7Hkb48QCgQZPuNYBk2aQCaR4L277mHtolfRgyGk4yIdC9e2vd5q10VXBGYuQ0F+kG+fchyZnEUinaG4pJDTz/wWbyx8nbt+dS/33Psg1192Nu+/+AjxZIrevpR3ACM9SFQTKpFQmA1bdnL9rXdRf6iVkO7S2tRKd8rhUHvHkWb9oN9HNBSkqb0TFM1bbP2aFKWFBaQtE8e1ueXay9iyfAWZeBJN0xBCYFomI0aPwufX0VQh9zY2s3Lt5jkAX9x1F9rOxx6TiiLYt+/g3I54GiEExSXFVA6qJpezcG0IF4RZ+877TDLjFPU2oYZ11Jpy8AeRiQROOofjSnBARIKo+X6UHfvwDyin4VALqbRJ0B8g5UrKXYdr4830OiqO8JNSBcKVOP3eSXpfQ0Dzcai9l7hP54YrL+CLdVtZ+N7HnHnmCXzv4nNobe7k6RffgWAU1e/HUGwUVUfBIxSHVUn98iXsW/YZmm546namiWoYBMKeaAqKeuQlO66DbbtkYkl0meX3v7+D6kFV1DV1kkplcW2bs844lQkjhzP/13ez5PWnmHvMVHY0NBPrS6JrRv8hRj+ZTddxLZdRwwdTsiaf3z/wV9565iHyggFW79iLOaQKQ1epLC5CSofCSIh0JkNHby+lBQX9yZbApxnk+4MkkimqKyo5bvxQVn/2Caeefx6xrl6yWYviAVWUlJXQ1tKq9PakqD/UMEtKqQkhHGXRokWO47j+xuauoxKpJFKi1Az2OgTtXA7hN9jf0MTGd97jjNIgSU1FrSjGbevAqq/DjnUj3SxCcRF+FSUZQ9+0Fc00iWcztDe2EtT9aI5EQ5LAxXAlAeESR2C6Xs3pYcein9XgrV5NN+hIZemIJfjeBWfw8SfL2bp5G1kM7v31Lbz02AJOmjGG4ogPXEj1JEh09pLo6iXRE8NEwVH95ByB6Sq4egDLgWQsSbI3TrK3l0R3D8m+OI5lURRQOHXmOF596j7OOP14GtuT7G9sx+w33JDKElatWcfcmVOYfcxUvti6m87eFIqqeydqR3qhXRzp0tUbY+SQav72wG/42Y+uobG9mxFDaxg9qIINO+uoa4uRzOU8RoZjU1FSSDqXJefYR5JOKSXBYBDX8fQ1Lz7nTLav3UpfPI2jehTZcF6EwcMG47iKkjFzNLb2DAMGAvIw0DGwobmj0rVzEqEpw0eOwO83iCcSBPwGm5et4uyow9DW/cQTJm4ggB01kIrqlRXCRbUtpGMicwpuuBCpCJI9JsFQFM1Q6E3m6MiaZBQFTdExVEmJ41CASvrwTvoKgCeFB4kGND/1jW2cdNwMTpo9g58ueIRXn3mIhvYUp55+InPnTOeND5czZuhA6huaaGrtoLW9i1hfH4l0jqxp41omQvYzCJEYhkEoFCY/GmXggBIqSouxBRw3bSLl5aVkHUl7b4Z9jU209iZwpcK4EYP56LOVfLJ4GTs+fY6O7h6kq2L4dBwJKk4/jUX2N0GAgk5PTxxV0xg9agSmZZLO5Bg5sJyuZJK6Q20Uh4OMrilDqBoqDsXRPFLpNHokghACV4IiHKJhL6MfVOpSU6BRu3UnE6dPJh3P4DNURo4eyfIl60GxnIaWHh0YB9RrAF2xxJjWrm4N13JQUQfWVKEIQTjgJ8+v0t3cQEddI7synURzJnlhBWNYFOEL4UoFkRbItMBWwSz0Qb4fmVJoTXZwyDRpSti0m5LRhqDEkGRsm6yqoQkJmkDTdRQhcaSNi44qQXFdbKEgNAfLVejuSvOPx+9lxPGX8ovfPcIvbr2eTTsPovt8dGclQ0cOY+zECQhkPzznHWB4yYiXQEkBrrRxpIIQGiieqJkUgrc/XkleUTE9iSy9fQn2NXfQ0h1H9xkMH1TFymWrueeXd/Pi039g9MjhrN13iJAvgClzCOkBLy5ef5oQYPiCFBfn07j1EKlUiuLiQhAu3RE/iXiSgN+P5vOzpb6R6op83FQLgWgJ4WC0n8/TSaqzAVc6ZNO9aHmjSMog+2s30B3LkN1bx1HHTEU6Fo5jMnBINaoKLqo82NTMgYbWicA7GsDevfWjOmNxUDRpaArlVeWYmSx/f/hvTJg6icK8Yj6MVLEsXEk4FaMwlaZwf5aBWo4CYVOYMylwLfIsBV+ZRsSv0tmpsbolQTcumqITlBDCoVg6vGDkExcKZ9txQEEBD+RQlH5ARaIaGgoKSiqGu7mWlSUlzJx1FOvfe5LpJ3+X9uYWrvvhNYQKS2mNxVi9rY7y4igBXcOv6aArCMVz94pUEEKC6NeylB6LIufa5EwHIVTqW3tYU1uPaZl093mtQkMGVqO6Ds899QJvvvI6f3/8bi447zQ+XLIC/8EGQpMmo4YiWNLFtSyEUFGF8BoihIsmXPbsa2DZqnXc8dMbKSkuJJax6Ykn2V3fzJABFdiuSVcsQ3L/RiyjFCdQSSzWTVtLE+2tLfRkLJpbu4nnVpE1BAFfMfs6UgwIHCAgBKIgjNQUissr8Ps1zFxOtHZ0U9/aPuoI0NHa0zO6J5EGVRGhSISCigpaO3pYvWojfYkk+QE/J1xwHnFHxXQSpFMW2xubeXPHTuxEHz5sDOGSrzr4ukzyLEm1myES8NNYUE0vDse21OGXAseVxHWdbk0hkXOwpOMdtgsPxQLQNQ23fi9pNURvPElnX45uGeeZX/+Va86aze41r3PR93/CdVfdxGlnnUWoMI+W9i7UQIhsLoNAosgvZQkVIVBVEIoED5JBOjY5yyKT9VpZU6bFpr31jB0+hLLScpLxNB+9+xFvvL+YAflBNi99BSMQ4IIf3cP4kYOplCb5BxooKSkmnEwhBg4iK8BQRL+nEJi2S852iGcyJBJ9VA4opbKsiIKon6deeZ+3uj/nxBlTEKkET7y8gq54H8lMFsuxURUfquHHsdJUDqxhxKgayvILiJREEX5By4H9NOyrY/FHixkzeQJDR48mFAlhZrIinsyyt65uhJRSaFJK5YEnXhicTucQrqSwsIC8SJSOVDv+SIjW1i6CgyowDA0nFkOTgnR3J10drYQiQUQkjCslLoJO6SBx2W9ZiAN1lAcE26uriVhZMu1NqCKLcMURBqEjPb6SsFykA4Y0CQqXVMxk7/KNdFUP4kAyx87dB9i7cyfdyQxfLF/OzVeey8t/f5jVK9fzp8ef5eNP2llRtoZJ44dSUTmAvPwCgqEQoXAIzTAwVBVVKLhInMMyl66LIiW25WDmTFRbUrtlF4f27GXbtp00HDhIZWkpd//oUq68+EzeXbKBW3/7MF19Gdau3cjw0cOYMl4yOm4SaD7AAF8+BRURr83VlshcjqwL2azlNQQpBhKJJl1UCaaU1PekiH+2nLNOnE53OoWtBYgWhvoPTADHQVUCHDVtEk1NzaiGTtAKEwqHsS2bLz7+mJqifPZurWX4xAkUlZbQ2dmD5cDe3fsq1374cEQD2L+vody2JdJyRFFhAQHDQFMEqitpPNDEzJlH4aoKvT0x2hqaaG9r6yeQKUis/rgn+tmCoLk2A6RNuxHGSvRSUJRPQlOQtqdmU2VnyXfB1/+iXcfBkml6PtnCoeJiOopK6Bo8gtrd++nqidHZ0UU6a+E6Jo4rueeRF9i8YxeP/fZnLH/nefY1NPDAU2/ytxfeRPb2QihCqDAfVdcIRyMEAwHvXqXEdS1kv/Sw6Tok0zlsB1LJNCRToDhcfN4pPPmn25k1YQwHE1muuvsRXnvmNdSiUvKjAbKOd1a8eft+DkXbGTdpOGayl56tB4h2dGJZSUzXi401VWXUHSw5UrciBa5UkLkshpUh7kIslqA0HOJATwpF1ZCui6JALp1ixsyZWLZNOplCim5c1yUYDKEYYfbvO8CZ37+YrlUbEYpBcXEp0t4uHF1Sf6Cx4Kk9jUVq6uDOsr19zq317bGga+UYd9REcfRxx6L7dNqbm9m+bAVVkRCbt+9iV/1BMmYO3edD0TWEooAiPHgSxWvxUgSKaTOyr4MDoTBtUsNO5Sjq7WI4FqqqU+Y4TFYFUijss0GMmUBVWTkNluBgTrB4yQpee/MD2ls7GD5iFHNPPY36g/sZWZLHw3fdzIJbr+Lbp51EcV6UNz5azO2/+Ss7d9ZRXhJh4qzpFJQUkcnkmHH0DJoaD5FKpkinUmSzWbK5LNlMmmw6TTqZoLp6ENmszYjhNRx19CQMw0dLczur12wgmpfP6OE1HDttIqfMPYp0qo9Nm3czacoEzr3kYnpjMVZv2synH6+hvasLywiSiIbo7ozTmzCZPWMShUVRjpk5jUjAh+b3UVQQobWji9279vOjqy7hky9WUlFSSNAfoKEnSSjsA8XAcW0GDx1C1aAatm3ZRqwnRndnN80HD9LReIiKvDCbN26hND9CVzzHhNmzqN24mX21O4XmC8h8QyiNB3a8qe1v7wp1Si0kECBUCotKUYQgGPCTX1DELceOYW5zLZ81NNMaiNCih4jpfrK+AKY/hOILoBoKriZQFYmuBLATHahmjkzaQrezpKIBb+e4Noquoxg+ujExXfBpPlozGRo7u/l81Vp2b6slkcyRV1REND+PN9/7iIa2Ln578/c46ahR/OONj3jwsedJmDlChsb6rXu59vLzuejbJ+KP5rG/K8We/Y3cc/d95NJJZhw1mdKSUtra28hks7h46FfQ78exTZLpJLt2bOfKS87mmGOmEuuJk8hkWL96FZfddCfnnDKbvLDBsTOn8/KfF/DS3E95fc1mPvnwY7asXM6U8WPo9Qfobm5mj5VDmzSKukMN2Oj4DB/xeBxF1dGDQSzTxnEkpmkRjAYZPqgSxbFJZnMcPX4sH6zeTDYVRCgQ0kCkkrRs38yIggAlQysozI9SWVrIwMpSJowZwYtvfMrvH32Wm359O3l+KCkqBEVHFQq9yZSa6uvL05q6ElqfFu5n3KuEAn4ieQqffbCaxLLF/HHWKLo/P8jsgcXkbIdON0W7FaclCW1JhV4E7WqIHlXHliopCVMVk5n5AbpkjrijUXOghXGuxFQEKeGQzmTQBWQkRI083l63hXVrNmJaLhWlZTiiB0NXmD5pNMlYimNHlHH6rAnMOu0amrtjnHf+mdhqgN3btqIYBjdceQ7DB1ZT3x6n1LSIlxVw0cXzWPTKq6SzOYaNHEl1dRXhcBQFyJom7V0xDu4/SDLRxenfOplBQ6pobmnDUFUCPp2f/PAaGuoP8MHKLcycPpnFDz7P48+9xkuP3sP5Z59Ae1cvAfUGFClJmw6m7ZDNpCmKBtl/sIl7H19IJpujpb2LkcOHk0imCIdDHgdaCBzHpSuRJBLwsXLlForCYf506/cozo8S8BuUFhdSGA1RXFhMMOQ/0thjOxaa6rXn3nTleazfuYtINA9dg4K8fOhP8hLxDOmkrWt1dQeH6QPCfghI3Jzw+3wk0hkWL3yd+0bX0L10OT7TIacDqqASlaqgwjQpkbZLxmeQKypA6ejBTidIqD62SY3GlEO7aXO+28mJOtShoFg6ri2ICAVbtUhZKrucFFKLUllZguO6FBVESWazGJpCWVGUo0ZUcsvV53P+1T8np2lsWfEm7R09PPLCIk4/61T2H2jk2Vc/ZsGtV+M6NoPKyokGIowaMpCzzjiedWs3UFu7lz31DSRTcXAluuGnIL+AE084mmlHTWDAgHKEAJ/Ph2tZ6IogHovhC0YQvhB55RVMK67g5RcWMeGESzlh1gR6+5KkMlkyGdOTMMbrl0pmcjz9wB3cf+e11Hem8IfDuAIc08V1vJaD/GiEooJCXlj4LsfPOZb2rjipjMn++oOoo0Zw9qlzv3aGnTNN4qkUQb+Bqui0d3dQkBcl6PNz57WXctM9f2PU1LGoAQMcBwXcXC6nptPZMVo8FceftVACEbBtCsuLWLlsK9OcHCMaDtLR106kIA8hXCwHTFdFuN5KyoSjCC2I3tyGo0KtkcdTaZ2eRB+T4imuKAgwISLY7Pj52NWZ5/RhqAodDuzKwkYRIBzwM1xL0mUGCPl8TJk0hvbOLhRFxXZg+vRxtDS3s2T1Zu6+5w6eeOlNOroThKJF1NbuJhgKsqV2N0IRaJqLtHMMqixmw6YtfPL+p8QzMGPGeO66/Qf0JS1smcOvGwQNg4DvS9VaVddxXJegT2fL1l1cdP0vKCoq5KhhA4kdbCQSDfDDa87FMHQQCtFoPuFIiIJomEjIhy0dSkqLeOL5t7nj7od5+a+/49HH/syUaUdRUlxGJOxHCBtdUxBWlrmzZyEEVFYW094To7Ozm/179nL/w0+yfd8+fnXDVeSFQ0jXwadp5EUidPf0EAoHKS8uIpVOgc/P6JHDmD1+GJ9/tIL8gmKwLYQCpmN62LuEEVq08hKMMEIRoqKyjLXvfMiFRRqi/RCaLWjKOrQ4kpCQRFwH/Cq56gpUV8Np76TbsvlHQuPPGY38TI4Z2RyjBxRSGRGkY0kOSUG7VBiruLQ6gs81g2wkjxPCBqepkr2Wyx4JVaUlTBg7jP0HDpHKuqh2kpuvvZh/vP4xze29jJowgaVrt3L8SScyevQw+uIptu7YjSpcLjnnW2Qtm0g4yIL7/swv73qQoQNrWL58HTt27uJn119MaUmUwkiQiE/H51NBUVA1DVVVcGzbK50QXHb9Lzh2yjieemg+R8+YwDEzJnDU5LHU1FRTWVVJaWU5kbwwuqFjOw4Z0yGdc2jtTLJy1QZqqsuYNmUUKCrdbd18sngpZRVl1AyqYmh1KYl4iudeXsSIUTWomkFnbx+ZXIpjZ0zljFNPpbu9m4xrI3wa0XDA0/xQVCKhEIl0klhfkn2NrcRSadLpNEWFEV55/V3KygeweuVqVE2XmGnF7Gn5XAMfnoqiwFYNWtev59TSAItSkjGzLyIchsb9zeRZNpNCNiU9zRS0tVDS3E2vK2hUfOzuSZPLZPmuYfGt6jDaqEksq2tjy+5DTAxBJJdBsVSeD/oJR8PMCPoYY/Wh5NLEszmkCKG6EkU4+AX09aZoj3Xw8z/8iLbOXv705ItUlg2grbOTcFCnvbERmUvT09tDJBykL5GmL5WhoDDCnx99gVdffZuP3nqaWCrDu0sW8/Qf7mXXgVZ+evtv+NnN1zNm3HBS6TSG0BGqi+MKfD6DSDTCO+8tJtaX5vLvXcyDL77FhnW1fOv045FODk14/VBSKB5nCE/jWkpJPJkimUyzeesmfvvcg6xeu5VBQ0fw/csvYNuWrTz5j49ZumwNZ540k96ePoaOHodrhJHoXvOB66ArGloQjj9xFlnTob0zQSyWYuygKmKxbho7ukkmsjR195IX9LNrVz09liASDJBvBHj2scfRgxGvxbY/ZmuGGsQwNDISSv0+fjllCImGfWwvGsyb69bj2A5KwE8oEKJ76GgyWpTqUDWTatejxZK0x23CuJw1IMTIwWWow0ewaU0tfdsbMKIG78VT7K2ooTA/zJRMnHGY5Md7sbMaPU4av+pDqD4SWZtsxmLJ4mUEi0K8+6f7GVxTybCjL+QPv/oJW3bvYfFnq5k5+2g27toJtRAO+Dzl16wga0vURJK/v/Aad9/1E5SQnx9cdyeBgRN4+vPNbHhnPslUjh/f+yjjZs8ml7ORQsFRveb9sLT43XXn8+Kr73L8nJm4rqC7q5sBlcUE/Qa2Lb1Oj35+kxAuQmjYlqSzsxfD52PV6vVMnzCCkSMGc8ef/s6PfnAVn62tZc6kMbz6l+k8/MyrrN6wlWTaJJFK0NbVxeBhgzi4dRtXXHAWo6uLcFxJbypHc0cvrtTRDY3PVm+idnc9puKjpzeGRHCsSLG7K0YDAVwzwfCBFcyaOoY77n/GEzHXVDRNk1okoirRSIhYLMPoudNJKyZtdQ0EpwwmPxwknsjiU8BJ9PLJW+8wYOAgSkfX0JnKonXFqcgPMmnSEIprKogPGMiqN5eg1x1iytAyliQdmo8+keuvuJC6zetIvPQSmb4klgBNekw+N2CQbDNxFR+HWjtoNXTuuecnnHHMVIbOOIvvnHksP73uEjbuPsQF193O6hWrGTG8BkP30dfTw8H6BkaNGQ4COjp60Ywgo4YP41BLN7ZjEaqsYsOOOsaMHsHMo2fy8LtfIE48F8tysV2LkGWhBv0c3L6N2x94goMdXVxz3eUcampm0sSxaLpCJpPzqJ7SPTLFRVVVzJxFLOZRdVKpFFs3b+S9Zx7gyZfeYf2m3VQXBMkrLWHJpp1MHzOU00+Zw9AhdWzaVkdlZRk7arez+N19PHDHJeR66jnQEaG6KERZJEhhKMDuQ22kHBtLUYjnLHZs34pQJLFEkuKJE+ixJVLamLakob6RO3/0XRa+s5i1tYcojAZw/JqmDKzIawiGgjlFCEEsIev0cjqFgSJUXEXHslysnMSSOkWl1ZQPriIvmSbQ2U1NsZ+Jk4aQHlDGlmghn3+0gnBdI9mqgfy1x6UnI5gcCbPps89ofWkh4d6E18QXMLAL83g7bvHLHgXnqOP53vcu47zvXsiC++7i6hNnceENP8fMZXjyD3ewr6WbksIQf7n/DgYURajfvpMVS5azdMV64n1JSktL6E1kCIdCRIJ+2jv7KMwv4s7bf4h2YBuFPU389LYfcKjxIEKROIqCIiUGOX4xLcKPJkQpKinhg/eWMmnkCPIjQbp7+3Bsh0wqh+eM+xsKXYF0FeKJLJ3dcXKmjc+vs7W2lumTRnPUtIks27Kfo6ZN42e/e4wXX3mLiuI8mnpi7K47SGlRCX3JDPv2N+APBZhzzATuefwDFq1pxXKgpTMBCHRFYdjAMsK6iqLpuI5FMBSioKiYcChA3Mgj6/eDk8YxbYSm09XSxuRRg5GOLSIBH5FIaJ9WXJGfSOmKVFSJaDlIc9cBwgkTkcwhpQ1OzutVxkXV8wgZfiIHm6gI+wmMH8XGKccSnDiWpo/fRmzciVlWxIL2LPsi5QxPdVH14fv4TJOBmSyZvCixolL2x+N8lMghjz2RsdOmUVYWZFjNMMrKipgzKMpr733O2+8sZsuyt/CFwqimgoNKRovwsztup3btOu76/SMUl5bQYXdw1JSJmEIlGA7T0dNFc0c7RQMqqKqu4OEHF6CpGvG+Ppav2UTRuJm4UuDYJqoS4LW9SVwjh9OXQBcuJxw/i/pDzdhCIKR7ZOYSuP0NcQrpdI54PImmavj9ARShs37zLq698AyKIxG+/6NrqW/uoq2lg+UrV/P5yge49MKzGVAYZujAEOef92321R1k9bKV7N7XyZufrCISMojkFTBn1hQ27t5DXsBg2KBKxlVV0BtL4UiBdMGxXRTVRzzejZnKoojDQsgOBw+1MWroQMAWkXAIJxzo0wqFawfz803b6vJPVmGM4rAWA9eQVOQVk0uZ3vo1UyjCRXVdIr1NyHCU10vHoucVIZaup2rFZoxwiL8kDJqjBkPybeIihNPdS1RRyJ58Is7gwbz89sekBo5mzCnfoqx8ANFIgCHDBlAQjlLsc1FSSX72+8eYcdxs/vHmh6xctwVUDUPTkT4/kyZP5K03P+SMM06hr6sDFejt7uLe+/5Mb1sbRQVhBg2qoaW9m1AwRDLRR188zisvvUOHG2XOGeeRMU0UoWApLpviOoV5QdrWruCoiWMYWDOALbV7UX0+pNuv++w4KAIcxyWdM1m+ZhO7duxAlZLSsgrQdYLBCK+99RGXnH08M8eMJqf5wadxXMEZtB9q5sX3luDLZTh64nAmTRpPcXEpFZUlLF7xEQOrC5Fmmtff/oDRQytJrHmNwuO+w7adBxgzpJoRlWW4ltc/rUkXQ1fBsVGFi4pAcVyCgSBra3ezbdde/LpCMGC4WU011e37m9Nrt2y7au2OusI5AUte4rNEo6qy2fTR0NmLKnSQkMmkKSwuRLcsIrt30mSZ1PWmyWzegr1rJ1bKpiMQYnXGombcOKZPncCO2v1odpbBZ55AezTCs4tXEpo8jePPu4Di4giDqksZPmoohj9ALp1kTk0hDz76LG8uWkxRURG6ajN6xFAqSwvRBPT1dLBs6WoaW3ooKMgjk8mxu3YX9fUHCPh8bKvdzcO/+wVbdh8kmbF45/1PePbZRXz42Sq6A4Ucd/0PcUtLsc0siu0ic6bXidLRQe3Lf+cHl5+HUFX6EllUcXikgNeuk8s5ZDMmrR2dLF+5jjtuuZarLzmTvfv3YWXThHRJLJnhQH0dl807g7aMQ1lFKT7VBl1l8MiRpEyXNWs2kurtI+LXOdTSxvrNuxg7pIopE0ezdOUGZsyaSbmWprxoAAUjJ7Bv5y5UTaEva7N9114Cfh3HlhTk52PlcjguKIpKvLeTktJSRo0ZKZev3SxGVZeYA0O5ezXAHTy0piuo+ofut3W2xfuolGnKhM2WzhhZoeL3hVBSDs3du5hQEkYoMFbzMSvTR8SnEQxpSDNDJuvwtm4w+7gZlOUXoMiPCaoqH3y2kp1aARPPPp/ho4dQWOhn5LAhhMIBspZDMpdl/IA8Nm+q5e77/8qtP72KO265isJg6OsKcUA8neGhx57j7geeQw/5OebosUSDfppa2qgeWMmStTsZN2E8O7Zt5aMv1lIz91RKywZROnQYMVti7Tvg0V5MiWs66H6Nus/fZGBhlIE1g9hZV3eEa+RKgUQhm82RTCbRdJW9+/aT6I1T19bDiHGjyQ/n8Ztf3swbb73P8y9/wNZd+2msP0BZfiUtCZMhA6spLS1hX30DY6ZOYMDAajYuWUb96x9SU12BqitUVJZw7lkn8cXKzXz6wSeMHz2cvveXEY5uwjah/uAh2nuT6IpGrDeJbbn4fX2YORufrhING4yYMJ2Tjz+OPXX7ySSTjBheE6/Oar2aEMJdumbzgWBxZMZ+My13BfOYlezi+x31zBlQSb0Isl/Rac/q9NkhBrom/q4eziopJKDGydkKKVRaE70c1PJwo3kYQYMdBxtwI3msSylUTpnCCbNmUVAUYdDAKmoGVOBik7FsFAWCqmRExMcljz7HpGlHceutNxFP95HM9OFTFQK6gmXaZBWN+x79O8+88BZVVSU8+adfcMKs6eiqwnuLV3DFzb/l3Xc/5JOl6+iK9VEyYCBl+SFibfU0HNjlxVRVRzU0TyJBuLiqTuOWjfzgirPpjifJmS4+/TDCpZDJpkmnM/j9ftZv3EZjYxPfPuM4ln3+KW8sfBXLsiitGcymTfs4fs40amv3sHd3HVNOHUxrwsQybXRdYeyokTR1dLJPCGZ++ww6DjWwf9tWspYgZ7nEYr0UFhczZtx4jj9uMg1NbdiZJK6iEfTpGKFOOrvilJeVUlGWT1FBlAGVFYwYNpiH//YMsUSSwuIi9n2+Skp0MWHs6JZLz/95jwYwcFDpnpqKCtav3yrdojy2Zf1EU70M6OhhgqIxI+BHCWoIf4hQJku4pIBe12Zpn0o66qPGtXGERi8KCeEQ7+7BzJrgCzDnisspLM6jqCDKoMEDvXkMttXf0+zpZYR9GumsydYde7n+hqvAdYgnTIRymGjuUhgN8OmS1Sxeuom8ojLu+vElHHfsTA60tDOgrJg9+w9w7OzpPPfgr3j46UX84dHn8RuCn31rGlVl+aSyXo+3KxyP5IZKRWGIZ9/6mIa1AaZPncie/QdRFRXHkbgScpk0luXRVRVVobWjhxGjx/Dd719B1B/kD3ffw6iRg0l2NrFu1SpufuoB6g4209sXx696A0Zc1Zv6gsxRWVJASUEeBxtaCPlURo4ewUdvf8jeuoN09oylN95LXVMzp0TnUDFIoAsD6ebIz4vStSxJc3sbP77p+wyoKKCts4fOjm7e/WwlPRmXcF6AW355H0tWbpbV1QOZMH7cfiGE11VZU165q6I4DyEdtveZ+GYew+BJo9l9qJV4Ux2BrjYifTGK+hKUiRwjXIET0MlUl1KgSPoO1CEUjXpsXKmyZechujt6qaiuZPDAMiorK6ioLMN0TG8QVj9TUUjpZYfSJZMzsS0X03HpjicJBPrH6LgS2zYRhsGGTbVEggE0YTH72BkeG8BxyLpQ39jO2tWbePaDNVQOrGbqhJH0JnKs2LyNn914Bb29cRTF61I83IDtCxi89d5STj5hFqaZxbQyqLof25Fk0lksx0GoAkdKFMckFA6yds1GEn1xRg4fQtyC7155NfmRAJ8v30xOCrp6E+Tn5ZG0ZL8KkUTp15ySrouhCsaOHEJlaTFNzU0MHT2UJbt2sWFbPZmsQ2tLB72dfYR8Gn5DQdcCWJkMpeUljJ0yjj898RxNze30xuPYio4SDFFSWk77rlq2r1kLAnnUxBEMGlC256v84J0VhSFXuqiq5qD6YPSIUcQGDkKqM7GyDul0knQqw7aubpbs24evL4Ew02R6ulEClVho7Ij1YiWT7KjdjhoMc831VzJm/BhUTXjaWv1nsUcmg/VzedM5m1BZkLKyQmpr9zN58kS6e3spLyhE01WktMnZgqTtsv9QEyOGDaa5M01pcZS8SIjGxhaWrtnMOafMZtGLz7J1615qhgyhJ5FE6BrpVBrHtvsbE8BybPLyIrz74VIONDTxg2uvpO5QG0LRsUyLbNYTCRWKiiNdHAmWJTlUf4DzLvg2pflRXnlhIWg+7v7Tw7S1dFBSWUFrR4zu5kYmTR7Hob4sqJ6YmSvUIzMQJZDN5YhEQ4yOjCQvP5+62p28v3gplgmLl6zkUEsrxcWFngSx8HIPB5WCghJC+SFKx01gWEER/nAYQ9GIhoIsX/wp+4IhzHSS6qpiouHwtq8a+MDgqopOYRhliuvKtgMNIhj2YyJxU0kMmSPoM3ADQSgvhXGjyNou0pQI18GRNo50GdDYSLyzg1DQzylnn8qwUSPJZrO4lqeE56F8XxkLKyUqgqwjyEnBzVdfzBU/+R0nnXI8wbwotYc6cB0bISVBXxfVNUPpjb1DPGnS1Jukoa2TCcNr+OTzNaQswa0//zG5VIIzLrqBnGVzzHEzmTppPL2xZL9us0DBU8OLJZI8/dyrHH30VHKOTTyeQdPUIxPShOZhzAKPs6wKwfChNSz+8BNuuOEGxk+ewLyzvkXtjp18/vFKfvf7O/nDXb/n8vNPo6iyjA11Pfh8fq90dr+q3StAUbAcF+m4VAwo5yd3/YwP336f+n0HieYVUTqwAk31hnYpmo6uGfj8PnSf4bl928Zx3SM6Wj6/j8YDB71GeWGow6oGuIBn4Pnz5yuaqiRf/3DploK84KnxeNrp7urWiqJhxoweQSaTxjJzpFMZEokMfckcmXQSVTi4hts/e1tFN1SGDp5OVXkpZeVlWJZNNpnx2H+4OK7HPFS+HPzH4eGwiq6xrSXGOed+i89WreO679/EVdddzdHTJxLJL/EkllyXIdXl3Pqja3nssafZs3cfoyeMY/OBdl57/wsyySzX3PxL8qJ5mLbD7Xfegi8YoKM3TVdfgzfxW1UQrovf72N/3SF2HWzjFxedQ3NLB7brYOcsUBSk0q+e8ZUFaVsWkyaPx5Euv/rVbyitKKO0soqN6zdQVVPDb+95gDFDSvnNL29hXUsMTdE9So7XM9rf1C+OCNZICY5QMDMWqs/PRVdeSrwvQWtLO509vVhZs9/LgehXxlc1QTgYJhoOEgn58Qf8GMEg2USavo52hGu5/oCmDKmqaADqAbS5d92lLFiwwJ04ZujSmsriUzf3NZGMZ2jYu4dRo6uBAMFwhIKiL4dESseTKrAc25v2JSVBQ8cwvJbUnG2jCR0j5HUSOuLrQxuFOCz/5NHFfEKSsiUbe7L87U/zmTVlAn966hU++HAxZaUl5EejSAUy2TTxvjQZC37/2z9wwYXnk1eYT1d7K3997D5Wr1zDHx9+nF//6g6K8yPEEn3ohu8IJROp4GCh+ww+/WQZk6aMIT8U5VBDK6j98lDSRdrya4o8ol9VIOBXGTFyCNt37qWnu4fnn3oeIxSkqjTC9eedyi9+cT174jY5QuRF9COKd477Ffmmr0hPHR52KSRIy6YwL0pJQT6m45DN2ViOREV6MxM13Wv97Rc5EACOJBQSrNm2g67OGAjhDizNU0YNq14jhMgtXLhQFVJKtV++4djLbl6w/KU3Pnd94aBSXllG1aCB5EwLtz8pEf1jZ1S82OBKsMzDgxcVhBQey69fh09+Za/+q7K7J+7lyal5YILt2EQ1wcCyQuKJFJt31tFwqA0c23sLjgW2Q82gKmbOmMyq9Rto7YgRzc/nlBOPo+ngQTZu28nYceOxHBPZ7zmE2/9GpECqCsJxaNhfz403XuVpbiQTnuz+YYmII6N8ZL9upMS1LWzLYtnKTRw81ITfr6I4knDAz9DB1QwaVElnLE6PBaqmHhGKk4fHavZPeEMIFFWCqyBRUFQP4xZIkDaKquAg+4dOex7nS62QL9XkDy88v6HT3tLKoYZGcqmMffpJM7X3n/79D4QQj0spNe0rEWLL2OGDOoRCqaIIt7GhSWnYW+/p8R2mMx5ePqYJQsXn9zGwuopdjc04mZwneKod3jHOv5ke8E/GFv8iMA+2BE3F8PsIBvyei3QsiiJh7vzxNYwfP4Z4OsWEKWNYt2EXb7//Cf/4x5ugKuh+P+vWb/ryfiVf0fbot59lUlpaAkKnq7cL27ZxLIdsLkc6lyWZypBKpkgmvbacXDbnyRZnLUzbQTEMTMdFQaErlaN1005WrN4CitY/zs49ogD45WS1/v9wMpB0IBwAXf/SuwgJjgLJJBgK+EMgnS//nq8/B19djLpOIOiXOI46aVSNAyw/zIPThBBy3sKFqhAi+eGydZ9WVJRe0t4VcwM+Q5F+35eDM/qZf450qBhSQ8PBRo6dPZ1rr/kuf338Gdav3U5xeQGtre3oegApna8KU30p73CEb/71KdpC8RgIUnr1o7dqvVWqqCpmoo8f3nI9xZXlfLZuG9MmjGTssCEseu1DhGoQyYvgmBaOdAjmRb723Yr0EhYvaZKooRDJTJpnnnsZRVVJZ7NYloPt2NiO4/GZDg/JEZ5mhqooqKqGoXmkc6/UE54WVsCHEvIWolQE0pV4j+/dv9sfhW3T4txvn8m4EYN44rnXaO2KYfh9IF1s6eKTkiuuPQ/Xgadfff+I8M1XHPs3TkXHlTimI0MFecqkUUNrgZ2H3aQGcENJiVgEzJk17eNJI2su/aB5lRA+TxfyiEtQIJvNMnrUYH584zU0HGwgHAlxqLGBb58+l5NOOI5QOMSjDz/FwaYWfD4/dn8zvJBOP71SIPkG9TmhkE2nIGchdB2f3wcoOFJiJeLggBExKCot5bZfP0jjgQZu/MF3+fYpc1E0gRNPkjBtNL+BIiDdm/zS6yheDaoafny6iu3YpJMWuC7pvrav/x6eUq3uN7DS6f5d6Hkj57ACgN+H4jq4pgmqjj/kw5WgSoVsNo3MmYCCL+j3RgIJbwiXrkgs2+bzpSs4btZEfnPHjfzw9nvJ2Ta6omCl0txx2zVMnTCWny54kGw2RzAUwLa9IdPfPMZE9o+vk1hZ050wcpgyfdqkz4QQcsmSJZoQwta8eUhzHYCAyodjxwzs/mDp+iKJ6glHiS9XkCMFjU1txOI9RPJDrF27hZbWHgbXVDJj+gRiPd00Nx9CoiGFVxZZpoXrguE3PFfluv/icRzTZsygCoZXV3CwpZWd9S1INURAdTnxuMkEgj52Hmjijw8/g1+FwuJiVq7ZxJw5s+ntjXPssRMpKshj5eZdZLM5Tjp+MqrQsR3vmDOTzbJ7/wE6uvoIFRYwc8JwAn4/uM6Xsdax0TWdzt4UB1rbmXH0aHDBtjwWhAR0Q2PfwWZUw8eYoQNIJDJ8sXE3iqZiZjPUVJQxfugALNdl2fptWC5omo9sOsXs42eia/DRG++wpXY3F5xzKlVVFeyua0PxgT8UZOSY0WzasY9d2/cy7ZjpDBxYwRtvfkwgFPLyoH83OkdRcG1HmTxuuDOovOQVgM7Ozi+V7oQQcs78+ZoQouutTz7/8LnSpZd298QdXUOT0ptGZjtQXJDP9IkjwHZZunwdny1ZjWlLtm3fjWtZTJk0hmNmz2BjbR2xeAaJw6DKCgKGxr4DjbgoGJpypO3FK+NUMvFevn/R5dxyzaW88PqHXHHj3aBneej+27nqonPYvGM3Z1x6G8FQgF/dehW/efApzjnnTF78x1s0H2xk8Yt/pKaqiuMv/AH76lt4/9kH4J/khBtb27jrwWd4+c1PePeZ+4j800HG4c+SVZv5+W8f4L2n//SNf//np1/mr8+9xat/uRtd0/jhnffxl6ffoqgsymuPL2DK2JE8+vybfPzFRnzhMC4e5Nra1MitP7mOU4+fRVlFKalkmoFlBezath9TGFSXFyCdHGNGD+OPD/2S0sJinn/5ra8lfv/uY0nXCUR96tETR24O+H0bAOXwHIcjSndf3HWXFAsWcPbJx7/0txfevuzDJZsVfzSA7YKiaGTTKaYeM4Xrr7ucXXv3c+hQCznLJByJkoznaGppZ8aMCVzz/e/S9cenCXTFsHJJTjvlGEaOGMo99z5MYXERrW1d5CwbVcgvs2tFYPXL/QpApmPcd9/Pueqic9i2Zz9nf+9WWg8e4vXX/sr22h3U7zvAyy+/ze59+wkHQuQylicVbHuy/KlsDp+h8/wr79De3cfkiWP41pyjefoPv6B+/15+95dnGVgxgJLCEOecdjxZ0+b5197HsR021O7C6Z+8lkoneemtj7EdiRQqPlVl5Za97Nm6nxvv+D1P/OFO7v7ZD/jgk2Vccek5TBk7ktUbtvCz3z6KFgzj9nsHw2+wp66RF196jWnTJ9HRG6OkuIgzzzyFtrZuDja2cM2Vl9OTyJHo6yBr5li/cRcrVm/GFwx+TT3gn4dvCUWQS6XktCnjOfW4o1/N5kyWLFmiHJZR0r6S4x6elvzZjEkjd3+yqnaUg+KCq0jpYhhBtu2o45nnFjFz1lQGVJaza/cBkokkus9HZVUFlu3y9DMLaWlu4brvX0x5ebF3giPg9ttuwB/w8dcnnmPX3kOofn9/MtU/TFL1ivm+WC9XX3MeP/vB5RxqaeOiH9xJY5eJiERJJ+L86OqLOXionWf+8RGhsmIUxzwCBBzWfhbCg1B+88izNOxqhqDg8zf/xpzpU5k5Ywa/u/dvoKpMmz6G8844mXQqxe33PEa8JwHSZeasKSiKIJlIctMd9+MkXVBtcFVEpIDAwAE8+Y/3mHvMVC45+zTeefHPDKwqI51K8YM7HyBtSYIhj+fsJWIOqs9gycqtLF2+CU2TfOv0kxk/aTzXfO9CPvnwM+oaW5EtPby58DXiySxSSAy//2uC3vKfdrE3bVWV0pHqjHFDE5XlpS94Imhzv0FtVgg53wvM1rqtux5f9Mmah3bua5R+vx/XdVGFSlt7Nx99+jkTJ4/lmGMm4TN02jq6Ka8sZdK4kcR64ixdvpFcKklPdycDBhTz2efr6OntZfYxM3Bsh+62riP19JHMUMojicSpc4/mkvNPx7Ytbrz9bnbVHiSvqoK+jiS27ZIfjTJ25GCEclg27MtSSyieoQ/rLJeXldLRlaSmspCyojwURZBIpFDy/Cj+MKFIpD9MSAoL8sgIH46VRfRTQ4qLClj30Qs4Zg5NVensS/G9n9xNdyyHESrg1vkPM2XMCMaOHIaULtf/9F627jhEqCDiyTwdzuJdgRAOgYDfqzpxee/9zzxAZco4jjnhOD7+4GNWba7H0QT+kN+roaXzbz3zYWNbOcspKSvXZh814XUhRMu8eQvVBQuE88160XPnOgtATJsw6oWZE0betWPPoTxFePJmrmIjkAysrqKoMJ+eWIxZM6fg2GDZJpmMSSQvTOWAAs44dR7jxo5k6+Za3nh3MU7GQrpwzlmnctttP+SpZ19mb0Mrft2bFwQuWj/5e9jQIUcMf+G5p/HRF9uwHAuQ2LaFlJJ01kToElWR4DpHdKGlkF4yLBU0ReXtv99PJpkhEo1QVBClrqmN9xZ/geoPYZkWjmsdcXeO6+DaJq7j0l/jeDMGqwegCAdNNSjsi6EqBq5Mo2gGiUQv6VSmv1KRCE31To0UUG2vT8orlVRvPoP0xuylk2kqSgrp7Whn+1bB9OkTOec7Z9IZW0Ttrv0QCqIIHRSJ+MqIZyHcr+1iVVHIpZLqjJkTmfftk/4CiHnzYNGifyPpL4SQ8+fPV4UQPSfNnv5cRVm+yJm2czjI6z6Dg02tPP7E89z/x7+zZNkaKiuLWPzZCu5/6En+8tcXaWqN0dbRQyaTQggI9A/IMHQNobg0tTWTylr9p0reC0CoR5KuWF+SPzz2DLFEgsvOOZMfXzePdE8MISCRzAIwuLoCN5Mh0d2DrgnyCvKQ0D9PQkPBBQnJVJZw1E9RQZTG5lZOu+h6DnYkMQwvg/6mHUF/HQ7Q3tnLiJnfpnj8GRRPPJ2Rsy+kpaMbza+T627n7tuuZ8qkcbR2dSJdm/t/fTMTxlWRTmY8tOqwLOKRhFIjncoyqLKQX9x8FRd99zwamxrYtnk78ZzLxZd9h6OPmkxBKIh0bRxb+Zri+5cIlodLOK7rhMJhcdyMsZ8Dm+bPny/+eUjWN03qcAFx4bdPun/q2KFJO5dTVEWVHr1Ugqqze18zaSvFxq3beeLZV9m6bTeDB1ZRUVaIa5q89do7NBxsZOyEMVxywZlcetFpnHD8MZimwwsvvEFTc4ennXxYkV0KLMsDGT5fsZbbb/0t9zz8rDd97ZarOGHmJGQ8zZpt2xFCcPYps7np+suYMrqa3//8BsqLCjnY1MK+fQcwfAa2ZZKzTc6+8mamnHwZjZ29FBcWMGH0CMik0YTrjY2VAttxsJ2vG9uVeA3/imDCuJFMmTCaqeOGMWviWMaOGESmrZszTp3GjVdfiGlanH/Nz3jl/SVEggEe+OWN6FYGy5ZfM46iKKTTKSaMrOa3v7yFksoSerp7aGvvYdFbH7Cvro6gP8o1113Mn353O7f+8HLCfgXH+aZxSB7EmctkmTBqMNd/74LfCCHk2LFjxb/+5j99FixY4M6bt1ARQjSfMnvG08XlxYpp2o53auQdEBg+DZ/hJxbPsnrZOq687Dyu+t75nPudb/HjGy6nqCiKavg5cLCJESOqOG7WdLo6e0mmspSWl/QPofhqNugSCPrRVJWMmUUUlvLgk6/wytsfEgmFeeWJ3zJm8mjeeXcJr7z7CZFIhEfuvo2Nn7zK1Redg+W6LPjj30j1ZdF8OuFIHj7dIBTJo3F/G3ff/yiBgJ8X/vJbJowdSiprgvAEujVVJT8/ilDVI55EVSWqplJWWsKnr/yV1W8/wbI3nuDz1x7jntuvYXB1lJf+eh+GofPHx59l1YpafvPA07R39XDiMUdz/69vxEmnj9TYihDkcjkGV5cxY9pktuzcQyyR4/mXFtHaFkPzh3jjnY/IZpLYuRwfLV5KMBRk7uzpmJkUQsivIYMScKXrBEIBde60MV8U5+ctnT9/vvJNI+6+cbjhjh0LxYIFC8QHby3c+tmyVVftO9AS8BnaV9IZ75jPdV0mThzN7GOm8bcnX+STxcuYM/to0pkc736wjKXL1xAJB9E0Hw898iQbNtfS1d2Foun9uZU8Mjc4GPCTSiT5fOUGttc1IowAqzZsQSoKDYcaiafSbN/bzHtfrKSpuQnLMmlsaeaLleu57Xd/4+3F61DDEVThYqiwsXYXny5fSwKFXfvrcaWkrbWV7r4U2+sbUXSBTzfw+3WWr97EZ2u2YNke8KGrKpqqsWHrTtZv3c26LTtZv3UnW3ft4ZOlawhF8xFS8v6S1dz3t1cQoQgdnV3sPdBCPJ7AkrBlVz3JdAZVVT1lWDPHyOE1lJUWkkynWL5iLZs37yUYjiDwUK7jZh9NZ3cvj/3pKXbUHUJVVdo6e71B1V/56ALSGVtOGlUtH/7tDy+773d/aLrxxhuVRYsWyf/0/OD58+drCxYssB/527O33fP0W/d39KRsQ+ufmIhEKAq5nMXM6VM4ee7RPPzY8/T2xfnlT69h38FGXnntQwx/AL+hoyFIZbK4woPdFFVBlZ5EsQe1Kti5FI7pgKESNDxep+1KzFQa7Bz4A0QjYVxHkuxLgrAQmoa0FVA0Qnl5uNJGOhbZeAzQMMJhlH7N50w8BdJCCYYI6Aquq+EiySX6wDHRwxE03YNILSuHnUx6r0dRPJzWMb3oZQRRNBU3nQYEgUgAV1FQdYNMIoPM5kB1CeUVevg3oKtg9etpHjt9Epl0ivUbtuMPBT1CuFTJmVnmzJrEmWeeyttvf8iKlRsQmo56WNvjyz4YXImj64p6zbePfemhe++87Px589RFixY5/1MDoqWU4q677hJ33XWXdtoVP97y0eo9o3y6KoV0lcPHm1JKHNPi5huuJBKJYFkWhXlRfvfAY/TGM6jSxUqmwGfgD0e9EXNCkEplwLbQwwaq8IyZNbPe/zMCaP2Avuu45HIZNH8AVdHIpeKg+4gGPTWbZDoD0tNethOJfoOooPaXSrrmaUfalpcZi35Nar8fITTcTBIjFMVv+MjYlod7qyq+UAhNVXCBTCoJEnzhMIZ0sYROLhlDWjb+UABFUcm5Ck68FyUYJhgK4jgumb4+7z6QYEuUcBRVWljdPR4y5Q94sK0GgUge0nExrSxDaqopLiykducebKlAP47Qr9mOokiZzrlyyojy5PLnHxwbKi5umT9/Pv9ujvC/HU7Zf8qkCCHMp19770f7Gzo/3d/c7fgNVSiuIw6LboYCAZ578VXGjBlBcVERK5atRLpen/SUicO5/bqLefHtT3jjo3UEikpI9bRxy/fOZdZRE7j1d0/Q1B2HdJLLv3MyZ518LI88vZAVm/cgdcGIqiLuvu3nvPD6R2zbuZ/7//gz3vr4C15641M0v4+fXXs+QwdW8sIbH/Gz67+LbdnksjkMn0EkHOTZ196nML+AE2dN9tpdpMSnKix45Dk0BW647Bzu/+tLbNjeQEF+iPv/8FP2Hmjij08tQgYCBHX4829/jColt973JFlHYqb7uP7i0zh6ylju+MPfaGlPMaS6iPvuv5U3P17Gy28txl+Qx83XzuPc048nYKh8uGQtDz/xMiPHjOAnV52H5jH/0BWN9bXb+dUDL2AEguhqkP0Nbeyvb8H4/1R35mFSFWfb/1XVOae7Z2PfZJAdZBBRMaCADrIvohIYUIS4oCIxMZjoZxJjCHEJGmNETaKJ5s3i9opfXIICgsuwi4JhBwFx2GdgmKWnp5dzqur74/QMaPSLMWqSui7+G7rPqburnqrnuZ/7jnkgTKN9fEPmIBWgWzXNdwafc8ZPclu2PBDeez+nveyCyZN1SUmJum7y+GUXTbvpiUMVVTMCowOFcBqWfyYIqEtbVr29pVF4LKyASbp26sCEcSPof/YZrHtvJoeOHOW8M3tw/4+/g5SKux95hn2HjtE0P4cffXcG3TsWEsvJYdWMW9G+Q5sWzZg0bgQbtrzP3rIDTBo3jHFDB1K27yArX1/H2GGDOe/sIl5YupIO7duRF3Pp1rkTFUcrqKisIScnyrAL+nHpiCGs3bCJTBD6TSjlcVqndky+ZAy9enRnaMkskkYzY+ollL69gfsee5pMveSS0edy3dSvA/DKinX85dVVYAyD+/dl6oSxtGzalHHTbsXL8Zh48Qj2lx/jqadCRbwp44ezbdce4vEEd3znGlL19Wzbs5evjxnKll17Ka+opGleDvl5BSBCJoyxmljUyfK4stqL4sTWbBFaWu10aeGtnT/3locOb1+nFiyY/P91AJf/yEH6ueeeM8ZY+cs5c77XqV2L/cYIZRrYp9m/iTgSz1VEPBfPdbIfK0JymNa0b9eGH3zrCky8knuz4MbrE6FCbbyGiWOK6d6xkIWLljFqSH/6n9ULkUhghYvWmmQ6NHkMdOgk9vSv7qbVKS0pLz9KRsO6DVs5a+AEps76AVob7nzgMfoMnMDjT75M1HGprqnm4itnM2rabIZPmcXyt94mGgk/u0+vbvzm57djM/Ucr4kTr09nJ0Yz+7rL2LxjJ3/btJlbZ16JKzRISSYT5r7HjriAH99yLdXl5WitqTxezeDhg5kyfjiPPPEcvS+YwrmXzmT0VbN59JklRKI5aK2ZO+9hRpRcz8jJM7nlzkdwI272lBw2mFlzEsHiRCy1WhtaN83JjD63zwwhhC4qKrJ8qoHQZwRYCGFLSkpE167Na847vceM5rkRobUxQgh7gq8UxuOGfw0sAWsNSilWrFrNFZNG8/tfzeWs3j14a9VqYpEIOvCxSjNr+gTe2bydyd/8IfFEkm9PuxSbSYd1VhUaeWkTyv2/uXwlhe3b8cTDdxBxBFZrhHKQ+c2I5BWglCSWW4Bo0hIZzSEIfJo2acqedYuo2bqMfe+8SpvCVvgm7PFdtOxNJo0Zwo9vmkY6WY/rOIh4Hf37dGZgv7788GeP8MN5v+bcs4ooPu9MRDxOxPWoT9Tz5oqVzL31OmZOHY9SEikMXzujF8YYnn5hESqaT9PmbViyfDPHK+PkRF2UUjz96M+p/3ANlTtXcNWUsaRra5DK+ftC/sltO1boqCdVYfPobXPn3r6tuLjY+bS4+5m36MatesECXVxc7Dzx0JylPQeN/2ldxv2x1r4vhHRP/pkZYxq1nsn6E1lg/h8W8L0bCrj6iok8/Ptn2bfvAEMGDaS2NkG/r/Wh3xlF7C7bzwM/uRWJ4dKxw2jd8RQyiUR4yBYQmtnCS8tW8eLrq5k/9/9Ql0xSU10TdkiQ9VyyIGyA1RkwoVFlIpng+u/eSdpKHMelvCqF60WxwD0P/4Gkb7ntplnhbrF5OzZTx/XTwq352ssmoLN30OumT2TZwtIwNrous277OQt+35Yfff8mrAVfh93+Uko6djyFNSvfpdpYCvKj1NbVkEyGJMV7H36UtzfuIbcgn1UbNuNFcsLCCx+jFZ9gcQRSek6+l/7LO4ufe5DiYqe0tDT4LNhJPuMoLS3VFBc7u1YvnJPv+q9bJ+IaawOs+bsSlsGCNAjlIIC6hOG2eb9h/dbtzJ3/P7jR3MZHv+nqyRit2X/oMOf068OWnR8QjUa5asoYEplQ59F1Iwg3FwHkNW3JQ/c/znMLl5IXi2ElSDcCMoJSkWzt2oAOU46huqykti5DdW2cZKKOzoWtsqQ4MCqHmbfcza79B1FKkYxnaNXlVCaMG8a+wxW0bteGwvbtOHDgEBcNH0xhz84kM2lyohEqk4Zps39CvC6FEALPdVm8uJRj1dX8+p7vc+0NU5k4egCvP/sgN1xXQl11FUJAdX2ayuOVVFYeo0thO2TMQzdqTfORHdFYDMJzcqT/YY8OsRkWJKWl+rPi5vDZh6V0iDGUisJ8OT1+NLlGC68jVhshwkpBI9AARiJsyK3KzVMsWbieoVNmU1tejqtCau0ZvTpSMm4Uf1n8BiVX3wrRXKROs3bhE8yY/vUw8Y4N/RyCIFRSz6QRkSjfvv0+TuvaiTZtWmKNAisIAj/kVOksWU1a6jIZYrEIr/z5gcYXueMXj7Kv7ADGGKLScOxABTNn38Vfn/wl6Uw90yeNp2leHld9ey4vLXwTlGX4kAEseeZXfGPKWFLp8FmaFUTZtGEXs394F4/d/1Mcz+VYxXGm3TiHh+/+Hr+b94PG/PqhA+XIiCLQmnt/9N3GDNMHZQfoM+YbBCIacsbFSdu0NRbp4Bi/viBWd1npS4urKSlRLFhgPitogn92hF+gm5zap18m1rpUSy8qbCCEkLKRIIgNXTbzcmjfphkHD1eQqM9gpIMwmlbN8mjWJI+q2jjtmzenrOI4VXU+juOgUwmaN4nQumVzyiuO07ZVE45Vp6mNx+l6aguOVCeprE2DNbRuGqNVQRPeLztEgCEn6nBq2zZUVNVSVZ3EioB2rQpoWlCA0QaBQhFwsLIGnc5wStsCDhyuIqnBTybpcmoHMtbgOB5NcqPs2rOfINtdANCjUyvSmYBkSlMQddlZth8rYuh0gh6d2xNPJCmvriOV0jTNV/Tu2hGpJLs/2M/hI9W0bJNP+1bNCExo3KWEJOVnKDsS/xipzmZ91FWghHGVf3Rq7a71z1Bc7PAZt+bPDzDQ8EUF3QaNCNyC13wwyhpBg+WltVgZdsTrTIDrukiVtaOTkkwqia6vx81rih8YXM/FaajvCoGv0wSJemKR0IswbQQyEiWoS4CfBumGiQtjwFVEC3JI1fmQTIaUXi8GjoBoNHzDmnj4tyab7GjejIhySfuZMFOVSiFiMYRwsFJjtYXqasjPw40o/NpawIVkOsxX5+SiHIsb80jVB+EzpVLgejj5zfAiiiBIk0mHOW9hwXGd0FLPD04Q/YxAyHBr/0jcDXnQvnSkKzNV30nsWvMQ/fq5rF/v/7NQfT6ATwI5r+v51+pIk99ZtMYa2UDctVmivJQydCMBTKDxkwlatmpCUa/TWLVqHcLLQTleeM+zoKTF14bWeQ6vPvUQ8+b/nmdfWoaTm0e/07tRMmowCEHEdfiwbD8LFi6n7NAhxo86n0FnnR5OpFK4jmDxmi0cOlDONy69EJ21qNu77zDPvvAaVemQEntOUWcmjirm9bUbWbpmE67r0SzP5earJ/Dmu9tYu3YDs2dMIicaJeK5VB6v4qWly9m0qwKhHEYNPIPLLhpKNCfKmg2bWbBoJRWVtShXonVAJhHnzL6nY61g49824+UVIF0nBN6cyMU3XC2FsNjsoUqmj96f2L361s+zcj9PDP74qSuguNipKy19PO+0oWgV/Z3BaGmFJBRAaJTOFyh8P033bp3o3bMrubmK3j170awgH61cSkvXkM4ESKWQysWvrmLWN6+i86ltWbxiLV5BC9K11RR1PYXvzpzGsaoqEvVJOrY/hWumT2LgyMsZc8FAZl1dwocHj6B1QG40wu4Dx4i5MHvmdI5UVhKvS9C946lMumgYl1x7G7XxFHd8+2rGDhvEyCH9WTnxmyQzhqZ5UW694RvE/vA8Gzb8jR/ffD3pdJr9Bw7Tscup3DxrOsMmf4tWLVrwyp/uY+++w9TW1FJy0VD2HjzCi0vWEnEiDB44gIin6HVad1whad++LbXxJGvXvYuKRrAfucU2MEBkIBzPkToVghuGxODzwiT5V0ZpaUC/fm7djjceFzpznSWijFVGoA1ZXlRo8hSS4dLpJOedexYRL4dlr6/izLPPpn271tTHqxHZNq10JkPz5jnceGUJv/3j/6WqMkkk6oI1xOMJAq2ZNut2OhX241u33U2vrp0YNvJCjlfVEASa0ZOvpduAi+l87njmz38CYSyB1lxx4+306DSQ2+56kAsGnM3pPU+jR6dCRg85l2Ur13F6r54MHdgXXVuFNoZAaxK1CTKZMFnzy8f+TPdugzhv3JU0yctn0tghFA/oA0i+c/vPOHPwRFr3Gc0bK94jkhsjHY/jKUPxkPPZvXc/7+/ay5DiIXgRSZBKoazKNgKIhn4ta4Xjo6KO0qn7E9uWhCv3nzhQffEAA6xf79Ovn1u/Y9njTpC6TkihDFJaY0zDqTrjp2nbtiXjRg7h8KEjPL1gIUvfWMnLi16jTetmjB4xLHQrUeBXVTJx7Pnk5ebwmydfROQ1AW1AuaBcHKU492tncMHoIZw3oG/IvKiowot4OI5ixctPcnDja+xdt4iivkUkUwGOUgz62pkMmzCe4oH9ATh4cD8zpo6nuq6Wr0+/ke3v7+LGqyaDTmKNxlEK4Uhcx0NIwelF3bjw4jGMGXpB+J3Hqnh5yXJq6up5+c+PsH3jEq654hJcGaATcXr17s6ZfU/nnTXv8PJLi3nhlddZvfZt+p9zNqf36UMqWXdyF4oFYYRSrgpqT4AbXofsvwKPwxcx1q/3KS526kuXPR7reWGFFrFnhLI5ShMglKOkRFtDMpUm4sZCgp0JQpl7KUgGYf+wNgIvx2P2ddN4+sXF7Hn/Q6Jt2mGsDnuhIuHj/ujmG/jJ924A4MEnnmX18nVccckIAB7943OUV1bj5UQpO1JJr55dAfjpzTPhe4JUKsktP3uEeH2CGZeNJ5Wu59ZvXg1GMnzQufTq25OamjosYXun40r8IOCi4UO4aHgo8/vK0pU89cIbHK2s4rTzv87oCwdy8cjBzPv+LAoLW/Ltm+fh+2lSyRTRWAShHKw1OE6WuWIsyokiURh8g5DSIBR+7XdSO996qOGm8q+C+8Ws4I/F5OTON1+Wxh9prdxn3JhjrQ6UIzlacZw3S1fTrGUTJk8ax4gxxYwddSE1NXUsX/02wvXIJHzGjRhEUbeOzH/8f5GxfITWWVa/DbUugJt+9DOm3zwHgD17yzCVx9DZqVi9/m+8vvo9Vq7dRKsmzXDd0LDyu3N+QZ8hl3P6sKn8Yt6vuXziGJoVFFBTU8f0yydgnfAQdv20S0n7Wca2Dsgk64i4Hk8+v5BB468imc6QSic4WnaAaVMvYfa1k3njrbe598HHSaXTFHXpCJEou3fvZfOWrfQ5ozeXXjKaCZeMZMCAc9ixczc7du7Ci8UwBIFxI9IKWW+Nf0XmCwb3i1vBHwM5Vbp0VaTnqGIrMn+Srns+2milpJBKytIVq2lWUMD5gwdRWrqKaDRCbo6Hn7HgJ5l93RReX7GWDZt2EWveAqMDVChxjfEzABwoP85fn1/E5ReNYP6dt7J4yZvE49Xh6nrq142/2j+/sJhnX3kDgPcPHmTLpt24LZqS17Y1My+7mK3v72HARdeSxEHoDK89+QA3XHYxLy58E99YXCmwJtxHy6sTrF60irsefoK7b5lFyRUraB4R3HbjNdx24zUAHCo/yj2PPIn0Ijgu1CUyLF64lNP6nIZUDn995TUyaY0TjVptfS3cHEcYZ5cwqSv9nYvWZGNu8EVCIvgyxolfoYoUjZknpHOLMD7aD4JMOuU0b9aErl278O76jSAs0ViMdCLDhYP6suzpBxk97SaWrNgUsiV0eDc2WtM0P0bnwraUHT5GVXUdTfIi9OzUjrJDxzDap32b5vgmlDVw3QjHq+uoqY3TrbA1uw8foy6RDh1ipKBnx7ZUHKviSGUcL+qR8g3tWjahS4e27P7wIC1bNaWqKk55eQV9enSksjrOwSPHUcrSp0dn6jOCLVt30KFDW3p2KwRr2bL9Qw5VxonmxrDaoIOAIJmkW/fOIAS739+NG41pGYkqqTxMoJ+PiMzM2m2vHf9XrkJfPcAh6UfCXAtYr/eYiQbnEQfbVhqjtQ5EJghkJBrJipNY/LRP7x6d6dahBa+uWI+Ph4OPtbKxS01rTZDxcVwX5Si0NgSpdOifiAgTIUGQfSsLOTHcWAw/4+M6Cj+dDpm6KqTiSKXCLkFjQldxP4B0BtmkSRgWFOH/S6YRjsLzXKyFTDINQhLNiZJKpyGdAUvoguq5JypqWVOuTDoD1tpIJKKNVI6xJim1vT2549VfZleEggX6y0DhSwQ4+/nFxYrS0oDu49p7KviFkO6UsGXYBqCVtdkErDSkUxnIGLz8nEaDR3tSX7jI6lWQ7fYLuz5Do+UgCOjZqydtTmkXKgwYKPtgL/v3fhgq10nJWef0o90p7Thy4DDvvbMBBJwzoD/SVaT9NK7n4iqH9955l/p4AqEUOkvKb5A1bADNZr0Rw77mrAyDodEW9qMHHaGRUhkk1gbLldA31W99bSNz5kjmzrVfVLz98mPwJxYoSgMoUexacDADl7k9R76M9O5UUnbB+GCttkIpDMQiEYg5aBNkrenEyYIWIcsAzUdkXKxGSYmfqueCkUMoHj6MDe+EwqgT2p7Cb++9l52bt3Lb3T+hV+/eHDi4n84dOvK39e/xm4d/xZAxwylo1Yr85s2JV1VhUin27NxFTVU1XtZL0ZyQUMnmb8yJ1aFtVmE9JA82NoVZAViNsNIqVxnrH7dB+p7MzmUPAJbiYoe5c4Mvef4/mTb7xY9tWbTmSFP5+00mt9efhGOERZ6J40Wl8QGhLQhrTVbeTvzDveZEh50kSGc4o//XQEju++Ectm/cTPGIoezcspXOp3VnzMXjuOsHd/D0Y4+zY9sOSq6cSu2xY/x23n28v2MHxUOH8Yf5v+Kp3/yWVOAjhfwnN0JxsjyUFsKA4ygtpNDC/5NvMpfpHW8sapgHyv6ov4qZl3x1w8JcQ0mJouylan/7wu8LafujzVMIGQjlqmwhKpBZI+1GxSUhPrKHncwkOXmS08mAZu1P4c5Hfsmc++8i4sIH23fRt+9Z7Ny2na3vbiCvaQs2r9vAxs3b6FjUB4TCKEk0KsCxGGE///uFvBsrlKOs9KQ25jWj00OCLUuuZPubZRQXO43z8BWNrxLgBnqIzsZmJ7P11e2Z7QunaehvtP+UFcK3rudoJcO9FxsqHYoToP5dr05WhT2rk8DhAweZd8vt3DH7+xw7VsXYSZfwYVkZbTt0oFnrNtQdOkCLNq1pW1jI0SOHwUoETrbQEXKoT2AsGosAn35csQZLAAhURFmBsCazxBo7IrNt0ahgx7JSSkrCH++XcEr+d8fgfxCb50jYJvztC94Dpnm9x96tTOqaAHW5cLz21mZLfJYgRFLIT51pKciLRejcsZCxUyaAcmnVqh07gs289coiLhg1gtt/fg9b16+nT//+iHSa5a++gnAt1mTwcgpwnGjjvffvt2B7MofGCoSxWEdIRyKUtCZTK3TyeSH4bXL70rdPLKA5sGCu/jfN81cVgz81M2LD+DxHQmupj75UERz9YGleqy5/8K39QFibC7QXjusipMQagbVaWGFCJecTsmAWyM2NodNp3JwIbtRj7fJSXnvxZRLxBBvffY/8ggLadOzIvvd38cdHH+PI4XIcz8MV4Loe2zZuoqamGqnUybHVCiFsmPIQRgihpHKEUNkKtmWdtXa+FM6Nqe2L/xwc++BgCGyJhG0mfMd/3xD8R405kuK35MlbmVc0ukgiJlrDcCT9hXSiIqxDZkuRRmcJ2fiZtNS+H+7XNuyz9GJRlPIIMj5+OhPuuMYgox4RLxLSe4whnUpbx/NQjmOxxgohQ2KyEAqhQjdxLNb4VljWWyHf8q14Ptj+6tsfTfAUfaUx9r8M4JOeq6REEjZTNU5WpGhkN0fKc7UxxVg10Fp6SuUosvfS0BvFgtBZjTVhTcg2sEKedOXKMj+NMVk7FYGUrjAnOEfhfRuL1T7AB0LYdcBbUoo1iS2LN30C+cEA5j9vIv/jR+Oq/ngCXkR6X9pFEhRZbc+0gl5AN6CtFLaFhJyGepyV4qOvnP0UYxucVCxYUW8RtQJbDmaPtWyTkk1ayC2ZaPnuv6PLFBc7lA4x/0mr9b8U4E8Au6Gw8QmjebfRBYkct4XrZwqNUgUyMF6gTNFHLg5Z5SCj/Q8k1GpBGtfb42VUbXzHi5Wfml+vqBD/DaCePP4fYEYBNs4TU5UAAAAASUVORK5CYII=" style="width:28px;height:28px;border-radius:7px;object-fit:cover"></div> Kurtex</div>
  <div class="hamburger" onclick="toggleSidebar()"><i class="ph ph-list"></i></div>
</div>
<div class="sidebar-overlay" id="sidebar-overlay" onclick="closeSidebar()"></div>

<div class="layout">
<aside class="sidebar" id="sidebar">
  <div class="sidebar-logo">
    <div class="logo-icon" style="background:none;padding:0;overflow:hidden"><img src="data:image/png;base64,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" style="width:32px;height:32px;border-radius:9px;object-fit:cover"></div>
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
  setTimeout(refresh, 50);
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
    var now = new Date();
    var dateStr = now.toLocaleDateString('en-US',{weekday:'long',year:'numeric',month:'long',day:'numeric'});
    var timeStr = now.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'});
    var rate = d.total ? Math.round(d.done/d.total*100) : 0;
    var resRate = d.total ? Math.round((d.total-d.missed)/d.total*100) : 0;
    document.getElementById('report-content').innerHTML =
      '<div style="border-bottom:2px solid var(--accent);padding-bottom:16px;margin-bottom:20px">'
      + '<div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:12px">'
      + '<div>'
      + '<div style="font-size:22px;font-weight:800;color:var(--text);letter-spacing:-.3px">'+d.label+'</div>'
      + '<div style="font-size:12px;color:var(--muted);margin-top:3px">Kurtex Truck Maintenance — Operations Report</div>'
      + '</div>'
      + '<div style="text-align:right">'
      + '<div style="font-size:11px;color:var(--muted)">Generated</div>'
      + '<div style="font-size:12px;font-weight:600;color:var(--text)">'+dateStr+'</div>'
      + '<div style="font-size:11px;color:var(--muted)">'+timeStr+'</div>'
      + '</div></div></div>'

      + '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px">'
      + '<div style="background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center">'
      + '<div style="font-size:28px;font-weight:800;color:var(--accent)">'+d.total+'</div>'
      + '<div style="font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-top:3px">Total Alerts</div>'
      + '</div>'
      + '<div style="background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center">'
      + '<div style="font-size:28px;font-weight:800;color:var(--green)">'+d.done+'</div>'
      + '<div style="font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-top:3px">Resolved</div>'
      + '</div>'
      + '<div style="background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center">'
      + '<div style="font-size:28px;font-weight:800;color:var(--red)">'+d.missed+'</div>'
      + '<div style="font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-top:3px">Missed</div>'
      + '</div>'
      + '</div>'

      + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:20px">'
      + '<div style="background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:12px 16px;display:flex;align-items:center;justify-content:space-between">'
      + '<span style="font-size:12px;color:var(--muted);font-weight:500">Resolution Rate</span>'
      + '<span style="font-size:18px;font-weight:800;color:'+(resRate>=80?'var(--green)':resRate>=60?'var(--yellow)':'var(--red)')+'">'+resRate+'%</span>'
      + '</div>'
      + '<div style="background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:12px 16px;display:flex;align-items:center;justify-content:space-between">'
      + '<span style="font-size:12px;color:var(--muted);font-weight:500">Avg Response Time</span>'
      + '<span style="font-size:18px;font-weight:800;color:var(--text)">'+d.avg_resp+'</span>'
      + '</div>'
      + '</div>'

      + (d.leaderboard.length ? ''
        + '<div style="margin-bottom:20px">'
        + '<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--border)">Agent Performance</div>'
        + '<table style="width:100%;border-collapse:collapse;font-size:13px">'
        + '<thead><tr>'
        + '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">#</th>'
        + '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Agent</th>'
        + '<th style="text-align:right;padding:6px 8px;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Cases</th>'
        + '</tr></thead><tbody>'
        + d.leaderboard.map(function(a,i){
            return '<tr style="border-top:1px solid var(--border)">'
              + '<td style="padding:8px;font-weight:700;color:var(--muted);width:30px">'+(i+1)+'.</td>'
              + '<td style="padding:8px;font-weight:500">'+(medals[i]?medals[i]+' ':'')+a.name+'</td>'
              + '<td style="padding:8px;text-align:right;font-weight:700;color:var(--accent)">'+a.count+'</td>'
              + '</tr>';
          }).join('')
        + '</tbody></table></div>'
        : '')

      + (d.top_groups.length ? ''
        + '<div style="margin-bottom:20px">'
        + '<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--border)">Most Active Groups</div>'
        + d.top_groups.map(function(g,i){
            var maxCount = d.top_groups[0].count;
            var pct = Math.round(g.count/maxCount*100);
            return '<div style="display:flex;align-items:center;gap:10px;padding:6px 0;border-top:1px solid var(--border)">'
              + '<span style="font-size:12px;font-weight:500;width:180px;flex-shrink:0">'+g.name+'</span>'
              + '<div style="flex:1;height:5px;background:var(--surface3);border-radius:3px">'
              + '<div style="height:100%;border-radius:3px;background:var(--accent);width:'+pct+'%"></div></div>'
              + '<span style="font-size:12px;font-weight:700;color:var(--accent);width:30px;text-align:right">'+g.count+'</span>'
              + '</div>';
          }).join('')
        + '</div>'
        : '')

      + (d.missed_cases.length ? ''
        + '<div>'
        + '<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--red);margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--border)">Unresolved Alerts ('+d.missed+')</div>'
        + '<table style="width:100%;border-collapse:collapse;font-size:12px">'
        + '<thead><tr>'
        + '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Driver</th>'
        + '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Group</th>'
        + '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-size:10px;font-weight:600;text-transform:uppercase">Time</th>'
        + '</tr></thead><tbody>'
        + d.missed_cases.map(function(c){
            return '<tr style="border-top:1px solid var(--border)">'
              + '<td style="padding:7px 8px;font-weight:500">'+c.driver+'</td>'
              + '<td style="padding:7px 8px;color:var(--muted)">'+c.group+'</td>'
              + '<td style="padding:7px 8px;color:var(--muted);font-size:11px">'+c.opened+'</td>'
              + '</tr>';
          }).join('')
        + '</tbody></table></div>'
        : '');
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
