"""
dashboard.py — Kurtex Alert Bot Web Dashboard
Routes and read-only API. Presentation lives in templates/ and static/.
"""
import csv, hashlib, hmac, io, json, logging, os, re, secrets, time, urllib.parse, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread

from app_time import CENTRAL_TZ, chicago_date_str, chicago_now, chicago_timestamp
from dashboard_data import CaseSnapshot, DataUnavailable
from flask import Flask, g, jsonify, render_template, request, session, redirect, Response

logger = logging.getLogger(__name__)
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

def _dashboard_secret() -> str:
    env_secret = os.getenv("DASHBOARD_SECRET", "").strip()
    if env_secret:
        return env_secret
    secret_file = DATA_DIR / "dashboard_secret"
    if secret_file.exists():
        existing = secret_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    value = secrets.token_urlsafe(48)
    secret_file.write_text(value, encoding="utf-8")
    try:
        secret_file.chmod(0o600)
    except OSError:
        pass
    return value

app = Flask(__name__)
app.secret_key = _dashboard_secret()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
BOT_TOKEN      = os.getenv("BOT_TOKEN", "")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))


def verify_telegram_login(data):
    if not BOT_TOKEN:
        return False
    check_hash = data.get("hash", "")
    try:
        age = time.time() - int(data.get("auth_date", 0))
        if int(data.get("id", 0)) <= 0 or not 0 <= age <= 86400:
            return False
    except (ValueError, TypeError, OverflowError):
        return False
    if not isinstance(check_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", check_hash):
        return False
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()) if k != "hash")
    secret = hashlib.sha256(BOT_TOKEN.encode()).digest()
    computed = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, check_hash)


def get_bot_username():
    return os.getenv("BOT_USERNAME", "")


case_snapshot = CaseSnapshot()

def load_cases():
    cases, stale = case_snapshot.read(DATA_DIR / "cases.json")
    g.case_data_stale = stale
    return cases

@app.after_request
def dashboard_headers(response):
    if not request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if getattr(g, "case_data_stale", False):
        response.headers["X-Case-Data-Stale"] = "true"
    return response

@app.errorhandler(DataUnavailable)
def data_unavailable(error):
    return jsonify({"error": str(error)}), 503

def valid_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat() == value
    except (TypeError, ValueError):
        return False

def matches_agent(case, user):
    if user.get("id") and case.get("agent_id"):
        return str(case["agent_id"]) == str(user["id"])
    if user.get("username") and case.get("agent_username"):
        return norm_uname(case["agent_username"]) == norm_uname(user["username"])
    name = user.get("name") or user.get("first_name") or ""
    return bool(name and case["agent_name"].strip().casefold() == name.strip().casefold())

def top_units_for(cases, limit):
    counts = Counter((c["unit_number"].strip(), c["vehicle_type"].strip().lower()) for c in cases if c["unit_number"].strip())
    return [{"unit":u, "vtype":vt, "count":n} for (u,vt),n in counts.most_common(limit)]

def csv_cell(value):
    text = str(value or "")
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


CHI_TZ = CENTRAL_TZ

def today_str():
    return chicago_now().date().isoformat()

def week_start_str():
    now = chicago_now()
    return (now - timedelta(days=now.weekday())).date().isoformat()

def month_start_str():
    return chicago_now().date().replace(day=1).isoformat()

def fmt_dt(iso):
    if not iso: return "—"
    try:
        dt = chicago_timestamp(iso)
        return dt.strftime("%b %d %H:%M") if dt else "—"
    except: return str(iso)[:16]

def norm_uname(u):
    """Normalize a Telegram username for comparison: strip whitespace, leading '@', lowercase."""
    return (u or "").strip().lstrip("@").lower()

def case_local_date(c):
    """The case's opened_at date, converted to America/Chicago (naive timestamps are assumed UTC)."""
    iso = c.get("opened_at") if isinstance(c, dict) else c
    if not iso: return ""
    return chicago_date_str(iso) or (iso or "")[:10]

def fmt_secs(secs):
    if secs is None: return "—"
    secs = int(secs)
    if secs < 60: return f"{secs}s"
    if secs < 3600: return f"{secs//60}m {secs%60}s"
    return f"{secs//3600}h {(secs%3600)//60}m"

TESTING_GROUPS = {"testing", "test", "tests"}

def is_testing(c):
    # Testing tab/filter removed: cases from testing groups are no longer
    # excluded from stats — they now count as normal active cases.
    return False

def build_phrase_pattern(q):
    """Compile a case-insensitive, whole-word/phrase regex for exact keyword matching.
    'air' won't match inside 'repair'; 'oil leak' matches only as a contiguous phrase."""
    tokens = [re.escape(t) for t in re.split(r"\s+", (q or "").strip()) if t]
    if not tokens:
        return None
    return re.compile(r"\b" + r"\s+".join(tokens) + r"\b", re.IGNORECASE)

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
            "opened_raw":  case_local_date(c),
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
        from storage.user_store import get_user
        u = get_user(user_id)
        if not u:
            return redirect("/login?error=unauthorized")
        role = u.get("role", "agent")
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


_PART_IMAGE_CACHE = {}

_PART_IMAGE_CONTEXT = {
    "air": "heavy duty truck air brake pneumatic",
    "brakes": "heavy duty truck air brake",
    "suspension": "semi truck trailer suspension",
    "electrical": "heavy duty diesel truck electrical",
    "engine": "heavy duty diesel engine truck",
    "aftertreatment": "diesel truck exhaust aftertreatment",
    "trailer": "semi truck trailer",
    "reefer": "refrigerated semi trailer reefer",
}
_PART_IMAGE_BAD_WORDS = {
    "shell", "seashell", "snail", "mollusc", "mollusk", "gastropod", "animal", "plant",
    "flower", "bird", "fish", "food", "toy", "art", "painting", "sculpture", "logo", "map",
}
_PART_IMAGE_TRUCK_WORDS = {
    "truck", "semi", "tractor", "trailer", "diesel", "engine", "automotive", "vehicle", "lorry",
    "brake", "suspension", "reefer", "refrigeration", "commercial vehicle", "heavy duty",
}

def _part_tokens(value: str):
    words = re.findall(r"[a-z0-9]+", (value or "").lower())
    stop = {"and", "the", "system", "assembly", "unit", "truck", "semi", "heavy", "duty", "part"}
    return {w for w in words if len(w) >= 3 and w not in stop}

def _commons_part_images(part_name: str, category: str = "", keywords: str = "", limit: int = 4):
    """Return only high-confidence real photos for a heavy-duty truck component.

    Wikimedia search is used for discovery, but results are scored locally. Generic or
    unrelated matches are rejected; an empty result is preferable to a wrong photo.
    """
    clean_name = re.sub(r"[^a-zA-Z0-9 /+&()._-]+", " ", (part_name or "")).strip()[:100]
    clean_keywords = re.sub(r"[^a-zA-Z0-9 /+&()._-]+", " ", (keywords or "")).strip()[:160]
    category = re.sub(r"[^a-zA-Z]+", "", (category or "").lower())[:30]
    if not clean_name:
        return []
    cache_key = (clean_name + "|" + category + "|" + clean_keywords).lower()
    cached = _PART_IMAGE_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < 86400:
        return cached[1]

    context = _PART_IMAGE_CONTEXT.get(category, "heavy duty diesel truck")
    # Exact component wording comes first. Do not use a bare/generic fallback query.
    queries = [
        f'"{clean_name}" {context}',
        f'{clean_name} {context} component',
    ]
    required = _part_tokens(clean_name)
    hint_tokens = _part_tokens(clean_keywords)
    candidates = {}

    for search_query in queries:
        params = {
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"filetype:bitmap {search_query}", "gsrnamespace": "6", "gsrlimit": "24",
            "prop": "imageinfo", "iiprop": "url|mime|extmetadata", "iiurlwidth": "1200",
            "iiextmetadatafilter": "ImageDescription|ObjectName|Categories|LicenseShortName|Artist|Credit",
            "origin": "*",
        }
        url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "KurtexDashboard/1.1 (heavy-duty parts photo lookup)"})
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.info("Commons part image lookup failed for %r: %s", search_query, exc)
            continue

        for page in (data.get("query") or {}).get("pages", {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            if info.get("mime", "") not in {"image/jpeg", "image/png", "image/webp"}:
                continue
            meta = info.get("extmetadata") or {}
            meta_text = " ".join(str((meta.get(k) or {}).get("value", "")) for k in ("ImageDescription", "ObjectName", "Categories"))
            hay = re.sub(r"<[^>]+>", " ", (page.get("title", "") + " " + meta_text)).lower()
            hay_tokens = _part_tokens(hay)
            if any(bad in hay for bad in _PART_IMAGE_BAD_WORDS):
                continue
            # Component identity is mandatory. This prevents e.g. "turbo" shells.
            component_hits = len(required & hay_tokens)
            if required and component_hits == 0:
                continue
            truck_hits = sum(1 for term in _PART_IMAGE_TRUCK_WORDS if term in hay)
            hint_hits = len(hint_tokens & hay_tokens)
            # For ambiguous one-word parts, require explicit vehicle/mechanical context.
            if len(required) <= 1 and truck_hits == 0 and hint_hits < 2:
                continue
            score = component_hits * 12 + min(truck_hits, 4) * 4 + min(hint_hits, 5)
            title = page.get("title", "").replace("File:", "")
            if clean_name.lower() in title.lower():
                score += 12
            image_url = info.get("thumburl") or info.get("url")
            source_url = info.get("descriptionurl")
            if not image_url or not source_url:
                continue
            key = source_url
            item = {
                "image_url": image_url, "source_url": source_url, "title": title,
                "license": (meta.get("LicenseShortName") or {}).get("value", ""), "score": score,
            }
            if key not in candidates or score > candidates[key]["score"]:
                candidates[key] = item

    results = sorted(candidates.values(), key=lambda x: (-x["score"], x["title"].lower()))[:max(1, min(limit, 4))]
    for item in results:
        item.pop("score", None)
    _PART_IMAGE_CACHE[cache_key] = (time.time(), results)
    return results

@app.route("/api/part_image")
def api_part_image():
    q = (request.args.get("q") or "").strip()
    cat = (request.args.get("cat") or "").strip()
    keywords = (request.args.get("keywords") or "").strip()
    if not q:
        return jsonify({"ok": False, "error": "Missing part query"}), 400
    results = _commons_part_images(q, cat, keywords, 4)
    return jsonify({"ok": bool(results), "results": results, "result": results[0] if results else None})

@app.route("/api/stats")
def api_stats():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    try:
        cases = load_cases()
        today = today_str(); wk = week_start_str(); mo = month_start_str()
        real = [c for c in cases if not is_testing(c)]
        tc = [c for c in real if case_local_date(c) == today]
        wc = [c for c in real if case_local_date(c) >= wk]
        mc = [c for c in real if case_local_date(c) >= mo]
        st = Counter(c.get("status","open") for c in tc)

        def lb(lst):
            cnt = Counter(c["agent_name"] for c in lst if c.get("agent_name") and c.get("status") in ("assigned","reported","done"))
            return [{"name":n,"count":v} for n,v in cnt.most_common(10)]
        # Group resolution rates (all-time, min 3 cases to be meaningful)
        from collections import defaultdict
        grp_data = defaultdict(lambda: {"total":0,"done":0,"missed":0})
        for c in real:
            gn = c.get("group_name","Unknown") or "Unknown"
            grp_data[gn]["total"] += 1
            if c.get("status") == "done":   grp_data[gn]["done"] += 1
            if c.get("status") == "missed": grp_data[gn]["missed"] += 1
        group_stats = []
        for gn, d in grp_data.items():
            if d["total"] >= 1:
                rate = round(d["done"]/d["total"]*100) if d["total"] else 0
                group_stats.append({"name":gn,"total":d["total"],"done":d["done"],"missed":d["missed"],"rate":rate})
        group_stats.sort(key=lambda x: -x["total"])

        top_problem_units = top_units_for(real, 6)
        hashtags = re.findall(r'#\w+', " ".join(c.get("description","") for c in real).lower())
        rt = [c["response_secs"] for c in real if c.get("response_secs") is not None]
        avg = int(sum(rt)/len(rt)) if rt else 0
        return jsonify({
            "today": {"total":len(tc),"open":st.get("open",0),"assigned":st.get("assigned",0)+st.get("reported",0),"done":st.get("done",0),"missed":st.get("missed",0)},
            "week":  {"total":len(wc),"done":sum(1 for c in wc if c.get("status")=="done"),"missed":sum(1 for c in wc if c.get("status")=="missed")},
            "month": {"total":len(mc),"done":sum(1 for c in mc if c.get("status")=="done"),"missed":sum(1 for c in mc if c.get("status")=="missed")},
            "all_time": {"total":len(cases),"done":sum(1 for c in cases if c.get("status")=="done"),"avg_resp":fmt_secs(avg)},
            "leaderboard_day": lb(tc), "leaderboard_week": lb(wc), "leaderboard_month": lb(mc),
            "top_groups": group_stats[:6],
            "top_problem_units": top_problem_units,
            "top_words": [{"word":w,"count":v} for w,v in Counter(hashtags).most_common(15)],
            "reassigned_count": sum(1 for c in cases if c.get("reassigned")),
        })
    except DataUnavailable:
        raise
    except Exception as e:
        logger.error(f"api_stats error: {e}")
        return jsonify({"error": "Unable to load data. Please retry."}), 500


@app.route("/api/cases")
def api_cases():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    try:
        f = request.args.get("filter","today")
        search = request.args.get("search","").lower().strip()
        date_filter = request.args.get("date","").strip()
        if date_filter and not valid_date(date_filter): return jsonify({"error":"Invalid date."}), 400
        try:
            offset = max(0, int(request.args.get("offset", 0)))
        except (TypeError, ValueError):
            offset = 0
        try:
            limit = max(1, min(int(request.args.get("limit", 100)), 500))
        except (TypeError, ValueError):
            limit = 100
        cases = load_cases()
        if f != "testing":
            cases = [c for c in cases if not is_testing(c)]
        if date_filter:
            cases = [c for c in cases if case_local_date(c) == date_filter]
        elif f == "today":    cases = [c for c in cases if case_local_date(c) == today_str()]
        elif f == "week":     cases = [c for c in cases if case_local_date(c) >= week_start_str()]
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
        cases = sorted(cases, key=lambda c: c.get("opened_at",""), reverse=True)
        total = len(cases)
        page = cases[offset:offset+limit]
        return jsonify({
            "cases": [serialize_case(c) for c in page],
            "total": total, "offset": offset, "limit": limit,
            "has_more": offset + limit < total,
        })
    except DataUnavailable:
        raise
    except Exception as e:
        logger.error(f"api_cases error: {e}")
        return jsonify({"error": "Unable to load data. Please retry."}), 500


@app.route("/api/case")
def api_case_detail():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    case_id = request.args.get("id","").strip()
    if not case_id: return jsonify({"error":"no id"}), 400
    try:
        cases = load_cases()
        matches = [c for c in cases if c["id"] == case_id]
        if not matches: matches = [c for c in cases if c["id"].startswith(case_id)]
        if len(matches) > 1: return jsonify({"error":"Ambiguous case ID. Use the full ID."}), 409
        for c in matches:
            if c:
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
                    "location":         c.get("location",""),
                    "setpoint":         c.get("setpoint",""),
                    "current_temp":     c.get("current_temp",""),
                    "temp_recorder":    c.get("temp_recorder",""),
                })
                return jsonify(data)
        return jsonify({"error":"not found"}), 404
    except DataUnavailable:
        raise
    except Exception as e:
        logger.error(f"api_case error: {e}")
        return jsonify({"error": "Unable to load data. Please retry."}), 500


@app.route("/api/agent")
def api_agent():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    agent_name = request.args.get("name","").strip()
    agent_uname = norm_uname(request.args.get("username",""))
    if not agent_name: return jsonify({"error":"no name"}), 400
    period = request.args.get("period","all").strip().lower()
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = max(1, min(int(request.args.get("limit", 15)), 100))
    except (TypeError, ValueError):
        limit = 15
    try:
        cases = [c for c in load_cases() if matches_agent(c, {"id":request.args.get("id"), "name":agent_name, "username":agent_uname})]
        total  = len(cases)
        done   = sum(1 for c in cases if c.get("status") == "done")
        missed = sum(1 for c in cases if c.get("status") == "missed")
        rt     = [c["response_secs"] for c in cases if c.get("response_secs") is not None]
        avg    = int(sum(rt)/len(rt)) if rt else 0

        if period == "today":
            bound = today_str()
            period_cases = [c for c in cases if case_local_date(c) == bound]
        elif period == "week":
            bound = week_start_str()
            period_cases = [c for c in cases if case_local_date(c) >= bound]
        elif period == "month":
            bound = month_start_str()
            period_cases = [c for c in cases if case_local_date(c) >= bound]
        else:
            period = "all"
            period_cases = cases

        period_cases.sort(key=lambda c: c.get("opened_at",""), reverse=True)
        period_total  = len(period_cases)
        period_done   = sum(1 for c in period_cases if c.get("status") == "done")
        period_missed = sum(1 for c in period_cases if c.get("status") == "missed")
        page = period_cases[offset:offset+limit]

        return jsonify({
            "name": agent_name, "total": total, "done": done, "missed": missed,
            "avg_resp": fmt_secs(avg), "rate": round(done/total*100) if total else 0,
            "period": period,
            "period_stats": {
                "total": period_total, "done": period_done, "missed": period_missed,
                "rate": round(period_done/period_total*100) if period_total else 0,
            },
            "cases": [serialize_case(c) for c in page],
            "offset": offset, "limit": limit,
            "has_more": offset + limit < period_total,
        })
    except DataUnavailable:
        raise
    except Exception as e:
        logger.error(f"api_agent error: {e}")
        return jsonify({"error": "Unable to load data. Please retry."}), 500


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
            uname = norm_uname(u.get("username"))
            agent_cases = [c for c in cases if matches_agent(c, u)]
            total  = len(agent_cases)
            done   = sum(1 for c in agent_cases if c.get("status") == "done")
            missed = sum(1 for c in agent_cases if c.get("status") == "missed")
            rt     = [c["response_secs"] for c in agent_cases if c.get("response_secs") is not None]
            avg    = int(sum(rt)/len(rt)) if rt else 0
            result.append({
                "id":       u.get("id", ""),
                "name":     name,
                "username": u.get("username",""),
                "total":    total, "done": done, "missed": missed,
                "avg_resp": fmt_secs(avg),
                "rate":     round(done/total*100) if total else 0,
            })
        result.sort(key=lambda x: -x["total"])
        return jsonify(result)
    except DataUnavailable:
        raise
    except Exception as e:
        logger.error(f"api_agents error: {e}")
        return jsonify({"error": "Unable to load data. Please retry."}), 500


@app.route("/api/my_profile")
def api_my_profile():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    try:
        user  = session["user"]
        name  = user.get("first_name","")
        uname = norm_uname(user.get("username",""))
        cases = load_cases()
        my_cases = [c for c in cases if matches_agent(c, user)]
        today = today_str(); wk = week_start_str()
        tc = [c for c in my_cases if case_local_date(c) == today]
        wc = [c for c in my_cases if case_local_date(c) >= wk]
        total  = len(my_cases)
        done   = sum(1 for c in my_cases if c.get("status") == "done")
        missed = sum(1 for c in my_cases if c.get("status") == "missed")
        rt     = [c["response_secs"] for c in my_cases if c.get("response_secs") is not None]
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
    except DataUnavailable:
        raise
    except Exception as e:
        logger.error(f"api_my_profile error: {e}")
        return jsonify({"error": "Unable to load data. Please retry."}), 500


@app.route("/api/unit")
def api_unit():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    unit_number = request.args.get("unit","").strip()
    vtype = request.args.get("vtype","").strip().lower()
    if not unit_number: return jsonify({"error":"no unit"}), 400
    try:
        all_cases = load_cases()
        unit_cases = [c for c in all_cases if (c.get("unit_number") or "").strip() == unit_number]
        if vtype:
            unit_cases = [c for c in unit_cases if (c.get("vehicle_type") or "").strip().lower() == vtype]
        unit_cases.sort(key=lambda c: c.get("opened_at",""), reverse=True)
        total  = len(unit_cases)
        active = sum(1 for c in unit_cases if c.get("status") in ("open","assigned","reported","missed"))
        done   = sum(1 for c in unit_cases if c.get("status") == "done")
        missed = sum(1 for c in unit_cases if c.get("status") == "missed")
        issue_counts = Counter((c.get("issue_text","").strip() or "")[:60] for c in unit_cases if c.get("issue_text","").strip())
        return jsonify({
            "unit": unit_number,
            "vtype": (unit_cases[0].get("vehicle_type","") if unit_cases else ""),
            "total": total, "active": active, "done": done, "missed": missed,
            "top_issues": [{"issue": iss, "count": cnt} for iss, cnt in issue_counts.most_common(5)],
            "cases": [serialize_case(c) for c in unit_cases[:50]],
        })
    except DataUnavailable:
        raise
    except Exception as e:
        logger.error(f"api_unit error: {e}")
        return jsonify({"error": "Unable to load data. Please retry."}), 500


@app.route("/api/issue_search")
def api_issue_search():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    q = request.args.get("q","").strip()
    vtype = request.args.get("vtype","").strip().lower()
    if not q: return jsonify({"error":"no query"}), 400
    try:
        # Match the exact keyword/phrase as typed (whole-word boundaries on each
        # side, internal whitespace tolerant), case-insensitive. This is a literal
        # match of the phrase, not "any of these words anywhere".
        pattern = build_phrase_pattern(q)
        if not pattern: return jsonify({"error":"no query"}), 400
        all_cases = load_cases()
        matches = []
        for c in all_cases:
            if not c.get("unit_number"): continue
            if vtype and (c.get("vehicle_type") or "").strip().lower() != vtype: continue
            text = (c.get("issue_text") or "") + " " + (c.get("description") or "")
            if pattern.search(text):
                matches.append(c)
        from collections import defaultdict
        by_unit = defaultdict(lambda: {"cases": [], "vtype": ""})
        for c in matches:
            unit = (c.get("unit_number") or "").strip()
            by_unit[(unit, c["vehicle_type"].lower())]["vtype"] = c.get("vehicle_type","")
            by_unit[(unit, c["vehicle_type"].lower())]["cases"].append(c)
        results = []
        for (unit, _), d in by_unit.items():
            cases = sorted(d["cases"], key=lambda c: c.get("opened_at",""), reverse=True)
            last = cases[0]
            results.append({
                "unit": unit,
                "vtype": d["vtype"],
                "count": len(cases),
                "last_seen": fmt_dt(last.get("opened_at")),
                "sample_issue": (last.get("issue_text") or last.get("description") or "")[:100],
            })
        results.sort(key=lambda x: -x["count"])
        return jsonify({"query": q, "results": results, "total_matches": len(matches)})
    except DataUnavailable:
        raise
    except Exception as e:
        logger.error(f"api_issue_search error: {e}")
        return jsonify({"error": "Unable to load data. Please retry."}), 500


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
        truck_breakdowns = Counter(c.get("unit_number","").strip() for c in cases if c.get("vehicle_type") == "truck" and c.get("unit_number","").strip())
        driver_counts = Counter(c.get("report_driver","").strip() for c in cases if c.get("report_driver","").strip())
        issue_counts  = Counter((c.get("issue_text","").strip() or "")[:40] for c in cases if c.get("issue_text","").strip())
        load_counts   = Counter(c.get("load_type","").strip() for c in cases if c.get("load_type","").strip())
        latest_by_unit = {}
        active_by_unit = {}
        for c in sorted(cases, key=lambda x: x.get("opened_at","")):
            unit = c.get("unit_number","").strip()
            vtype = c.get("vehicle_type","").strip()
            if unit:
                latest_by_unit[(unit, vtype)] = c
                if c["status"] in ("open","assigned","reported","missed"): active_by_unit[(unit,vtype)] = c
        fleet_status = []
        active_statuses = {"open", "assigned", "reported", "missed"}
        for (unit, vtype), c in latest_by_unit.items():
            c = active_by_unit.get((unit,vtype), c)
            status = c.get("status") or "open"
            fleet_status.append({
                "unit": unit,
                "vtype": vtype,
                "case_id": c.get("id",""),
                "status": "active" if status in active_statuses else "repaired",
                "case_status": status,
                "issue": c.get("issue_text") or c.get("description") or "",
                "driver": c.get("report_driver") or c.get("driver_name") or "",
                "opened": fmt_dt(c.get("opened_at")),
            })
        active_units = sum(1 for x in fleet_status if x["status"] == "active")
        repaired_units = sum(1 for x in fleet_status if x["status"] == "repaired")
        fleet_status = sorted(
            fleet_status,
            key=lambda x: (x["status"] != "active", x["vtype"], x["unit"])
        )
        return jsonify({
            "total_reports": total, "truck_count": truck_count,
            "trailer_count": trailer_count, "reefer_count": reefer_count,
            "active_units": active_units,
            "repaired_units": repaired_units,
            "top_units":    [{"unit":u,"vtype":vt,"count":cnt} for (u,vt),cnt in unit_counts.most_common(10)],
            "top_broken_trucks": [{"unit":u,"vtype":"truck","count":cnt} for u,cnt in truck_breakdowns.most_common(10)],
            "top_drivers":  [{"unit":n,"vtype":"","count":cnt} for n,cnt in driver_counts.most_common(10)],
            "top_issues":   [{"unit":iss,"vtype":"","count":cnt} for iss,cnt in issue_counts.most_common(8)],
            "load_types":   [{"unit":lt,"vtype":"","count":cnt} for lt,cnt in load_counts.most_common(6)],
            "fleet_status": fleet_status,
        })
    except DataUnavailable:
        raise
    except Exception as e:
        logger.error(f"api_fleet error: {e}")
        return jsonify({"error": "Unable to load data. Please retry."}), 500


@app.route("/api/report")
def api_report():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    try:
        period    = request.args.get("period","today")
        date_from = request.args.get("from","")
        date_to   = request.args.get("to","")
        if period == "custom":
            date_to = date_to or today_str()
            if not valid_date(date_from) or not valid_date(date_to) or date_from > date_to:
                return jsonify({"error":"Choose a valid start and end date in order."}), 400
        cases     = load_cases()
        if period == "today":
            label = "Today — " + chicago_now().strftime("%B %d, %Y")
            cases = [c for c in cases if case_local_date(c) == today_str()]
        elif period == "week":
            label = "This Week"; cases = [c for c in cases if case_local_date(c) >= week_start_str()]
        elif period == "month":
            label = "This Month"; cases = [c for c in cases if case_local_date(c) >= month_start_str()]
        elif period == "custom" and date_from:
            dt = date_to or today_str(); label = f"{date_from} to {dt}"
            cases = [c for c in cases if date_from <= case_local_date(c) <= dt]
        else:
            label = "All Time"
        total  = len(cases)
        done   = sum(1 for c in cases if c.get("status") == "done")
        missed = [c for c in cases if c.get("status") == "missed"]
        rt     = [c["response_secs"] for c in cases if c.get("response_secs") is not None]
        avg    = int(sum(rt)/len(rt)) if rt else 0
        agent_counts  = Counter(c["agent_name"] for c in cases if c.get("agent_name") and c.get("status") in ("assigned","reported","done"))
        group_counts  = Counter(c.get("group_name","Unknown") for c in cases)

        # Top issues broken down by vehicle type (truck/trailer/reefer)
        by_vtype = {}
        for vt in ("truck", "trailer", "reefer"):
            vt_cases = [c for c in cases if (c.get("vehicle_type") or "").lower() == vt]
            issue_counts = Counter((c.get("issue_text") or c.get("description") or "Unspecified issue").strip() for c in vt_cases if (c.get("issue_text") or c.get("description")))
            by_vtype[vt] = {
                "total": len(vt_cases),
                "top_issues": [{"issue": i, "count": n} for i, n in issue_counts.most_common(5)],
            }

        # Top problem units for this period (most reported units, any type)
        top_units = top_units_for(cases, 10)

        return jsonify({
            "label": label, "total": total, "done": done, "missed": len(missed),
            "assigned": sum(1 for c in cases if c.get("status") in ("assigned","reported","done")),
            "open": sum(1 for c in cases if c.get("status") == "open"),
            "avg_resp": fmt_secs(avg), "rate": round(done/total*100) if total else 0,
            "leaderboard": [{"name":n,"count":v} for n,v in agent_counts.most_common(10)],
            "top_groups":  [{"name":n,"count":v} for n,v in group_counts.most_common(5)],
            "missed_cases": [serialize_case(c) for c in missed[:20]],
            "by_vtype": by_vtype,
            "top_units": top_units,
        })
    except DataUnavailable:
        raise
    except Exception as e:
        logger.error(f"api_report error: {e}")
        return jsonify({"error": "Unable to load data. Please retry."}), 500


@app.route("/api/export")
def api_export():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    cases = load_cases()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ID","Reported By","Group","Assigned To","Status","Opened","Closed","Response","Description","Notes"])
    for c in sorted(cases, key=lambda x: x.get("opened_at",""), reverse=True):
        w.writerow([csv_cell(value) for value in [
            (c.get("id") or "")[:8], c.get("driver_name",""), c.get("group_name",""),
            c.get("agent_name",""), c.get("status",""),
            (c.get("opened_at") or "")[:16], (c.get("closed_at") or "")[:16],
            fmt_secs(c.get("response_secs")), c.get("description",""), c.get("notes",""),
        ]])
    out.seek(0)
    today = chicago_now().strftime("%Y-%m-%d")
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=kurtex-{today}.csv"})


# ── HTML pages ────────────────────────────────────────────────────────────────






@app.route("/api/trends")
def api_trends():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    try:
        cases = [c for c in load_cases() if not is_testing(c)]
        period = request.args.get("period","30")
        try: days = int(period)
        except (ValueError, TypeError): return jsonify({"error":"Trend period must be 1 to 366 days."}), 400
        if not 1 <= days <= 366: return jsonify({"error":"Trend period must be 1 to 366 days."}), 400
        by_date = defaultdict(list)
        for case in cases: by_date[case_local_date(case)].append(case)
        from datetime import timedelta
        today = chicago_now().date()
        labels, totals, resolved, missed_arr, avg_resp_arr = [], [], [], [], []
        for i in range(days-1, -1, -1):
            d = today - timedelta(days=i)
            ds = d.isoformat()
            day_cases = by_date.get(ds, [])
            rt = [c["response_secs"] for c in day_cases if c.get("response_secs") is not None]
            labels.append(d.strftime("%b %d"))
            totals.append(len(day_cases))
            resolved.append(sum(1 for c in day_cases if c.get("status")=="done"))
            missed_arr.append(sum(1 for c in day_cases if c.get("status")=="missed"))
            avg_resp_arr.append(int(sum(rt)/len(rt)) if rt else 0)
        return jsonify({"labels":labels,"totals":totals,"resolved":resolved,"missed":missed_arr,"avg_resp":avg_resp_arr})
    except DataUnavailable:
        raise
    except Exception:
        logger.exception("Dashboard request failed")
        return jsonify({"error": "Unable to load data. Please retry."}), 500

@app.route("/api/comparison")
def api_comparison():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    try:
        from datetime import timedelta
        cases = [c for c in load_cases() if not is_testing(c)]
        today = chicago_now().date()
        # This week vs last week
        this_mon = today - timedelta(days=today.weekday())
        last_mon = this_mon - timedelta(days=7)
        last_sun = this_mon - timedelta(days=1)
        def week_stats(start, end):
            wc = [c for c in cases if start.isoformat() <= case_local_date(c) <= end.isoformat()]
            total = len(wc); done = sum(1 for c in wc if c.get("status")=="done")
            missed = sum(1 for c in wc if c.get("status")=="missed")
            rt = [c["response_secs"] for c in wc if c.get("response_secs") is not None]
            avg = int(sum(rt)/len(rt)) if rt else 0
            rate = round(done/total*100) if total else 0
            return {"total":total,"done":done,"missed":missed,"avg_resp":fmt_secs(avg),"avg_secs":avg,"rate":rate}
        this_sun = today
        tw = week_stats(this_mon, this_sun)
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
    except DataUnavailable:
        raise
    except Exception:
        logger.exception("Dashboard request failed")
        return jsonify({"error": "Unable to load data. Please retry."}), 500

@app.route("/api/fleet_intelligence")
def api_fleet_intelligence():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    try:
        all_cases = [c for c in load_cases() if not is_testing(c)]
        reported = [c for c in all_cases if c.get("vehicle_type")]
        # Top units with history
        from collections import defaultdict
        unit_data = defaultdict(lambda: {"cases":[], "vtype":""})
        for c in reported:
            unit = (c.get("unit_number") or "").strip()
            if not unit: continue
            unit_data[(unit, c["vehicle_type"].lower())]["vtype"] = c.get("vehicle_type","")
            unit_data[(unit, c["vehicle_type"].lower())]["cases"].append(c)
        top_units = []
        for (unit, _), data in unit_data.items():
            cases = data["cases"]
            total = len(cases)
            issues = Counter((c.get("issue_text") or "")[:50] for c in cases if c.get("issue_text"))
            top_issue = issues.most_common(1)[0][0] if issues else "—"
            last_case = max(cases, key=lambda c: c.get("opened_at",""))
            top_units.append({
                "unit": unit, "vtype": data["vtype"], "total": total,
                "top_issue": top_issue, "last_seen": fmt_dt(last_case.get("opened_at")),
            })
        top_units.sort(key=lambda x: -x["total"])
        # Top drivers
        driver_data = defaultdict(list)
        for c in reported:
            d = (c.get("report_driver") or "").strip()
            if d: driver_data[d].append(c)
        top_drivers = []
        for name, cases in driver_data.items():
            total = len(cases)
            issues = Counter((c.get("issue_text") or "")[:50] for c in cases if c.get("issue_text"))
            top_issue = issues.most_common(1)[0][0] if issues else "—"
            top_drivers.append({"name": name, "total": total, "top_issue": top_issue})
        top_drivers.sort(key=lambda x: -x["total"])
        return jsonify({
            "top_units": top_units[:20],
            "total_units": len(unit_data),
            "top_drivers": top_drivers[:20],
            "total_reports": len(reported),
        })
    except DataUnavailable:
        raise
    except Exception:
        logger.exception("Dashboard request failed")
        return jsonify({"error": "Unable to load data. Please retry."}), 500


@app.route("/login")
def login():
    return render_template("login.html", bot_username=get_bot_username(), error=request.args.get("error"))

@app.route("/")
def index():
    if not session.get("user"): return redirect("/login")
    user = session["user"]
    is_manager = user.get("role","agent") in ("developer","super_admin")
    return render_template("dashboard.html", user=user, is_manager=is_manager)

def run_dashboard():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=DASHBOARD_PORT, debug=False, use_reloader=False)

def start_dashboard_thread():
    Thread(target=run_dashboard, daemon=True).start()
    logger.info(f"Dashboard started on port {DASHBOARD_PORT}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_dashboard()
