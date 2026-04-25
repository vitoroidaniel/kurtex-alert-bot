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


TESTING_GROUPS = {"testing", "test", "tests"}

def is_testing(c):
    return (c.get("group_name") or "").lower().strip() in TESTING_GROUPS

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
        # Exclude testing cases from all stats
        real = [c for c in cases if not is_testing(c)]
        tc = [c for c in real if (c.get("opened_at") or "").startswith(today)]
        wc = [c for c in real if (c.get("opened_at") or "") >= wk]
        mc = [c for c in real if (c.get("opened_at") or "") >= mo]
        st = Counter(c.get("status","open") for c in tc)

        def lb(lst):
            cnt = Counter(c["agent_name"] for c in lst if c.get("agent_name") and c.get("status") in ("assigned","reported","done"))
            return [{"name":n,"count":v} for n,v in cnt.most_common(10)]
        grps = Counter(c.get("group_name","Unknown") for c in real)
        hashtags = re.findall(r'#\w+', " ".join(c.get("description","") for c in real).lower())
        rt = [c["response_secs"] for c in real if c.get("response_secs")]
        avg = int(sum(rt)/len(rt)) if rt else 0
        return jsonify({
            "today": {"total":len(tc),"open":st.get("open",0),"assigned":st.get("assigned",0)+st.get("reported",0),"done":st.get("done",0),"missed":st.get("missed",0)},
            "week":  {"total":len(wc),"done":sum(1 for c in wc if c.get("status")=="done"),"missed":sum(1 for c in wc if c.get("status")=="missed")},
            "month": {"total":len(mc),"done":sum(1 for c in mc if c.get("status")=="done"),"missed":sum(1 for c in mc if c.get("status")=="missed")},
            "all_time": {"total":len(real),"done":sum(1 for c in real if c.get("status")=="done"),"avg_resp":fmt_secs(avg)},
            "leaderboard_day": lb(tc), "leaderboard_week": lb(wc), "leaderboard_month": lb(mc),
            "top_groups": [{"name":n,"count":v} for n,v in grps.most_common(5)],
            "top_words": [{"word":w,"count":v} for w,v in Counter(hashtags).most_common(15)],
            "reassigned_count": sum(1 for c in real if c.get("reassigned")),
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
        if f != "testing":
            cases = [c for c in cases if not is_testing(c)]
        if date_filter:
            cases = [c for c in cases if (c.get("opened_at") or "").startswith(date_filter)]
        elif f == "today":    cases = [c for c in cases if (c.get("opened_at") or "").startswith(today_str())]
        elif f == "week":     cases = [c for c in cases if (c.get("opened_at") or "") >= week_start_str()]
        elif f == "missed":   cases = [c for c in cases if c.get("status") == "missed"]
        elif f == "active":   cases = [c for c in cases if c.get("status") in ("open","assigned","reported")]
        elif f == "reassigned": cases = [c for c in cases if c.get("reassigned")]
        elif f == "testing":  cases = [c for c in cases if is_testing(c)]
        status_f = request.args.get("status","").strip().lower()
        if status_f:
            cases = [c for c in cases if (c.get("status") or "").lower() == status_f]
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
                    "pickup":           c.get("pickup",""),
                    "delivery":         c.get("delivery",""),
                    "comments":         c.get("comments",""),
                    "setpoint":         c.get("setpoint",""),
                    "current_temp":     c.get("current_temp",""),
                    "temp_recorder":    c.get("temp_recorder",""),
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
        # Exclude testing cases from agent stats
        cases = [c for c in load_cases() if not is_testing(c) and (c.get("agent_name") or "").lower() == agent_name.lower()]
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
        # Exclude testing cases from agent stats
        all_cases = load_cases()
        cases = [c for c in all_cases if not is_testing(c)]
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
        # Exclude testing cases
        cases = [c for c in load_cases() if not is_testing(c)]
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
        cases = [c for c in load_cases() if not is_testing(c) and c.get("vehicle_type")]
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
        cases     = [c for c in load_cases() if not is_testing(c)]
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
    cases = [c for c in load_cases() if not is_testing(c)]
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


@app.route("/api/heatmap")
def api_heatmap():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        cases = [c for c in load_cases() if not is_testing(c)]
        heatmap = [[0]*24 for _ in range(7)]
        for c in cases:
            if not c.get("opened_at"): continue
            try:
                dt = datetime.fromisoformat(c["opened_at"]).astimezone(et)
                heatmap[dt.weekday()][dt.hour] += 1
            except: pass
        days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
        return jsonify({"heatmap": heatmap, "days": days, "max": max(max(r) for r in heatmap) or 1})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trends")
def api_trends():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    try:
        from datetime import date
        cases = [c for c in load_cases() if not is_testing(c)]
        period = request.args.get("period","30")
        days = int(period)
        today = date.today()
        labels, totals, resolved, missed_arr, avg_resp_arr = [], [], [], [], []
        for i in range(days-1, -1, -1):
            d = today - timedelta(days=i)
            ds = d.isoformat()
            day_cases = [c for c in cases if (c.get("opened_at") or "").startswith(ds)]
            rt = [c["response_secs"] for c in day_cases if c.get("response_secs")]
            labels.append(d.strftime("%b %d"))
            totals.append(len(day_cases))
            resolved.append(sum(1 for c in day_cases if c.get("status")=="done"))
            missed_arr.append(sum(1 for c in day_cases if c.get("status")=="missed"))
            avg_resp_arr.append(int(sum(rt)/len(rt)) if rt else 0)
        return jsonify({"labels":labels,"totals":totals,"resolved":resolved,"missed":missed_arr,"avg_resp":avg_resp_arr})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/comparison")
def api_comparison():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    try:
        from datetime import date
        cases = [c for c in load_cases() if not is_testing(c)]
        today = date.today()
        this_mon = today - timedelta(days=today.weekday())
        last_mon = this_mon - timedelta(days=7)
        last_sun = this_mon - timedelta(days=1)
        def week_stats(start, end):
            wc = [c for c in cases if start.isoformat() <= (c.get("opened_at") or "")[:10] <= end.isoformat()]
            total = len(wc); done = sum(1 for c in wc if c.get("status")=="done")
            missed = sum(1 for c in wc if c.get("status")=="missed")
            rt = [c["response_secs"] for c in wc if c.get("response_secs")]
            avg = int(sum(rt)/len(rt)) if rt else 0
            rate = round(done/total*100) if total else 0
            return {"total":total,"done":done,"missed":missed,"avg_resp":fmt_secs(avg),"avg_secs":avg,"rate":rate}
        tw = week_stats(this_mon, today)
        lw = week_stats(last_mon, last_sun)
        def delta(a, b, reverse=False):
            if b == 0: return {"pct": 0, "up": True}
            pct = round((a-b)/b*100)
            up = pct > 0 if not reverse else pct < 0
            return {"pct": abs(pct), "up": up}
        return jsonify({
            "this_week": tw, "last_week": lw,
            "delta_total":  delta(tw["total"], lw["total"]),
            "delta_done":   delta(tw["done"], lw["done"]),
            "delta_missed": delta(tw["missed"], lw["missed"], reverse=True),
            "delta_rate":   delta(tw["rate"], lw["rate"]),
            "delta_resp":   delta(tw["avg_secs"], lw["avg_secs"], reverse=True),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/fleet_intelligence")
def api_fleet_intelligence():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    try:
        from collections import defaultdict
        all_cases = [c for c in load_cases() if not is_testing(c)]
        reported = [c for c in all_cases if c.get("vehicle_type")]
        unit_data = defaultdict(lambda: {"cases":[], "vtype":""})
        for c in reported:
            unit = (c.get("unit_number") or "").strip()
            if not unit: continue
            unit_data[unit]["vtype"] = c.get("vehicle_type","")
            unit_data[unit]["cases"].append(c)
        top_units = []
        for unit, data in unit_data.items():
            cs = data["cases"]
            total = len(cs)
            issues = Counter((c.get("issue_text") or "")[:50] for c in cs if c.get("issue_text"))
            top_issue = issues.most_common(1)[0][0] if issues else "—"
            last_case = max(cs, key=lambda c: c.get("opened_at",""))
            top_units.append({
                "unit": unit, "vtype": data["vtype"], "total": total,
                "top_issue": top_issue, "last_seen": fmt_dt(last_case.get("opened_at")),
            })
        top_units.sort(key=lambda x: -x["total"])
        driver_data = defaultdict(list)
        for c in reported:
            d = (c.get("report_driver") or "").strip()
            if d: driver_data[d].append(c)
        top_drivers = []
        for name, cs in driver_data.items():
            total = len(cs)
            issues = Counter((c.get("issue_text") or "")[:50] for c in cs if c.get("issue_text"))
            top_issue = issues.most_common(1)[0][0] if issues else "—"
            top_drivers.append({"name": name, "total": total, "top_issue": top_issue})
        top_drivers.sort(key=lambda x: -x["total"])
        return jsonify({
            "top_units": top_units[:20],
            "top_drivers": top_drivers[:20],
            "total_reports": len(reported),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── HTML pages ────────────────────────────────────────────────────────────────

LOGIN_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Kurtex Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Plus Jakarta Sans',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#1a1208}
.card{position:relative;z-index:1;width:100%;max-width:380px;margin:0 auto;padding:20px}
.card-inner{background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);border-radius:24px;padding:44px 36px;backdrop-filter:blur(16px)}
.logo{width:60px;height:60px;border-radius:16px;background:linear-gradient(135deg,#C17B3F,#8B4A1A);display:flex;align-items:center;justify-content:center;margin:0 auto 20px;font-size:28px;box-shadow:0 4px 24px rgba(193,123,63,.5)}
h1{color:#fff;font-size:26px;font-weight:800;margin-bottom:6px;letter-spacing:-.4px;line-height:1.2;text-align:center}
.tagline{color:rgba(255,255,255,.5);font-size:13px;margin-bottom:28px;text-align:center}
.tg-wrap{display:flex;justify-content:center}
.error{color:#F87171;font-size:12px;margin-bottom:14px;background:rgba(248,113,113,.1);border:1px solid rgba(248,113,113,.25);border-radius:8px;padding:8px 12px;text-align:center}
</style>
</head>
<body>
<div class="card">
  <div class="card-inner">
    <div class="logo">🚛</div>
    <h1>Kurtex Dashboard</h1>
    <p class="tagline">Truck Maintenance Command Center</p>
    {% if error %}<div class="error">Login failed. Please try again.</div>{% endif %}
    <div class="tg-wrap">
      <script async src="https://telegram.org/js/telegram-widget.js?22"
        data-telegram-login="{{ bot_username }}"
        data-size="large"
        data-auth-url="/auth/telegram"
        data-request-access="write">
      </script>
    </div>
  </div>
</div>
</body></html>"""


DASHBOARD_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Kurtex Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/@phosphor-icons/web@2.0.3/src/regular/style.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{
  --accent:#C17B3F;--accent2:#A0622A;--accent-bg:rgba(193,123,63,.12);
  --bg:#FAF8F5;--surface:#FFFFFF;--surface2:#F3EFE8;--surface3:#E8E2D9;
  --text:#2C2416;--muted:#8C7B6B;--muted2:#B5A898;
  --border:#E2D9CC;--border2:#D0C7BA;
  --green:#3D7A4F;--red:#C0392B;--yellow:#C17B3F;--purple:#7B5EA7;
  --yellow-bg:rgba(193,123,63,.1);--yellow:var(--accent);
  --shadow:0 2px 12px rgba(44,36,22,.08);
  --radius:10px;
}
[data-theme="dark"]{
  --bg:#1A1208;--surface:#231A0E;--surface2:#2C2012;--surface3:#352818;
  --text:#F0E8DF;--muted:#9E8E7E;--muted2:#6E5E4E;
  --border:#3D3020;--border2:#4A3C2A;
  --accent-bg:rgba(193,123,63,.18);
  --shadow:0 2px 12px rgba(0,0,0,.3);
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Plus Jakarta Sans',sans-serif;background:var(--bg);color:var(--text);font-size:13px;overflow-x:hidden}
.layout{display:flex;min-height:100vh}

/* ── Sidebar ── */
.sidebar{width:230px;min-height:100vh;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0;position:sticky;top:0;height:100vh;overflow-y:auto}
.sidebar-logo{display:flex;align-items:center;gap:10px;padding:18px 16px 12px;border-bottom:1px solid var(--border)}
.sidebar-logo img{width:36px;height:36px;border-radius:50%;object-fit:cover;flex-shrink:0}
.logo-text h2{font-size:15px;font-weight:800;color:var(--text)}
.logo-text small{font-size:10px;color:var(--muted)}
nav{padding:10px 8px;flex:1}
.nav-item{display:flex;align-items:center;gap:9px;padding:9px 10px;border-radius:8px;cursor:pointer;font-size:12px;font-weight:500;color:var(--muted);transition:all .15s;position:relative}
.nav-item:hover{background:var(--surface2);color:var(--text)}
.nav-item.active{background:var(--accent-bg);color:var(--accent);font-weight:600}
.nav-item i{font-size:15px;flex-shrink:0}
.nav-badge{background:var(--red);color:#fff;font-size:9px;font-weight:700;border-radius:20px;padding:1px 5px;margin-left:auto}
.nav-group{margin-top:2px}
.nav-group-header{display:flex;align-items:center;justify-content:space-between;padding:9px 10px;border-radius:8px;cursor:pointer;font-size:12px;font-weight:500;color:var(--muted);transition:all .15s}
.nav-group-header:hover{background:var(--surface2);color:var(--text)}
.nav-group-header span{display:flex;align-items:center;gap:9px}
.nav-group-header i{font-size:15px}
.nav-caret{font-size:12px;transition:transform .2s}
.nav-caret.open{transform:rotate(180deg)}
.nav-group-items{max-height:0;overflow:hidden;transition:max-height .25s ease}
.nav-group-items.open{max-height:300px}
.nav-sub{padding-left:30px!important;font-size:11px!important}
.sidebar-footer{padding:12px 10px;border-top:1px solid var(--border)}
.user-chip{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:8px;background:var(--surface2);margin-bottom:8px}
.user-avatar{width:28px;height:28px;border-radius:50%;object-fit:cover;flex-shrink:0}
.user-avatar-init{width:28px;height:28px;border-radius:50%;background:var(--accent-bg);color:var(--accent);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;flex-shrink:0}
.user-name{font-size:11px;font-weight:600;color:var(--text)}
.user-role{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.theme-btn,.logout-btn{width:100%;padding:7px 10px;border-radius:7px;border:1px solid var(--border);background:var(--surface);color:var(--muted);font-family:inherit;font-size:11px;cursor:pointer;display:flex;align-items:center;gap:6px;margin-bottom:4px;transition:all .15s}
.theme-btn:hover,.logout-btn:hover{background:var(--surface2);color:var(--text)}

/* ── Mobile header ── */
.mobile-header{display:none;align-items:center;justify-content:space-between;padding:12px 16px;background:var(--surface);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:100}
.mobile-logo{display:flex;align-items:center;gap:8px;font-size:15px;font-weight:800;color:var(--text)}
.mobile-logo img{width:28px;height:28px;border-radius:50%}
.hamburger{width:32px;height:32px;border-radius:7px;border:1px solid var(--border);background:var(--surface2);display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--text);font-size:18px}
.sidebar-overlay{display:none;position:fixed;inset:0;background:rgba(44,36,22,.5);z-index:199;backdrop-filter:blur(1px)}
.sidebar-overlay.open{display:block}

/* ── Main ── */
.main{flex:1;padding:18px 20px 40px;min-width:0;overflow-x:hidden}
.topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;gap:10px;flex-wrap:wrap}
.topbar h1{font-size:20px;font-weight:800;color:var(--text)}
.topbar-right{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.badge-btn{display:flex;align-items:center;gap:5px;padding:6px 12px;border-radius:8px;border:1px solid var(--border);background:var(--surface);color:var(--muted);font-family:inherit;font-size:11px;font-weight:600;cursor:pointer;text-decoration:none;transition:all .15s}
.badge-btn:hover{background:var(--surface2);color:var(--text)}
.badge-btn .dot{width:7px;height:7px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

/* ── Pages ── */
.page{display:none}
.page.active{display:block}

/* ── Stat grid ── */
.stat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px;margin-bottom:18px}
.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 12px;cursor:default}
.stat-label{font-size:10px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}
.stat-value{font-size:26px;font-weight:800;color:var(--text)}
.v-accent{color:var(--accent)}.v-green{color:var(--green)}.v-red{color:var(--red)}
.v-yellow{color:var(--yellow)}.v-purple{color:var(--purple)}.v-blue{color:#2E6EA6}
.v-sm{font-size:18px!important}

/* ── Two col ── */
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}

/* ── Card ── */
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:16px}
.card-title{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:12px;display:flex;align-items:center;gap:6px}

/* ── Section ── */
.section{margin-bottom:18px}
.section-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;flex-wrap:wrap;gap:8px}
.section-title{font-size:13px;font-weight:700;color:var(--text)}

/* ── Table ── */
.table-wrap{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.table-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:12px}
thead th{background:var(--surface2);padding:9px 12px;text-align:left;font-size:10px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;white-space:nowrap;border-bottom:1px solid var(--border)}
tbody tr{border-bottom:1px solid var(--border);cursor:pointer;transition:background .1s}
tbody tr:hover{background:var(--surface2)}
tbody tr:last-child{border-bottom:none}
tbody td{padding:9px 12px;vertical-align:middle}
.desc-cell{max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font-size:11px}

/* ── Status badges ── */
.status-badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em}
.s-open{background:rgba(46,110,166,.12);color:#2E6EA6}
.s-assigned{background:rgba(193,123,63,.12);color:var(--accent)}
.s-reported{background:rgba(123,94,167,.12);color:var(--purple)}
.s-done{background:rgba(61,122,79,.12);color:var(--green)}
.s-missed{background:rgba(192,57,43,.12);color:var(--red)}
.reassign-badge{display:inline-flex;align-items:center;margin-left:4px;padding:1px 6px;border-radius:20px;font-size:9px;font-weight:600;background:rgba(123,94,167,.12);color:var(--purple)}

/* ── List rows ── */
.list-row{display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--border)}
.list-row:last-child{border-bottom:none}
.medal{font-size:14px;flex-shrink:0;width:22px}
.list-name{flex:1;font-size:12px;font-weight:500;color:var(--text);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar-wrap{flex:1;height:4px;background:var(--surface3);border-radius:2px;min-width:40px}
.bar-fill{height:100%;background:var(--accent);border-radius:2px;transition:width .4s}
.list-count{font-size:12px;font-weight:700;color:var(--accent);flex-shrink:0;min-width:20px;text-align:right}

/* ── Filter tabs ── */
.filter-tabs{display:flex;gap:4px;flex-wrap:wrap}
.tab-btn{padding:5px 12px;border-radius:6px;border:1px solid var(--border);background:var(--surface);color:var(--muted);font-family:inherit;font-size:11px;font-weight:600;cursor:pointer;transition:all .15s;white-space:nowrap}
.tab-btn:hover{background:var(--surface2);color:var(--text)}
.tab-btn.active{background:var(--accent);border-color:var(--accent);color:#fff}
.toggle-tabs{display:flex;gap:4px;margin-bottom:12px;background:var(--surface2);border-radius:8px;padding:3px}
.toggle-btn{flex:1;padding:5px 10px;border-radius:6px;border:none;background:transparent;color:var(--muted);font-family:inherit;font-size:11px;font-weight:600;cursor:pointer;transition:all .15s}
.toggle-btn.active{background:var(--surface);color:var(--text);box-shadow:var(--shadow)}

/* ── Search ── */
.search-wrap{position:relative;margin-bottom:14px}
.search-wrap i{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:15px;pointer-events:none}
.search-wrap input{width:100%;padding:9px 14px 9px 38px;border-radius:9px;border:1px solid var(--border);background:var(--surface);color:var(--text);font-family:inherit;font-size:13px;outline:none;transition:border-color .15s}
.search-wrap input:focus{border-color:var(--accent)}

/* ── Modal ── */
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(44,36,22,.5);z-index:300;padding:20px;align-items:center;justify-content:center;backdrop-filter:blur(2px)}
.modal-overlay.open{display:flex}
.modal{background:var(--surface);border-radius:16px;padding:24px;max-width:560px;width:100%;max-height:90vh;overflow-y:auto;position:relative;border:1px solid var(--border)}
.modal h2{font-size:16px;font-weight:700;margin-bottom:16px;color:var(--text);padding-right:24px}
.modal-close{position:absolute;top:14px;right:14px;width:26px;height:26px;border-radius:50%;border:1px solid var(--border);background:var(--surface2);color:var(--muted);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:14px}
.modal-close:hover{background:var(--surface3);color:var(--text)}
.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}
.detail-item{background:var(--surface2);border-radius:8px;padding:10px}
.detail-label{font-size:9px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}
.detail-val{font-size:12px;font-weight:500;color:var(--text)}
.desc-box,.notes-box{border-radius:8px;padding:10px 12px;margin-bottom:10px}
.desc-box{background:var(--accent-bg);border:1px solid rgba(193,123,63,.2)}
.notes-box{background:rgba(61,122,79,.08);border:1px solid rgba(61,122,79,.2)}
.box-label{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);display:block;margin-bottom:4px}
.box-text{font-size:12px;color:var(--text);line-height:1.5;white-space:pre-wrap;word-break:break-word}
.timeline{display:flex;align-items:center;gap:0;margin-bottom:16px;background:var(--surface2);border-radius:9px;padding:10px 8px;overflow-x:auto}
.tl-step{display:flex;flex-direction:column;align-items:center;flex:1;position:relative;min-width:60px}
.tl-step:not(:last-child)::after{content:'';position:absolute;top:13px;left:50%;width:100%;height:2px;background:var(--border);z-index:0}
.tl-dot{width:26px;height:26px;border-radius:50%;background:var(--surface3);border:2px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;color:var(--muted);position:relative;z-index:1}
.tl-dot.active{background:var(--accent);border-color:var(--accent);color:#fff}
.tl-dot.done{background:var(--green);border-color:var(--green);color:#fff}
.done-step .tl-dot{background:var(--green);border-color:var(--green);color:#fff}
.tl-label{font-size:9px;font-weight:600;color:var(--muted);margin-top:4px;text-align:center}
.tl-time{font-size:8px;color:var(--muted2);text-align:center;max-width:60px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

/* ── Agent accordion rows ── */
.agent-row{border:1px solid var(--border);border-radius:10px;margin-bottom:6px;overflow:hidden;background:var(--surface);transition:box-shadow .15s}
.agent-row:hover{box-shadow:0 1px 8px rgba(44,36,22,.06)}
.agent-row-header{display:flex;align-items:center;justify-content:space-between;padding:12px 14px;cursor:pointer;user-select:none;gap:10px}
.agent-row-header:hover{background:var(--surface2)}
.agent-row-left{display:flex;align-items:center;gap:10px;flex:1;min-width:0}
.agent-avatar{width:36px;height:36px;border-radius:50%;background:var(--accent-bg);color:var(--accent);display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:700;flex-shrink:0}
.agent-row-stats{display:flex;align-items:center;gap:16px;flex-shrink:0}
.agent-mini-stat{text-align:center;min-width:36px}
.agent-row-caret{color:var(--muted);transition:transform .22s;font-size:14px;display:flex;align-items:center;margin-left:4px}
.agent-row-caret.open{transform:rotate(180deg)}
.agent-row-detail{max-height:0;overflow:hidden;transition:max-height .32s ease;border-top:0 solid var(--border)}
.agent-row-detail.open{max-height:640px;border-top-width:1px}
.agent-exp-stat{background:var(--surface2);border-radius:8px;padding:9px 6px;text-align:center}
.aes-val{font-size:17px;font-weight:700}
.aes-lbl{font-size:9px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-top:2px}

/* ── Stats list ── */
.stats-list .row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);font-size:12px}
.stats-list .row:last-child{border-bottom:none}
.stats-list .val{font-weight:700;color:var(--accent)}
.word-grid{display:flex;flex-wrap:wrap;gap:5px;padding:4px 0}
.word-tag{background:var(--accent-bg);color:var(--accent);padding:4px 10px;border-radius:20px;font-size:11px;font-weight:500}
.agent-stat{background:var(--surface2);border-radius:8px;padding:9px 6px;text-align:center}
.agent-stat-val{font-size:18px;font-weight:800}
.agent-stat-label{font-size:9px;color:var(--muted);text-transform:uppercase;font-weight:600;letter-spacing:.05em;margin-top:2px}

/* ── Report modal ── */
.report-modal-overlay{display:none;position:fixed;inset:0;background:rgba(44,36,22,.5);z-index:400;align-items:center;justify-content:center;padding:20px;backdrop-filter:blur(2px)}
.report-modal-overlay.open{display:flex}
.report-modal{background:var(--surface);border-radius:16px;max-width:600px;width:100%;max-height:90vh;display:flex;flex-direction:column;border:1px solid var(--border)}
.report-header{display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid var(--border);flex-wrap:wrap;gap:8px}
.report-header h2{font-size:15px;font-weight:700;display:flex;align-items:center;gap:8px}
.report-tabs{display:flex;gap:4px}
.report-tab{padding:5px 12px;border-radius:6px;border:1px solid var(--border);background:var(--surface2);color:var(--muted);font-family:inherit;font-size:11px;font-weight:600;cursor:pointer}
.report-tab.active{background:var(--accent);border-color:var(--accent);color:#fff}
.report-close{width:26px;height:26px;border-radius:50%;border:1px solid var(--border);background:var(--surface2);color:var(--muted);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:14px}
.report-body{flex:1;overflow-y:auto;padding:16px 20px}
.report-period-bar{display:flex;align-items:center;gap:8px;margin-bottom:14px;flex-wrap:wrap}
.report-period-bar select,.report-period-bar input{padding:6px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:7px;font-size:12px;color:var(--text);font-family:inherit;outline:none}
.report-generate-btn{padding:6px 14px;background:var(--accent);color:#fff;border:none;border-radius:7px;font-family:inherit;font-size:12px;font-weight:600;cursor:pointer}
.report-footer{padding:12px 20px;border-top:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
.ts{font-size:10px;color:var(--muted)}
.print-report-btn{display:inline-flex;align-items:center;gap:5px;padding:7px 14px;background:var(--accent);color:#fff;border:none;border-radius:8px;font-family:inherit;font-size:11px;font-weight:600;cursor:pointer}

/* ── Loading / empty ── */
.loading{padding:24px;text-align:center;color:var(--muted);font-size:12px}
.empty-state{padding:32px;text-align:center;color:var(--muted);font-size:13px}

/* ── Mobile ── */
@media(max-width:768px){
  .layout{display:block}
  .main{padding:10px 12px 80px;width:100%;overflow-x:hidden}
  body{overflow-x:hidden}
  .sidebar{position:fixed;left:0;top:0;height:100%!important;width:80vw;max-width:280px;transform:translateX(-100%);z-index:200;box-shadow:4px 0 32px rgba(44,36,22,.25);transition:transform .28s cubic-bezier(.4,0,.2,1);overflow-y:auto}
  .sidebar.open{transform:translateX(0)!important}
  .sidebar-overlay{display:none;position:fixed;inset:0;background:rgba(44,36,22,.5);z-index:199;backdrop-filter:blur(1px)}
  .sidebar-overlay.open{display:block}
  .mobile-header{display:flex!important;z-index:198;position:sticky;top:0}
  .topbar{flex-wrap:nowrap;gap:6px;margin-bottom:14px}
  .topbar h1{font-size:16px;font-weight:700;white-space:nowrap;min-width:0;overflow:hidden;text-overflow:ellipsis}
  .topbar-right{gap:4px;flex-shrink:0}
  .topbar-right .badge-btn{padding:5px 8px;font-size:11px;border-radius:8px}
  .topbar-right .badge-btn span{display:none}
  .stat-grid{grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:14px}
  .stat-card{padding:12px 10px}
  .stat-value{font-size:24px}
  .two-col{grid-template-columns:1fr!important;gap:10px}
  .card{padding:14px 12px}
  .section-header{flex-direction:column;align-items:flex-start;gap:8px}
  .section-header > div:last-child{width:100%;overflow-x:auto;padding-bottom:4px}
  .filter-tabs{flex-wrap:nowrap;gap:4px}
  .tab-btn{white-space:nowrap;padding:6px 11px;font-size:11px}
  .agent-row-stats{gap:8px}
  .agent-mini-stat:nth-child(4),.agent-mini-stat:nth-child(5){display:none}
  .detail-grid{grid-template-columns:1fr!important}
  .timeline{padding:10px 6px}
  .modal-overlay{padding:0!important;align-items:flex-end!important}
  .modal{border-radius:20px 20px 0 0!important;max-height:88vh!important;max-width:100%!important;border-bottom:none!important;padding:20px 16px 32px!important}
  .report-modal-overlay{padding:0!important;align-items:flex-end!important}
  .report-modal{border-radius:20px 20px 0 0!important;max-width:100%!important}
}
@media(max-width:480px){
  .stat-grid{grid-template-columns:repeat(2,1fr);gap:6px}
  .topbar-right .badge-btn:nth-child(1),.topbar-right .badge-btn:nth-child(2){display:none}
}
@media print{
  .sidebar,.mobile-header,.topbar-right,.report-modal-overlay{display:none!important}
  .main{padding:0}body{background:white;color:black}
}
</style>
</head>
<body>

<div class="mobile-header">
  <div class="mobile-logo">🚛 Kurtex</div>
  <div class="hamburger" onclick="toggleSidebar()"><i class="ph ph-list"></i></div>
</div>
<div class="sidebar-overlay" id="sidebar-overlay" onclick="closeSidebar()"></div>

<div class="layout">
<aside class="sidebar" id="sidebar">
  <div class="sidebar-logo">
    <div style="width:36px;height:36px;border-radius:50%;background:var(--accent-bg);display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0">🚛</div>
    <div class="logo-text"><h2>Kurtex</h2><small>Alert Dashboard</small></div>
  </div>
  <nav>
    <div class="nav-item active" onclick="showPage('overview')"><i class="ph ph-squares-four"></i> Overview</div>
    <div class="nav-item" onclick="showPage('cases')"><i class="ph ph-clipboard-text"></i> Cases</div>
    <div class="nav-item" onclick="showPage('missed')"><i class="ph ph-warning"></i> Missed <span class="nav-badge" id="missed-badge" style="display:none"></span></div>
    <div class="nav-item" onclick="showPage('reassigned')"><i class="ph ph-arrows-clockwise"></i> Reassigned</div>
    <div class="nav-item" onclick="showPage('testing')"><i class="ph ph-flask"></i> Testing</div>
    <div class="nav-item" onclick="showPage('leaderboard')"><i class="ph ph-trophy"></i> Leaderboard</div>
    <div class="nav-group">
      <div class="nav-group-header" onclick="toggleGroup('group-analytics')">
        <span><i class="ph ph-chart-bar"></i> Analytics</span>
        <i class="ph ph-caret-down nav-caret" id="caret-group-analytics"></i>
      </div>
      <div class="nav-group-items" id="group-analytics">
        <div class="nav-item nav-sub" onclick="showPage('trends')"><i class="ph ph-trend-up"></i> Trends</div>
        <div class="nav-item nav-sub" onclick="showPage('heatmap')"><i class="ph ph-squares-four"></i> Heatmap</div>
        <div class="nav-item nav-sub" onclick="showPage('comparison')"><i class="ph ph-arrows-left-right"></i> Comparison</div>
      </div>
    </div>
    <div class="nav-group">
      <div class="nav-group-header" onclick="toggleGroup('group-fleet')">
        <span><i class="ph ph-truck"></i> Fleet</span>
        <i class="ph ph-caret-down nav-caret" id="caret-group-fleet"></i>
      </div>
      <div class="nav-group-items" id="group-fleet">
        <div class="nav-item nav-sub" onclick="showPage('fleet')"><i class="ph ph-wrench"></i> Fleet Stats</div>
        <div class="nav-item nav-sub" onclick="showPage('fleet_intel')"><i class="ph ph-magnifying-glass"></i> Intelligence</div>
      </div>
    </div>
    <div class="nav-item" onclick="showPage('my_profile')"><i class="ph ph-user"></i> My Profile</div>
    {% if is_manager %}<div class="nav-item" onclick="showPage('agents')"><i class="ph ph-users"></i> Agents</div>{% endif %}
  </nav>
  <div class="sidebar-footer">
    <div class="user-chip">
      {% if user.photo_url %}<img class="user-avatar" src="{{ user.photo_url }}" alt="">
      {% else %}<div class="user-avatar-init">{{ user.first_name[0] }}</div>{% endif %}
      <div><div class="user-name">{{ user.first_name }}</div><div class="user-role">{{ user.role if user.role else "Agent" }}</div></div>
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
      <a class="badge-btn" href="/api/export/pdf?period=today" id="pdf-export-btn"><i class="ph ph-file-pdf"></i> <span>PDF</span></a>
      <a class="badge-btn" href="/api/export"><i class="ph ph-download-simple"></i> <span>CSV</span></a>
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
          <select id="status-filter" onchange="loadCases()" style="padding:6px 10px;background:var(--surface);border:1px solid var(--border);border-radius:7px;font-size:12px;color:var(--text);font-family:inherit;outline:none;cursor:pointer">
            <option value="">All Statuses</option>
            <option value="open">Open</option>
            <option value="assigned">Assigned</option>
            <option value="reported">Reported</option>
            <option value="done">Done</option>
            <option value="missed">Missed</option>
          </select>
          <input type="date" id="cases-date-picker" style="padding:5px 10px;background:var(--surface);border:1px solid var(--border);border-radius:7px;font-size:12px;color:var(--text);font-family:inherit;outline:none" onchange="setCaseDateFilter(this.value)">
          <button class="tab-btn" id="cases-date-clear" onclick="clearDateFilter()" style="display:none">✕ Clear</button>
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

  <!-- Testing -->
  <div class="page" id="page-testing">
    <div style="background:var(--yellow-bg);border:1px solid var(--accent);border-radius:10px;padding:12px 16px;margin-bottom:16px;font-size:13px;color:var(--accent)">
      <b>Testing Group</b> — These cases are excluded from all statistics and reports.
    </div>
    <div class="section">
      <div class="section-header"><div class="section-title">Testing Cases</div></div>
      <div class="table-wrap"><div class="table-scroll" id="testing-table"><div class="loading">Loading...</div></div></div>
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

  <!-- Trends -->
  <div class="page" id="page-trends">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;flex-wrap:wrap">
      <div class="toggle-tabs" style="margin-bottom:0">
        <button class="toggle-btn active" onclick="setTrendPeriod(7,this)">7 Days</button>
        <button class="toggle-btn" onclick="setTrendPeriod(30,this)">30 Days</button>
        <button class="toggle-btn" onclick="setTrendPeriod(90,this)">90 Days</button>
      </div>
    </div>
    <div class="two-col" style="margin-bottom:16px">
      <div class="card"><div class="card-title"><i class="ph ph-chart-line"></i> Cases Over Time</div><canvas id="trend-cases-chart" height="180"></canvas></div>
      <div class="card"><div class="card-title"><i class="ph ph-timer"></i> Avg Response Time</div><canvas id="trend-resp-chart" height="180"></canvas></div>
    </div>
    <div class="card"><div class="card-title"><i class="ph ph-chart-bar"></i> Daily Breakdown</div><canvas id="trend-bar-chart" height="120"></canvas></div>
  </div>

  <!-- Heatmap -->
  <div class="page" id="page-heatmap">
    <div class="card">
      <div class="card-title"><i class="ph ph-squares-four"></i> Cases by Day & Hour (Eastern Time)</div>
      <div id="heatmap-container" style="overflow-x:auto"></div>
    </div>
  </div>

  <!-- Comparison -->
  <div class="page" id="page-comparison">
    <div id="comparison-content"><div class="loading">Loading...</div></div>
  </div>

  <!-- Fleet -->
  <div class="page" id="page-fleet">
    <div id="fleet-content"><div class="loading">Loading fleet stats...</div></div>
  </div>

  <!-- Fleet Intel -->
  <div class="page" id="page-fleet_intel">
    <div id="fleet-intel-content"><div class="loading">Loading...</div></div>
  </div>

  <!-- My Profile -->
  <div class="page" id="page-my_profile">
    <div id="my-profile-content"><div class="loading">Loading...</div></div>
  </div>

  <!-- Agents -->
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

<!-- Report View Modal -->
<div class="modal-overlay" id="report-view-overlay" style="z-index:500" onclick="if(event.target===this)closeReportView()">
<div class="modal" style="max-width:640px">
  <button class="modal-close" onclick="closeReportView()"><i class="ph ph-x"></i></button>
  <h2 id="report-view-title">Case Report</h2>
  <div id="report-view-body"><div class="loading">Loading...</div></div>
  <div style="margin-top:16px;text-align:right">
    <button onclick="printReportView()" style="background:var(--accent);color:#fff;border:none;border-radius:8px;padding:8px 16px;font-size:12px;font-weight:600;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:6px"><i class="ph ph-printer"></i> Print</button>
  </div>
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
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <a id="report-pdf-btn" href="/api/export/pdf?period=today" class="print-report-btn" style="text-decoration:none"><i class="ph ph-file-pdf"></i> Export PDF</a>
      <button class="print-report-btn" onclick="printReport()" style="background:var(--surface2);color:var(--text);border:1px solid var(--border)"><i class="ph ph-printer"></i> Print</button>
    </div>
  </div>
</div>
</div>

<script>
// ── State ──────────────────────────────────────────────────────────────────
var stats = {};
var currentFilter = 'today';
var currentPage = 'overview';
var lbPeriod = 'day';
var reportTab = 'today';
var currentDateFilter = '';
var searchTimers = {};
var isDark = localStorage.getItem('kurtex-theme') === 'dark';
var pages = ['overview','cases','missed','reassigned','testing','leaderboard','trends','heatmap','comparison','fleet','fleet_intel','my_profile','agents'];
var titles = {overview:'Overview',cases:'Cases',missed:'Missed Cases',reassigned:'Reassigned Cases',testing:'Testing',leaderboard:'Leaderboard',trends:'Trends',heatmap:'Activity Heatmap',comparison:'Week Comparison',fleet:'Fleet Stats',fleet_intel:'Fleet Intelligence',my_profile:'My Profile',agents:'Agent Profiles'};
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
  var sb = document.getElementById('sidebar');
  var ov = document.getElementById('sidebar-overlay');
  var isOpen = sb.classList.contains('open');
  if (isOpen) { sb.classList.remove('open'); ov.classList.remove('open'); document.body.style.overflow = ''; }
  else { sb.classList.add('open'); ov.classList.add('open'); document.body.style.overflow = 'hidden'; }
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebar-overlay').classList.remove('open');
  document.body.style.overflow = '';
}

// ── Navigation ─────────────────────────────────────────────────────────────
function showPage(page) {
  closeSidebar();
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
  localStorage.setItem('kurtex-page', page);
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
function onSearch(type) {
  clearTimeout(searchTimers[type]);
  searchTimers[type] = setTimeout(function(){
    if (type === 'cases') loadCases();
    else if (type === 'missed') loadMissed();
  }, 300);
}

// ── Nav Groups ─────────────────────────────────────────────────────────────
function toggleGroup(id) {
  var el = document.getElementById(id);
  var caret = document.getElementById('caret-' + id);
  var open = el.classList.contains('open');
  el.classList.toggle('open', !open);
  if (caret) caret.classList.toggle('open', !open);
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
  var steps = [{label:'Open',time:c.opened||''},{label:'Assigned',time:c.assigned_at||''},{label:'Reported',time:''},{label:'Resolved',time:c.closed||''}];
  var order = ['open','assigned','reported','done','missed'];
  var si = Math.max(0, order.indexOf(c.status));
  var html = '<div class="timeline">';
  steps.forEach(function(s, i) {
    var isDone = i < si;
    var isActive = i === si;
    var dotClass = isDone ? 'done' : isActive ? 'active' : '';
    html += '<div class="tl-step' + (isDone?' done-step':'') + '">'
      + '<div class="tl-dot ' + dotClass + '">' + (isDone?'✓':(i+1)) + '</div>'
      + '<div class="tl-label">' + s.label + '</div>'
      + '<div class="tl-time">' + (s.time&&s.time!=='—'?s.time:'') + '</div>'
      + '</div>';
  });
  return html + '</div>';
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
      + '<div class="stat-card"><div class="stat-label">Assigned</div><div class="stat-value v-yellow">' + (t.assigned||0) + '</div></div>'
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
    ? lb.map(function(a,i){return '<div class="list-row"><span class="medal">'+(medals[i]||(i+1)+'.')+'</span><span class="list-name">'+a.name+'</span><span class="list-count">'+a.count+'</span></div>';}).join('')
    : '<div style="color:var(--muted);font-size:13px;padding:8px 0">No data</div>';
}

async function loadCases() {
  var el = document.getElementById('cases-table');
  if (!el) return;
  el.innerHTML = '<div class="loading">Loading...</div>';
  try {
    var search = (document.getElementById('cases-search')||{}).value||'';
    var statusF = (document.getElementById('status-filter')||{}).value||'';
    var url = currentFilter === '__date__'
      ? '/api/cases?date='+currentDateFilter+'&search='+encodeURIComponent(search)+'&status='+statusF
      : '/api/cases?filter='+currentFilter+'&search='+encodeURIComponent(search)+'&status='+statusF;
    var r = await fetch(url);
    if (!r.ok) return;
    el.innerHTML = caseTable(await r.json());
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
async function loadTesting() {
  var el = document.getElementById('testing-table');
  if (!el) return;
  try {
    var r = await fetch('/api/cases?filter=testing');
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
    if (!r.ok) { el.innerHTML = '<div class="loading">Error.</div>'; return; }
    var d = await r.json();
    function unitCard(title, items) {
      if (!items||!items.length) return '<div class="card"><div class="card-title">'+title+'</div><div style="color:var(--muted);font-size:13px">No data yet</div></div>';
      var max = items[0].count||1;
      return '<div class="card"><div class="card-title">'+title+'</div>'
        + items.map(function(item,i){ return '<div class="list-row"><span class="medal">'+(medals[i]||(i+1)+'.')+'</span><span class="list-name">'+item.unit+(item.vtype?' <span style="font-size:10px;color:var(--muted)">'+item.vtype+'</span>':'')+'</span><div class="bar-wrap"><div class="bar-fill" style="width:'+Math.round(item.count/max*100)+'%"></div></div><span class="list-count">'+item.count+'</span></div>'; }).join('')
        + '</div>';
    }
    el.innerHTML = '<div class="stat-grid" style="margin-bottom:20px">'
      + '<div class="stat-card"><div class="stat-label">Total Reports</div><div class="stat-value v-accent">'+d.total_reports+'</div></div>'
      + '<div class="stat-card"><div class="stat-label">Trucks</div><div class="stat-value v-blue">'+d.truck_count+'</div></div>'
      + '<div class="stat-card"><div class="stat-label">Trailers</div><div class="stat-value v-yellow">'+d.trailer_count+'</div></div>'
      + '<div class="stat-card"><div class="stat-label">Reefers</div><div class="stat-value v-purple">'+d.reefer_count+'</div></div>'
      + '</div>'
      + '<div class="two-col" style="margin-bottom:16px">'+unitCard('<i class="ph ph-truck"></i> Most Reported Units',d.top_units)+unitCard('<i class="ph ph-user"></i> Most Reported Drivers',d.top_drivers)+'</div>'
      + '<div class="two-col">'+unitCard('<i class="ph ph-warning"></i> Top Issues',d.top_issues)+unitCard('<i class="ph ph-package"></i> Load Types',d.load_types)+'</div>';
  } catch(e) { console.error(e); el.innerHTML = '<div class="loading">Error.</div>'; }
}

async function loadMyProfile() {
  var el = document.getElementById('my-profile-content');
  if (!el) return;
  el.innerHTML = '<div class="loading">Loading...</div>';
  try {
    var r = await fetch('/api/my_profile');
    if (!r.ok) { el.innerHTML = '<div class="loading">Error.</div>'; return; }
    var p = await r.json();
    el.innerHTML =
      '<div class="two-col" style="margin-bottom:16px">'
      + '<div class="card"><div style="display:flex;align-items:center;gap:14px;margin-bottom:16px"><div style="width:52px;height:52px;border-radius:50%;background:var(--accent-bg);display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:700;color:var(--accent);flex-shrink:0">'+p.name[0]+'</div><div><div style="font-size:17px;font-weight:700">'+p.name+'</div><div style="font-size:12px;color:var(--muted)">'+(p.username?'@'+p.username+' · ':'')+p.role+'</div></div></div>'
      + '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px"><div class="agent-stat"><div class="agent-stat-val" style="color:var(--accent)">'+p.total+'</div><div class="agent-stat-label">Total</div></div><div class="agent-stat"><div class="agent-stat-val" style="color:var(--green)">'+p.done+'</div><div class="agent-stat-label">Resolved</div></div><div class="agent-stat"><div class="agent-stat-val" style="color:var(--red)">'+p.missed+'</div><div class="agent-stat-label">Missed</div></div><div class="agent-stat"><div class="agent-stat-val" style="color:var(--accent)">'+p.rate+'%</div><div class="agent-stat-label">Rate</div></div></div></div>'
      + '<div class="card"><div class="card-title">Period Breakdown</div><div class="stats-list"><div class="row"><span>Today assigned</span><span class="val">'+p.today_total+'</span></div><div class="row"><span>Today resolved</span><span class="val" style="color:var(--green)">'+p.today_done+'</span></div><div class="row"><span>Week assigned</span><span class="val">'+p.week_total+'</span></div><div class="row"><span>Week resolved</span><span class="val" style="color:var(--green)">'+p.week_done+'</span></div><div class="row"><span>Avg response</span><span class="val">'+p.avg_resp+'</span></div></div></div>'
      + '</div>'
      + '<div class="section-title" style="margin-bottom:10px">Recent Cases</div>'
      + '<div class="table-wrap"><div class="table-scroll">'+caseTable(p.recent)+'</div></div>';
  } catch(e) { console.error(e); el.innerHTML = '<div class="loading">Error.</div>'; }
}

// ── Agents — expandable accordion list ────────────────────────────────────
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
    window._agentsData = agents;
    var totalAll = agents.reduce(function(s,a){return s+(a.total||0);},0);
    var html = '<div style="margin-bottom:12px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">'
      + '<div style="font-size:12px;color:var(--muted)">'+agents.length+' agents &middot; '+totalAll+' total cases</div>'
      + '<div class="filter-tabs"><button onclick="sortAgentsList(\'total\',this)" class="tab-btn active" style="font-size:11px;padding:4px 10px">By Cases</button><button onclick="sortAgentsList(\'rate\',this)" class="tab-btn" style="font-size:11px;padding:4px 10px">By Rate</button><button onclick="sortAgentsList(\'name\',this)" class="tab-btn" style="font-size:11px;padding:4px 10px">A&ndash;Z</button></div>'
      + '</div>'
      + '<div id="agents-list">'+buildAgentsList(agents,'total')+'</div>';
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<div class="loading">Error: '+e.message+'</div>'; }
}

function buildAgentsList(agents, sortBy) {
  var sorted = agents.slice().sort(function(a,b){
    if (sortBy==='rate') return (b.rate||0)-(a.rate||0);
    if (sortBy==='name') return (a.name||'').localeCompare(b.name||'');
    return (b.total||0)-(a.total||0);
  });
  return sorted.map(function(a, idx) {
    var init = (a.name||'?')[0].toUpperCase();
    var rate = a.rate||0;
    var rateColor = rate>=80?'var(--green)':rate>=60?'var(--yellow)':'var(--red)';
    var enc = encodeURIComponent(a.name||'');
    return '<div class="agent-row" id="agent-row-'+idx+'">'
      +'<div class="agent-row-header" onclick="toggleAgentRow('+idx+',\''+enc+'\')">'
        +'<div class="agent-row-left">'
          +'<div class="agent-avatar">'+init+'</div>'
          +'<div><div style="font-size:13px;font-weight:600;color:var(--text)">'+a.name+'</div>'
          +'<div style="font-size:11px;color:var(--muted)">'+(a.username?'@'+a.username:'&mdash;')+'</div></div>'
        +'</div>'
        +'<div class="agent-row-stats">'
          +'<div class="agent-mini-stat"><span style="font-size:15px;font-weight:700;color:var(--accent)">'+(a.total||0)+'</span><span style="font-size:9px;color:var(--muted);display:block;margin-top:1px">total</span></div>'
          +'<div class="agent-mini-stat"><span style="font-size:15px;font-weight:700;color:var(--green)">'+(a.done||0)+'</span><span style="font-size:9px;color:var(--muted);display:block;margin-top:1px">done</span></div>'
          +'<div class="agent-mini-stat"><span style="font-size:15px;font-weight:700;color:var(--red)">'+(a.missed||0)+'</span><span style="font-size:9px;color:var(--muted);display:block;margin-top:1px">missed</span></div>'
          +'<div class="agent-mini-stat" style="min-width:56px"><div style="background:var(--surface3);border-radius:3px;height:4px;margin-bottom:4px"><div style="background:'+rateColor+';border-radius:3px;height:100%;width:'+rate+'%"></div></div><span style="font-size:11px;font-weight:700;color:'+rateColor+'">'+rate+'%</span></div>'
          +'<div class="agent-mini-stat" style="min-width:44px"><span style="font-size:11px;font-weight:600;color:var(--text)">'+(a.avg_resp||'&mdash;')+'</span><span style="font-size:9px;color:var(--muted);display:block;margin-top:1px">avg</span></div>'
          +'<div class="agent-row-caret" id="agent-caret-'+idx+'"><i class="ph ph-caret-down"></i></div>'
        +'</div>'
      +'</div>'
      +'<div class="agent-row-detail" id="agent-detail-'+idx+'"></div>'
    +'</div>';
  }).join('');
}

function sortAgentsList(by, btn) {
  if (!window._agentsData) return;
  document.querySelectorAll('#agents-content .tab-btn').forEach(function(b){b.classList.remove('active');});
  btn.classList.add('active');
  var list = document.getElementById('agents-list');
  if (list) list.innerHTML = buildAgentsList(window._agentsData, by);
}

async function toggleAgentRow(idx, encodedName) {
  var detail = document.getElementById('agent-detail-'+idx);
  var caret  = document.getElementById('agent-caret-'+idx);
  if (!detail) return;
  var isOpen = detail.classList.contains('open');
  if (isOpen) { detail.classList.remove('open'); if (caret) caret.classList.remove('open'); return; }
  detail.classList.add('open');
  if (caret) caret.classList.add('open');
  if (detail.dataset.loaded) return;
  detail.dataset.loaded = '1';
  detail.innerHTML = '<div style="padding:12px 16px;color:var(--muted);font-size:12px">Loading...</div>';
  var agentName = decodeURIComponent(encodedName);
  try {
    var r = await fetch('/api/agent?name='+encodeURIComponent(agentName));
    if (!r.ok) { detail.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px">Error loading data.</div>'; return; }
    var a = await r.json();
    var recentRows = (a.recent||[]).map(function(c){
      var cid = c.full_id||'';
      return '<tr data-id="'+cid+'" onclick="var id=this.dataset.id;document.getElementById(\'agent-detail-'+idx+'\').classList.remove(\'open\');document.getElementById(\'agent-caret-'+idx+'\').classList.remove(\'open\');setTimeout(function(){openCase(id);},150)" style="cursor:pointer">'
        +'<td><b>'+(c.driver||'—')+'</b></td>'
        +'<td style="color:var(--muted)">'+(c.group||'—')+'</td>'
        +'<td>'+statusBadge(c.status)+'</td>'
        +'<td style="color:var(--muted);font-size:11px">'+(c.opened||'—')+'</td>'
        +'<td style="font-size:11px">'+(c.response||'—')+'</td>'
        +'</tr>';
    }).join('');
    detail.innerHTML =
      '<div style="padding:14px 16px 16px">'
        +'<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:14px">'
          +'<div class="agent-exp-stat"><div class="aes-val" style="color:var(--accent)">'+(a.total||0)+'</div><div class="aes-lbl">All Cases</div></div>'
          +'<div class="agent-exp-stat"><div class="aes-val" style="color:var(--green)">'+(a.done||0)+'</div><div class="aes-lbl">Resolved</div></div>'
          +'<div class="agent-exp-stat"><div class="aes-val" style="color:var(--red)">'+(a.missed||0)+'</div><div class="aes-lbl">Missed</div></div>'
          +'<div class="agent-exp-stat"><div class="aes-val" style="color:var(--accent)">'+(a.rate||0)+'%</div><div class="aes-lbl">Rate</div></div>'
          +'<div class="agent-exp-stat"><div class="aes-val" style="font-size:12px;color:var(--text)">'+(a.avg_resp||'—')+'</div><div class="aes-lbl">Avg Resp</div></div>'
        +'</div>'
        +(recentRows
          ? '<div style="font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">Recent Cases</div>'
            +'<div class="table-wrap"><div class="table-scroll"><table>'
            +'<thead><tr><th>Reported By</th><th>Group</th><th>Status</th><th>Opened</th><th>Response</th></tr></thead>'
            +'<tbody>'+recentRows+'</tbody></table></div></div>'
          : '<div style="color:var(--muted);font-size:12px;padding:4px 0">No cases yet.</div>')
      +'</div>';
  } catch(e) {
    detail.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px">Error: '+e.message+'</div>';
  }
}

// ── Case Modal ─────────────────────────────────────────────────────────────
async function openCase(caseId) {
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
      + (c.full_notes ? '<div class="notes-box"><span class="box-label">Report / Notes</span><p class="box-text">'+c.full_notes+'</p></div>' : '')
      + ((c.status === 'reported' || c.status === 'done')
        ? '<div style="margin-top:14px;text-align:center"><button data-id="' + c.full_id + '" onclick="viewFullReport(this.dataset.id)" style="background:var(--accent);color:#fff;border:none;border-radius:10px;padding:10px 24px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit">View Full Report</button></div>'
        : '');
  } catch(e) {
    document.getElementById('modal-body').innerHTML = '<div class="loading">Error loading case.</div>';
  }
}
function closeModal() { document.getElementById('modal-overlay').classList.remove('open'); }

async function viewFullReport(caseId) {
  document.getElementById('report-view-overlay').classList.add('open');
  document.getElementById('report-view-body').innerHTML = '<div class="loading">Loading report...</div>';
  try {
    var r = await fetch('/api/case?id='+encodeURIComponent(caseId));
    if (!r.ok) { document.getElementById('report-view-body').innerHTML = '<div class="loading">Error.</div>'; return; }
    var c = await r.json();
    document.getElementById('report-view-title').textContent = 'Report — ' + (c.driver||'—') + ' / ' + (c.group||'—');
    var vtype = c.vehicle_type || '';
    var vtypeLabel = vtype === 'truck' ? 'Truck' : vtype === 'trailer' ? 'Trailer' : vtype === 'reefer' ? 'Reefer' : '';
    var pIcons = {critical:'🔴',high:'🟠',medium:'🟡',low:'🟢'};
    var pIcon = pIcons[c.priority] || '🟢';
    var pText = c.priority ? c.priority.charAt(0).toUpperCase()+c.priority.slice(1) : 'Low';
    function line(label, val) {
      if (!val || val === '—') return '';
      return '<div style="font-size:13px;margin-bottom:6px"><b>' + label + ':</b> ' + val + '</div>';
    }
    var s = '<div style="font-size:15px;font-weight:700;margin-bottom:2px">'+pIcon+' Case Report'+(vtypeLabel?' — '+vtypeLabel:'')+'</div>';
    s += '<div style="font-size:13px;margin-bottom:16px">Priority: <b>'+pText+'</b></div>';
    s += '<div style="margin-bottom:16px">';
    if (vtypeLabel && c.unit_number) s += line(vtypeLabel, c.unit_number);
    s += line('Reported by', c.report_driver || c.driver);
    s += line('Issue', c.issue_text || c.full_description);
    s += '</div>';
    if (c.load_type) {
      s += '<div style="margin-bottom:16px">';
      s += line('Load Type', c.load_type);
      if (c.load_type.toLowerCase() !== 'empty') {
        s += line('Pick up', c.pickup);
        s += line('Delivery', c.delivery);
      }
      s += '</div>';
    }
    if (vtype === 'reefer') {
      s += '<div style="margin-bottom:16px">';
      s += line('Setpoint', c.setpoint);
      s += line('Current temp', c.current_temp);
      s += line('Temp recorder', c.temp_recorder);
      s += '</div>';
    }
    if (c.comments) s += '<div style="margin-bottom:16px">'+line('Comments', c.comments)+'</div>';
    s += line('Handled by', c.agent);
    document.getElementById('report-view-body').innerHTML =
      '<div style="background:var(--surface2);border-radius:12px;padding:18px 20px;font-family:inherit;line-height:1.6">'+s+'</div>';
  } catch(e) {
    document.getElementById('report-view-body').innerHTML = '<div class="loading">Error.</div>';
  }
}
function closeReportView() { document.getElementById('report-view-overlay').classList.remove('open'); }
function printReportView() {
  var orig = document.title;
  document.title = document.getElementById('report-view-title').textContent;
  window.print(); document.title = orig;
}

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
    } else { url = '/api/report?period='+period; }
  }
  try {
    var pdfBtn = document.getElementById('report-pdf-btn');
    if (pdfBtn) pdfBtn.href = url.replace('/api/report','/api/export/pdf');
    var r = await fetch(url);
    if (!r.ok) { document.getElementById('report-content').innerHTML = '<div class="loading">Error.</div>'; return; }
    var d = await r.json();
    document.getElementById('report-ts').textContent = 'Generated ' + new Date().toLocaleString();
    var now = new Date();
    var resRate = d.total ? Math.round((d.total-d.missed)/d.total*100) : 0;
    document.getElementById('report-content').innerHTML =
      '<div style="border-bottom:2px solid var(--accent);padding-bottom:14px;margin-bottom:18px">'
      +'<div style="font-size:20px;font-weight:800">'+d.label+'</div>'
      +'<div style="font-size:11px;color:var(--muted);margin-top:2px">Kurtex Truck Maintenance — Operations Report</div>'
      +'</div>'
      +'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:18px">'
      +'<div style="background:var(--surface2);border-radius:10px;padding:14px;text-align:center"><div style="font-size:28px;font-weight:800;color:var(--accent)">'+d.total+'</div><div style="font-size:10px;color:var(--muted);text-transform:uppercase;font-weight:600;margin-top:3px">Total</div></div>'
      +'<div style="background:var(--surface2);border-radius:10px;padding:14px;text-align:center"><div style="font-size:28px;font-weight:800;color:var(--green)">'+d.done+'</div><div style="font-size:10px;color:var(--muted);text-transform:uppercase;font-weight:600;margin-top:3px">Resolved</div></div>'
      +'<div style="background:var(--surface2);border-radius:10px;padding:14px;text-align:center"><div style="font-size:28px;font-weight:800;color:var(--red)">'+d.missed+'</div><div style="font-size:10px;color:var(--muted);text-transform:uppercase;font-weight:600;margin-top:3px">Missed</div></div>'
      +'</div>'
      +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:18px">'
      +'<div style="background:var(--surface2);border-radius:10px;padding:12px 16px;display:flex;align-items:center;justify-content:space-between"><span style="font-size:12px;color:var(--muted)">Resolution Rate</span><span style="font-size:18px;font-weight:800;color:'+(resRate>=80?'var(--green)':resRate>=60?'var(--yellow)':'var(--red)')+'">'+resRate+'%</span></div>'
      +'<div style="background:var(--surface2);border-radius:10px;padding:12px 16px;display:flex;align-items:center;justify-content:space-between"><span style="font-size:12px;color:var(--muted)">Avg Response</span><span style="font-size:18px;font-weight:800;color:var(--text)">'+d.avg_resp+'</span></div>'
      +'</div>'
      +(d.leaderboard.length ? '<div style="margin-bottom:18px"><div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--border)">Agent Performance</div>'
        +d.leaderboard.map(function(a,i){return '<div class="list-row"><span class="medal">'+(medals[i]||(i+1)+'.')+'</span><span class="list-name">'+a.name+'</span><span class="list-count">'+a.count+'</span></div>';}).join('')+'</div>' : '')
      +(d.missed_cases.length ? '<div><div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--red);margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--border)">Missed Cases ('+d.missed+')</div>'
        +'<div class="table-wrap"><div class="table-scroll"><table><thead><tr><th>Driver</th><th>Group</th><th>Opened</th></tr></thead><tbody>'
        +d.missed_cases.map(function(c){return '<tr><td>'+c.driver+'</td><td style="color:var(--muted)">'+c.group+'</td><td style="color:var(--muted);font-size:11px">'+c.opened+'</td></tr>';}).join('')
        +'</tbody></table></div></div></div>' : '');
  } catch(e) { document.getElementById('report-content').innerHTML = '<div class="loading">Error.</div>'; }
}
function printReport() {
  var orig = document.title;
  document.title = 'Kurtex Report — ' + new Date().toLocaleDateString();
  window.print(); document.title = orig;
}

// ── Trends ────────────────────────────────────────────────────────────────
var trendPeriod = 30;
var trendCharts = {};
function setTrendPeriod(days, btn) {
  trendPeriod = days;
  document.querySelectorAll('#page-trends .toggle-btn').forEach(function(b){b.classList.remove('active');});
  btn.classList.add('active');
  loadTrends();
}
async function loadTrends() {
  try {
    var r = await fetch('/api/trends?period=' + trendPeriod);
    if (!r.ok) return;
    var d = await r.json();
    Object.values(trendCharts).forEach(function(c){ if(c) c.destroy(); });
    trendCharts = {};
    var accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
    var green  = getComputedStyle(document.documentElement).getPropertyValue('--green').trim();
    var red    = getComputedStyle(document.documentElement).getPropertyValue('--red').trim();
    var muted  = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim();
    var ctx1 = document.getElementById('trend-cases-chart').getContext('2d');
    trendCharts.cases = new Chart(ctx1, {type:'line',data:{labels:d.labels,datasets:[{label:'Total',data:d.totals,borderColor:accent,backgroundColor:accent+'22',fill:true,tension:.4,pointRadius:2},{label:'Resolved',data:d.resolved,borderColor:green,backgroundColor:'transparent',tension:.4,pointRadius:2},{label:'Missed',data:d.missed,borderColor:red,backgroundColor:'transparent',tension:.4,pointRadius:2}]},options:{responsive:true,plugins:{legend:{labels:{color:muted,font:{size:11}}}},scales:{x:{ticks:{color:muted,font:{size:10},maxTicksLimit:10}},y:{ticks:{color:muted,font:{size:10}},beginAtZero:true}}}});
    var ctx2 = document.getElementById('trend-resp-chart').getContext('2d');
    trendCharts.resp = new Chart(ctx2, {type:'line',data:{labels:d.labels,datasets:[{label:'Avg Resp (secs)',data:d.avg_resp,borderColor:accent,backgroundColor:accent+'22',fill:true,tension:.4,pointRadius:2}]},options:{responsive:true,plugins:{legend:{labels:{color:muted,font:{size:11}}}},scales:{x:{ticks:{color:muted,font:{size:10},maxTicksLimit:10}},y:{ticks:{color:muted,font:{size:10}},beginAtZero:true}}}});
    var ctx3 = document.getElementById('trend-bar-chart').getContext('2d');
    trendCharts.bar = new Chart(ctx3, {type:'bar',data:{labels:d.labels,datasets:[{label:'Total',data:d.totals,backgroundColor:accent+'88'},{label:'Resolved',data:d.resolved,backgroundColor:green+'88'},{label:'Missed',data:d.missed,backgroundColor:red+'88'}]},options:{responsive:true,plugins:{legend:{labels:{color:muted,font:{size:11}}}},scales:{x:{ticks:{color:muted,font:{size:10},maxTicksLimit:15}},y:{beginAtZero:true,ticks:{color:muted,font:{size:10}}}}}});
  } catch(e) { console.error('trends error:', e); }
}

// ── Heatmap ───────────────────────────────────────────────────────────────
async function loadHeatmap() {
  var el = document.getElementById('heatmap-container');
  el.innerHTML = '<div class="loading">Loading heatmap...</div>';
  try {
    var r = await fetch('/api/heatmap');
    var d = await r.json();
    var hours = [];
    for (var i=0; i<24; i++) hours.push(i===0?'12am':i<12?i+'am':i===12?'12pm':(i-12)+'pm');
    var html = '<table style="border-collapse:collapse;font-size:11px;width:100%"><thead><tr><th style="padding:4px 6px;color:var(--muted);text-align:left;width:32px"></th>';
    hours.forEach(function(h){ html += '<th style="padding:2px 1px;color:var(--muted);font-weight:500;text-align:center;min-width:28px">'+h+'</th>'; });
    html += '</tr></thead><tbody>';
    d.days.forEach(function(day, di) {
      html += '<tr><td style="padding:3px 6px;color:var(--muted);font-weight:600;white-space:nowrap">'+day+'</td>';
      d.heatmap[di].forEach(function(val) {
        var intensity = val / d.max;
        var alpha = val === 0 ? 0.04 : 0.1 + intensity * 0.85;
        var bg = 'rgba(193,123,63,' + alpha.toFixed(2) + ')';
        var color = intensity > 0.5 ? '#fff' : 'var(--text)';
        html += '<td style="padding:3px 1px;text-align:center"><div style="background:'+bg+';color:'+color+';border-radius:4px;padding:5px 2px;font-weight:'+(val>0?'600':'400')+'">'+( val>0?val:'')+'</div></td>';
      });
      html += '</tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<div class="loading">Error loading heatmap.</div>'; }
}

// ── Comparison ────────────────────────────────────────────────────────────
async function loadComparison() {
  var el = document.getElementById('comparison-content');
  el.innerHTML = '<div class="loading">Loading comparison...</div>';
  try {
    var r = await fetch('/api/comparison');
    var d = await r.json();
    function deltaHtml(delta) {
      if (!delta || delta.pct === 0) return '<span style="color:var(--muted);font-size:11px">—</span>';
      var color = delta.up ? 'var(--green)' : 'var(--red)';
      return '<span style="color:'+color+';font-size:12px;font-weight:700">'+(delta.up?'↑':'↓')+' '+delta.pct+'%</span>';
    }
    function compRow(label, thisVal, lastVal, delta, unit) {
      unit = unit || '';
      return '<tr style="border-bottom:1px solid var(--border)">'
        +'<td style="padding:12px 14px;font-size:13px;color:var(--muted)">'+label+'</td>'
        +'<td style="padding:12px 14px;font-size:18px;font-weight:800;color:var(--text);text-align:center">'+thisVal+unit+'</td>'
        +'<td style="padding:12px 14px;font-size:16px;font-weight:600;color:var(--muted2);text-align:center">'+lastVal+unit+'</td>'
        +'<td style="padding:12px 14px;text-align:center">'+deltaHtml(delta)+'</td>'
        +'</tr>';
    }
    el.innerHTML = '<div class="card"><table style="width:100%;border-collapse:collapse"><thead><tr style="background:var(--surface2)">'
      +'<th style="padding:10px 14px;text-align:left;font-size:11px;color:var(--muted);font-weight:600;text-transform:uppercase">Metric</th>'
      +'<th style="padding:10px 14px;text-align:center;font-size:11px;color:var(--accent);font-weight:700;text-transform:uppercase">This Week</th>'
      +'<th style="padding:10px 14px;text-align:center;font-size:11px;color:var(--muted);font-weight:600;text-transform:uppercase">Last Week</th>'
      +'<th style="padding:10px 14px;text-align:center;font-size:11px;color:var(--muted);font-weight:600;text-transform:uppercase">Change</th>'
      +'</tr></thead><tbody>'
      +compRow('Total Cases',d.this_week.total,d.last_week.total,d.delta_total)
      +compRow('Resolved',d.this_week.done,d.last_week.done,d.delta_done)
      +compRow('Missed',d.this_week.missed,d.last_week.missed,d.delta_missed)
      +compRow('Resolution Rate',d.this_week.rate,d.last_week.rate,d.delta_rate,'%')
      +compRow('Avg Response',d.this_week.avg_resp,d.last_week.avg_resp,d.delta_resp)
      +'</tbody></table></div>';
  } catch(e) { el.innerHTML = '<div class="loading">Error.</div>'; }
}

// ── Fleet Intelligence ────────────────────────────────────────────────────
async function loadFleetIntel() {
  var el = document.getElementById('fleet-intel-content');
  el.innerHTML = '<div class="loading">Loading...</div>';
  try {
    var r = await fetch('/api/fleet_intelligence');
    var d = await r.json();
    var unitsHtml = '<div class="table-wrap"><div class="table-scroll"><table>'
      +'<thead><tr><th>Unit #</th><th>Type</th><th>Reports</th><th>Top Issue</th><th>Last Seen</th></tr></thead><tbody>'
      +(d.top_units.length ? d.top_units.map(function(u){
        return '<tr><td><b>'+u.unit+'</b></td><td><span style="background:var(--accent-bg);color:var(--accent);padding:2px 8px;border-radius:20px;font-size:10px;font-weight:600">'+u.vtype+'</span></td><td><b style="color:var(--accent)">'+u.total+'</b></td><td style="color:var(--muted);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+u.top_issue+'</td><td style="color:var(--muted);font-size:11px">'+u.last_seen+'</td></tr>';
      }).join('') : '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:20px">No data yet</td></tr>')
      +'</tbody></table></div></div>';
    var driversHtml = '<div class="table-wrap"><div class="table-scroll"><table>'
      +'<thead><tr><th>Driver</th><th>Reports</th><th>Most Common Issue</th></tr></thead><tbody>'
      +(d.top_drivers.length ? d.top_drivers.map(function(dr,i){
        return '<tr><td><span style="margin-right:6px">'+(i<3?['🥇','🥈','🥉'][i]:(i+1)+'.')+'</span><b>'+dr.name+'</b></td><td><b style="color:var(--accent)">'+dr.total+'</b></td><td style="color:var(--muted)">'+dr.top_issue+'</td></tr>';
      }).join('') : '<tr><td colspan="3" style="text-align:center;color:var(--muted);padding:20px">No data yet</td></tr>')
      +'</tbody></table></div></div>';
    el.innerHTML = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px">'
      +'<div class="stat-card"><div class="stat-label">Total Reports</div><div class="stat-value v-accent">'+d.total_reports+'</div></div>'
      +'<div class="stat-card"><div class="stat-label">Unique Units</div><div class="stat-value v-blue">'+d.top_units.length+'</div></div>'
      +'</div>'
      +'<div class="section-title" style="margin-bottom:10px">Most Reported Units</div>'+unitsHtml
      +'<div class="section-title" style="margin:16px 0 10px">Most Reported Drivers</div>'+driversHtml;
  } catch(e) { el.innerHTML = '<div class="loading">Error.</div>'; }
}

// ── Refresh ───────────────────────────────────────────────────────────────
async function refresh() {
  await loadStats();
  if (currentPage==='overview') {
    try {
      var r = await fetch('/api/cases?filter=today');
      if (r.ok) { var cases = await r.json(); var el = document.getElementById('recent-table'); if (el) el.innerHTML = caseTable(cases.slice(0,10)); }
    } catch(e) {}
  } else if (currentPage==='cases') loadCases();
  else if (currentPage==='missed') loadMissed();
  else if (currentPage==='reassigned') loadReassigned();
  else if (currentPage==='testing') loadTesting();
  else if (currentPage==='fleet') loadFleet();
  else if (currentPage==='trends') loadTrends();
  else if (currentPage==='heatmap') loadHeatmap();
  else if (currentPage==='comparison') loadComparison();
  else if (currentPage==='fleet_intel') loadFleetIntel();
  else if (currentPage==='my_profile') loadMyProfile();
  else if (currentPage==='agents') loadAgents();
  var lu = document.getElementById('last-update');
  if (lu) lu.textContent = 'Updated ' + new Date().toLocaleTimeString();
}

try {
  var savedPage = localStorage.getItem('kurtex-page');
  if (savedPage && pages.indexOf(savedPage) >= 0) showPage(savedPage);
  else refresh();
} catch(e) { refresh(); }
setInterval(refresh, 30000);
</script>
"""



@app.route("/api/export/pdf")
def api_export_pdf():
    if not session.get("user"): return redirect("/login")
    try:
        import io, base64
        from reportlab.lib.pagesizes import letter, landscape
        from reportlab.lib import colors
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

        ACCENT   = colors.HexColor("#C17B3F")
        BG_LIGHT = colors.HexColor("#FAF8F5")
        BG_ROW   = colors.HexColor("#F3EFE8")
        TEXT     = colors.HexColor("#2C2416")
        MUTED    = colors.HexColor("#8C7B6B")
        GREEN    = colors.HexColor("#3D7A4F")
        RED      = colors.HexColor("#C0392B")
        WHITE    = colors.white

        period = request.args.get("period","today")
        date_from = request.args.get("from","")
        date_to   = request.args.get("to","")
        cases = [c for c in load_cases() if not is_testing(c)]

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

        cases = sorted(cases, key=lambda c: c.get("opened_at",""), reverse=True)
        total  = len(cases)
        done   = sum(1 for c in cases if c.get("status")=="done")
        missed = sum(1 for c in cases if c.get("status")=="missed")
        rt     = [c["response_secs"] for c in cases if c.get("response_secs")]
        avg    = int(sum(rt)/len(rt)) if rt else 0
        rate   = round(done/total*100) if total else 0
        agent_counts = Counter(c["agent_name"] for c in cases if c.get("agent_name") and c.get("status") in ("assigned","reported","done"))

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=landscape(letter),
            leftMargin=0.6*inch, rightMargin=0.6*inch,
            topMargin=0.5*inch, bottomMargin=0.5*inch)
        story = []

        def style(name, **kwargs):
            return ParagraphStyle(name, **kwargs)

        label_style = style("label", fontSize=8, textColor=MUTED, fontName="Helvetica", alignment=TA_CENTER)
        cell_style  = style("cell",  fontSize=9, textColor=TEXT, fontName="Helvetica")
        cell_muted  = style("muted", fontSize=9, textColor=MUTED, fontName="Helvetica")
        cell_bold   = style("bold",  fontSize=9, textColor=TEXT, fontName="Helvetica-Bold")

        now_str = datetime.now().strftime("%B %d, %Y — %I:%M %p ET")
        story.append(Paragraph(f"<b>Kurtex Dashboard</b> — {label}", style("hd", fontSize=16, textColor=TEXT, fontName="Helvetica-Bold")))
        story.append(Paragraph(f"Truck Maintenance Command Center · {now_str}", style("sub", fontSize=9, textColor=MUTED, fontName="Helvetica", spaceAfter=8)))
        story.append(HRFlowable(width="100%", thickness=2, color=ACCENT, spaceAfter=10))

        stats_data = [[
            Paragraph("TOTAL", label_style),
            Paragraph("RESOLVED", label_style),
            Paragraph("MISSED", label_style),
            Paragraph("RATE", label_style),
            Paragraph("AVG RESPONSE", label_style),
        ],[
            Paragraph(str(total), style("sv1", fontSize=26, textColor=ACCENT, fontName="Helvetica-Bold", alignment=TA_CENTER)),
            Paragraph(str(done),  style("sv2", fontSize=26, textColor=GREEN,  fontName="Helvetica-Bold", alignment=TA_CENTER)),
            Paragraph(str(missed),style("sv3", fontSize=26, textColor=RED,    fontName="Helvetica-Bold", alignment=TA_CENTER)),
            Paragraph(f"{rate}%", style("sv4", fontSize=26, textColor=ACCENT, fontName="Helvetica-Bold", alignment=TA_CENTER)),
            Paragraph(fmt_secs(avg), style("sv5", fontSize=20, textColor=TEXT, fontName="Helvetica-Bold", alignment=TA_CENTER)),
        ]]
        stats_table = Table(stats_data, colWidths=[2.0*inch]*5)
        stats_table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), BG_ROW),
            ("BACKGROUND", (0,0), (-1,0), BG_LIGHT),
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("TOPPADDING", (0,0), (-1,-1), 8),
            ("BOTTOMPADDING", (0,0), (-1,-1), 8),
            ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#E2D9CC")),
            ("INNERGRID", (0,0), (-1,-1), 0.3, colors.HexColor("#E2D9CC")),
        ]))
        story.append(stats_table)
        story.append(Spacer(1, 14))

        medals = ["1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.", "10."]
        lb_data = [[Paragraph("AGENT PERFORMANCE", style("lbh", fontSize=9, textColor=WHITE, fontName="Helvetica-Bold"))]]
        for i, (name, count) in enumerate(agent_counts.most_common(8)):
            lb_data.append([Paragraph(f"{medals[i] if i<len(medals) else str(i+1)+'.'} {name}  {count}", style("lbr", fontSize=9, textColor=TEXT, fontName="Helvetica"))])

        lb_table = Table(lb_data, colWidths=[2.8*inch])
        lb_table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), ACCENT),
            ("BACKGROUND", (0,1), (-1,-1), BG_LIGHT),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [BG_LIGHT, BG_ROW]),
            ("TOPPADDING", (0,0), (-1,-1), 6),
            ("BOTTOMPADDING", (0,0), (-1,-1), 6),
            ("LEFTPADDING", (0,0), (-1,-1), 8),
            ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#E2D9CC")),
        ]))

        status_colors = {"done": GREEN, "missed": RED, "assigned": colors.HexColor("#C17B3F"), "reported": colors.HexColor("#7B5EA7"), "open": colors.HexColor("#2E6EA6")}
        cases_data = [[
            Paragraph("REPORTED BY", style("th", fontSize=8, textColor=WHITE, fontName="Helvetica-Bold")),
            Paragraph("GROUP", style("th2", fontSize=8, textColor=WHITE, fontName="Helvetica-Bold")),
            Paragraph("ASSIGNED TO", style("th3", fontSize=8, textColor=WHITE, fontName="Helvetica-Bold")),
            Paragraph("STATUS", style("th4", fontSize=8, textColor=WHITE, fontName="Helvetica-Bold")),
            Paragraph("OPENED", style("th5", fontSize=8, textColor=WHITE, fontName="Helvetica-Bold")),
            Paragraph("RESPONSE", style("th6", fontSize=8, textColor=WHITE, fontName="Helvetica-Bold")),
        ]]
        for c in cases[:50]:
            sc = status_colors.get(c.get("status","open"), TEXT)
            cases_data.append([
                Paragraph(c.get("driver_name","—")[:25], cell_bold),
                Paragraph((c.get("group_name","—"))[:30], cell_muted),
                Paragraph((c.get("agent_name") or "—")[:20], cell_style),
                Paragraph((c.get("status","open") or "").upper(), style("st", fontSize=8, textColor=sc, fontName="Helvetica-Bold")),
                Paragraph(fmt_dt(c.get("opened_at",""))[:12], cell_muted),
                Paragraph(fmt_secs(c.get("response_secs")), cell_style),
            ])

        cases_table = Table(cases_data, colWidths=[1.5*inch, 2.0*inch, 1.5*inch, 0.9*inch, 1.0*inch, 0.8*inch])
        cases_table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), ACCENT),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [WHITE, BG_ROW]),
            ("ALIGN", (0,0), (-1,-1), "LEFT"),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("TOPPADDING", (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ("LEFTPADDING", (0,0), (-1,-1), 6),
            ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#E2D9CC")),
            ("INNERGRID", (0,0), (-1,-1), 0.2, colors.HexColor("#E2D9CC")),
        ]))

        combo = Table([[lb_table, cases_table]], colWidths=[3.0*inch, 7.8*inch], hAlign="LEFT")
        combo.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),4),("TOPPADDING",(0,0),(-1,-1),0)]))
        story.append(combo)
        story.append(Spacer(1, 8))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#E2D9CC")))
        story.append(Paragraph(f"Generated by Kurtex Dashboard · {now_str}",
            style("footer", fontSize=7, textColor=MUTED, fontName="Helvetica", alignment=TA_CENTER, spaceBefore=4)))

        doc.build(story)
        buf.seek(0)
        today = datetime.now().strftime("%Y-%m-%d")
        return Response(buf.getvalue(), mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=kurtex-report-{today}.pdf"})
    except Exception as e:
        logger.error(f"PDF export error: {e}")
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


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
