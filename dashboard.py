"""
dashboard.py — Kurtex Alert Bot Web Dashboard
Routes and read-only API. Presentation lives in templates/ and static/.
"""
import base64, csv, hashlib, hmac, io, json, logging, os, re, secrets, time, uuid, urllib.parse, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread, Lock
from functools import wraps
from backend.ai.ai_chat_store import ChatStore
from backend.ai.ai_learning import LearningStore
from backend.ai.ai_learning_routes import register_learning_routes
from backend.ai.fleet_knowledge import FleetKnowledgeStore
from backend.ai.ai_research import search_research
from backend.ai.ai_video import process_video, transcribe_audio, MAX_VIDEO_BYTES
from backend.ai.maintenance_ai import INSPECTION_POLICY, ranked_cases, tokens, knowledge_excerpt, COMPONENTS

from backend.core.app_time import CENTRAL_TZ, chicago_date_str, chicago_now, chicago_timestamp
from backend.core.dashboard_data import CaseSnapshot, DataUnavailable
from flask import Flask, g, jsonify, render_template, request, session, redirect, Response, send_file

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
app.config["MAX_CONTENT_LENGTH"] = 82 * 1024 * 1024
chat_store = ChatStore(DATA_DIR)
learning_store = LearningStore(DATA_DIR / "ai_learning.sqlite3")
fleet_knowledge_store = FleetKnowledgeStore(DATA_DIR / "fleet_knowledge.sqlite3")
_ai_user_locks = [Lock() for _ in range(64)]

def ai_serialized(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return jsonify({"error":"unauthorized"}),401
        if request.method in ("GET","HEAD"):
            return fn(*args, **kwargs)
        lock = _ai_user_locks[int(hashlib.sha256(_ai_user_key().encode()).hexdigest(),16) % 64]
        if not lock.acquire(blocking=False):
            return jsonify({"error":"A chat operation is still running. Please wait before retrying."}),409
        try:
            return fn(*args, **kwargs)
        finally:
            lock.release()
    return wrapped

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
            "report_driver": c.get("report_driver") or c.get("driver_name") or "",
            "unit_number": c.get("unit_number") or "",
            "vehicle_type": (c.get("vehicle_type") or "").lower(),
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



# ── Kurtex AI / Cloudflare Workers AI ─────────────────────────────────────────
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
KURTEX_AI_MODEL=os.getenv("KURTEX_AI_MODEL","@cf/meta/llama-4-scout-17b-16e-instruct").strip()

KURTEX_AI_SYSTEM = """You are Kurtex Maintenance AI, a diagnostic assistant for a commercial truck, trailer and reefer fleet.
Act like an experienced senior maintenance advisor while staying careful about uncertainty. Understand English, Romanian,
Russian, mixed-language mechanic slang, abbreviations and misspellings.
Rules:
- Diagnose systematically: symptom -> likely system -> useful questions/checks -> possible causes -> next action.
- Never invent a Kurtex case, repair, part number, fault code, measurement, OEM procedure or mechanic finding.
- Clearly separate KURTEX HISTORY supplied in context from your own diagnostic assessment.
- Retrieved fleet records are historical evidence, not proof the current problem has the same cause.
- For brakes, steering, wheel-end, pressurized air, refrigerant, high-current electrical and other safety-critical work,
  recommend qualified technician verification and do not give risky step-by-step repair instructions.
- If evidence is weak, say so. Better no match than an unrelated match.
- Be a strong senior maintenance copilot, not a generic chatbot. Give enough detail to make the next diagnostic action clear, but do not pad answers.
- Use the current conversation as diagnostic state: remember tests already performed and their results; do not restart or repeat failed/completed checks.
- When a measurement is supplied, interpret it against verified context when possible and continue the diagnostic branch from that result.
- Prefer testing before parts replacement. Explain what each test result would mean.
- Keep answers practical for mechanics and maintenance agents.
- Reply in the language used by the agent unless asked otherwise.
- Never show internal Kurtex case IDs/UUIDs unless the agent explicitly asks for an ID.
- When citing fleet history, prioritize useful operations fields: Driver / Group, Unit, Reported issue, Resolution, and Resolved by when useful.
- RESOLUTION means only the recorded repair/fix text. solved_by/resolved_by/closed_by is personnel metadata and MUST NEVER be presented as the Resolution.
- If no explicit repair/fix text exists, write "Resolution: No recorded resolution". You may separately write "Resolved by: <name>" when that metadata exists.
- Omit unavailable fields instead of writing "not provided", "unknown", or similar filler.
- Do not pad a history answer with generic observations (for example, do not say that check-engine issues can occur on trucks).
- If the agent asks for matching/history cases, answer with the matching case facts first. Ask diagnostic follow-up questions only when the agent is actually asking for diagnosis.
- Similar cases must match the affected SYSTEM/COMPONENT, not merely share words. Never group pneumatic air leaks with tire-pressure leaks; coolant, oil, fuel and refrigerant leaks are also separate problem families. Never include unrelated cases just to fill a section.
- When summarizing history, include only CONFIRMED recorded fixes. If a case has no explicit resolution, say "Resolution: No recorded resolution"; never invent, merge, infer a repair, or substitute the person who resolved/closed the case.
- For history questions, synthesize the useful pattern: state how many truly relevant cases were found, summarize confirmed fixes, exclude false matches, then give the next practical checks only if the user is asking what to do. Do not repeat the same issue label in multiple redundant bullets.
- For diagnosis, behave like a maintenance triage workflow, not a generic chatbot: first identify what is known, then ask the highest-value missing questions, then give prioritized checks from easiest/most likely to more involved.
- Give focused actionable diagnostic branches only when evidence supports them.
- For every recommended check, explain what result to look for and what that result would indicate. Do not claim a repair is confirmed before testing.
- Distinguish: OBSERVED (visible/provided), LIKELY (reasoned), VERIFIED (supported by Kurtex/source data).
- Use current WEB SEARCH evidence when supplied, but treat snippets as supporting references rather than authoritative OEM procedures.
- When manufacturer/model/fault code is missing and it materially changes diagnosis, explicitly ask for it.
- Do not recommend replacing expensive components until simpler checks and evidence support replacement.
"""

def _ai_explicit_similar_case_request(message):
    text=str(message or "").lower()
    phrases=("similar case", "similar cases", "previous case", "previous cases", "past case", "past cases",
             "fleet case", "fleet cases", "how did we fix", "how was it solved", "how were they solved",
             "same issue before", "history cases", "historical cases")
    return any(p in text for p in phrases)

def _ai_visual_description_request(message, has_visual=False):
    if not has_visual:return False
    text=str(message or "").lower().strip()
    phrases=("what do you see", "what can you see", "describe this", "describe the image", "describe the picture",
             "what is in this picture", "what's in this picture", "what is in this image", "look at this picture",
             "look at this image", "what does this show")
    return any(p in text for p in phrases)

def _cf_ai(messages, max_tokens=900, temperature=0.2, image_data_url=None, images=None):
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        raise RuntimeError("Workers AI is not configured")
    url=f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/{KURTEX_AI_MODEL}"
    payload_messages=[dict(m) for m in messages]
    payload_body={"messages":payload_messages,"max_tokens":max_tokens,"temperature":temperature}
    image_inputs = images or ([image_data_url] if image_data_url else [])
    if image_inputs:
        for i in range(len(payload_messages)-1,-1,-1):
            if payload_messages[i].get("role")=="user":
                payload_messages[i]["content"]=[{"type":"text","text":str(payload_messages[i].get("content") or "")}] + [
                    {"type":"image_url","image_url":{"url":url}} for url in image_inputs[:12]]
                break
    req=urllib.request.Request(url,data=json.dumps(payload_body).encode("utf-8"),
        method="POST",headers={"Authorization":f"Bearer {CLOUDFLARE_API_TOKEN}","Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=35) as resp: payload=json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try: detail=e.read().decode("utf-8","replace")[:2000]
        except Exception: detail=""
        logger.warning("Workers AI HTTP %s: %s",getattr(e,"code","?"),detail)
        raise RuntimeError(f"Workers AI HTTP {getattr(e,'code','error')}")
    except Exception as e:
        logger.warning("Workers AI request failed: %s",e); raise RuntimeError("Workers AI request failed")
    if isinstance(payload,dict) and payload.get("success") is False:
        logger.warning("Workers AI API error: %s",json.dumps(payload.get("errors") or payload,ensure_ascii=False)[:2000])
        raise RuntimeError("Workers AI API returned an error")
    def extract_text(value):
        if value is None:return ""
        if isinstance(value,str):return value
        if isinstance(value,list):return "\n".join(filter(None,(extract_text(x) for x in value)))
        if isinstance(value,dict):
            for key in ("response","content","text","generated_text","output_text"):
                if key in value:
                    found=extract_text(value.get(key))
                    if found:return found
            if isinstance(value.get("choices"),list) and value["choices"]:
                found=extract_text(value["choices"][0])
                if found:return found
            if value.get("message") is not None:
                found=extract_text(value["message"])
                if found:return found
        return ""
    text=extract_text((payload or {}).get("result") if isinstance(payload,dict) else payload)
    if not text:
        logger.warning("Workers AI unrecognized response shape: %s",json.dumps(payload,ensure_ascii=False)[:2000])
        raise RuntimeError("Workers AI returned no readable response")
    return text.strip()

def _ai_case_record(c):
    return {
      "id":c.get("id") or "",
      "driver":c.get("report_driver") or c.get("driver_name") or "",
      "group":c.get("group_name") or c.get("group") or "",
      "unit":c.get("unit_number") or "",
      "type":(c.get("vehicle_type") or "").lower(),
      "issue":c.get("issue_text") or c.get("description") or "",
      "description":c.get("description") or "",
      "notes":c.get("notes") or "",
      "solved_by":c.get("closed_by_name") or c.get("closed_by") or c.get("resolved_by") or c.get("agent_name") or "",
      "resolution":c.get("resolution") or c.get("solution") or c.get("close_notes") or c.get("closing_notes") or c.get("resolution_notes") or "",
      "status":c.get("status") or "",
      "opened":c.get("opened_at") or "",
      "closed":c.get("closed_at") or ""
    }

def _ai_problem_family(text):
    """Classify broad maintenance system so lexical overlap cannot mix unrelated failures."""
    t=" "+str(text or "").lower()+" "
    groups=[
      ("tire_wheel", ("tire","tyre","flat tire","wheel leak","rim","bead leak","tire air","puncture")),
      ("pneumatic_air", ("air leak","air line","gladhand","glad hand","air hose","air fitting","air pressure","air tank","air dryer","brake chamber","air bag","airbag","o-ring","o ring")),
      ("coolant", ("coolant","antifreeze","radiator","coolant leak","water pump")),
      ("refrigeration", ("refrigerant","freon","reefer","thermo king","carrier unit","evaporator","condenser","setpoint")),
      ("fuel", ("fuel leak","diesel leak","fuel line","fuel filter","injector")),
      ("oil", ("oil leak","engine oil","gear oil","hydraulic oil")),
      ("electrical", ("battery","alternator","voltage","wiring","wire","connector","fuse","relay","electrical","no power")),
      ("brake", ("brake","brakes","caliper","rotor","brake pad","slack adjuster")),
      ("engine", ("engine","motor","turbo","boost","def","scr","dpf","check engine","spn","fmi")),
    ]
    scores=[]
    for name,words in groups:
        score=sum(3 if (" "+w+" ") in t else 1 for w in words if w in t)
        if score:scores.append((score,name))
    return max(scores)[1] if scores else "general"

def _ai_case_search_text(c):
    r=_ai_case_record(c)
    return " ".join(str(r.get(k) or "") for k in ("type","issue","description","notes","resolution"))

def _ai_relevant_cases(query,cases,limit=8):
    """Retrieve history, then enforce subsystem compatibility before exposing it to the model/UI."""
    qfam=_ai_problem_family(query)
    ranked=ranked_cases(query,cases,max(limit*5,30))
    compatible=[]; fallback=[]
    for c in ranked:
        cfam=_ai_problem_family(_ai_case_search_text(c))
        if qfam=="general" or cfam==qfam:
            compatible.append(c)
        elif cfam=="general":
            fallback.append(c)
    # General/underspecified records can support a query, but never displace explicit same-system matches.
    return (compatible+fallback)[:limit]

def _ai_similar_case_cards(query,limit=5):
    """UI-safe case summaries: operational facts only, no internal IDs."""
    cases=[c for c in load_cases() if not is_testing(c)]
    out=[]
    for c in _ai_relevant_cases(query,cases,limit):
        r=_ai_case_record(c)
        vehicle=(r.get("type") or "").strip().lower()
        unit=(r.get("unit") or "").strip()
        out.append({
            "reported_by":" / ".join(x for x in (r.get("driver"),r.get("group")) if x),
            "driver":r.get("driver") or "",
            "group":r.get("group") or "",
            "vehicle":vehicle,
            "unit":unit,
            "issue":_ai_clip(r.get("issue") or r.get("description"),420),
            "notes":_ai_clip(r.get("notes"),520),
            "solved_by":r.get("solved_by") or "",
            "resolution":_ai_clip(r.get("resolution"),650),
            "status":r.get("status") or "",
            "opened":r.get("opened") or "",
            "closed":r.get("closed") or ""
        })
    return [{k:v for k,v in x.items() if v not in ("",None,[])} for x in out]

def _ai_clip(value,limit):
    text=re.sub(r"\s+"," ",str(value or "")).strip()
    return text if len(text)<=limit else text[:max(0,limit-1)]+"…"

def _ai_context(query="",limit=18):
    """Build a small, relevant context instead of dumping fleet history into every prompt."""
    cases=[c for c in load_cases() if not is_testing(c)]
    terms=tokens(query)
    matched=_ai_relevant_cases(query,cases,limit)
    compact=[]
    for c in matched:
        r=_ai_case_record(c)
        item={"unit":r["unit"],"type":r["type"],"driver":r["driver"],"group":r["group"],
          "issue":_ai_clip(r["issue"],500),"description":_ai_clip(r["description"],700),
          "notes":_ai_clip(r["notes"],650),"solved_by":r["solved_by"],
          "resolution":_ai_clip(r["resolution"],900),"status":r["status"],
          "opened":r["opened"],"closed":r["closed"]}
        compact.append({k:v for k,v in item.items() if v not in ("",None,[])})
    notes=[]
    if "_read_knowledge" in globals():
        raw=_read_knowledge() or []
        # Only a few recent team notes; large note stores must not inflate every chat.
        for n in [n for n in raw if tokens(query) & tokens(json.dumps(n,ensure_ascii=False))][-4:]:
            if isinstance(n,dict):
                notes.append({k:_ai_clip(n.get(k),500) for k in ("part","note","author","updated") if n.get(k)})
            else: notes.append(_ai_clip(n,500))
    approved=[]
    for x in (_ai_knowledge_matches(query,6) if "_ai_knowledge_matches" in globals() else []):
        approved.append({"id":x.get("id"),"title":_ai_clip(x.get("title"),140),
          "tags":(x.get("tags") or [])[:8],"content":knowledge_excerpt(x.get("content"),query,2200)})
    return {"cases":compact,"knowledge_notes":notes,"approved_maintenance_knowledge":approved,
            "reviewed_lessons":learning_store.matches(query),
            "fleet_experience":fleet_knowledge_store.matches(query,6),
            "historical_chat_experience":_ai_chat_history_matches(query,5),
            "retrieval":{"historical_cases_total":len(cases),"cases_in_prompt":len(compact),"approved_items_in_prompt":len(approved),"historical_chats_in_prompt":len(_ai_chat_history_matches(query,5))}}


AI_PARTS_FILE = Path(__file__).resolve().parent / "data" / "parts_manual_ai.json"
def _read_ai_parts_manual():
    try:
        data=json.loads(AI_PARTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data,list) else []
    except Exception as e:
        logger.warning("AI parts manual read failed: %s",e); return []

def _ai_parts_matches(query,limit=8):
    terms=_maintenance_tokens(query) if "_maintenance_tokens" in globals() else set(re.findall(r"[a-z0-9_-]{3,}",str(query).lower()))
    generic={"truck","trailer","unit","issue","problem","repair","service","check","maintenance","part","system"}
    terms={x for x in terms if x not in generic}
    ranked=[]
    for p in _read_ai_parts_manual():
        name=str(p.get("name") or ""); issues=" ".join(str(x) for x in (p.get("issues") or []))
        hay=" ".join([name,str(p.get("cat") or ""),str(p.get("keywords") or ""),issues,str(p.get("what") or ""),str(p.get("works") or "")]).lower()
        score=sum((5 if t in name.lower() else 3 if t in str(p.get("keywords") or "").lower() else 1) for t in terms if t in hay)
        if score: ranked.append((score,p))
    ranked.sort(key=lambda x:(-x[0],str(x[1].get("name") or "")))
    out=[]
    for score,p in ranked[:limit]:
        out.append({"id":p.get("id"),"name":p.get("name"),"category":p.get("cat"),"location":p.get("loc"),"keywords":p.get("keywords") or "","issues":p.get("issues") or [],
                    "checks":p.get("checks") or [],"guidance":p.get("fix") or "","source":p.get("source") or "","source_url":p.get("url") or ""})
    return out


NOTIFICATIONS_FILE = DATA_DIR / "dashboard_notifications.json"

def _read_notifications():
    try:
        if NOTIFICATIONS_FILE.exists():
            data=json.loads(NOTIFICATIONS_FILE.read_text(encoding="utf-8")); return data if isinstance(data,list) else []
    except Exception as e: logger.warning("Notification read failed: %s",e)
    return []

def _write_notifications(items):
    NOTIFICATIONS_FILE.parent.mkdir(parents=True,exist_ok=True); tmp=NOTIFICATIONS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items[-1000:],ensure_ascii=False,indent=2),encoding="utf-8"); tmp.replace(NOTIFICATIONS_FILE)

def _notify_user(kind,title,message,severity="info"):
    try:
        items=_read_notifications(); now=datetime.now().astimezone().isoformat(timespec="seconds")
        # de-dupe identical system failures within the latest entries
        key=_ai_user_key() if "_ai_user_key" in globals() else "user"
        for n in reversed(items[-30:]):
            if n.get("user")==key and n.get("title")==title and n.get("message")==message and not n.get("seen"):
                return
        items.append({"id":uuid.uuid4().hex,"user":key,"kind":kind,"title":title,"message":message,
                      "severity":severity,"seen":False,"created_at":now})
        _write_notifications(items)
    except Exception as e: logger.warning("Notification write failed: %s",e)

@app.route("/api/notifications")
def api_notifications():
    if not session.get("user"):return jsonify({"error":"unauthorized"}),401
    key=_ai_user_key(); items=[n for n in _read_notifications() if n.get("user")==key]
    # Operational users only need actionable fleet notifications. AI/system diagnostics stay developer-only.
    if not _ai_is_trainer():
        items=[n for n in items if n.get("kind") not in ("ai","system","developer")]
    items.sort(key=lambda x:x.get("created_at") or "",reverse=True)
    return jsonify({"items":items[:100],"unseen":sum(1 for n in items if not n.get("seen"))})

@app.route("/api/notifications/<nid>/seen",methods=["POST"])
def api_notification_seen(nid):
    if not session.get("user"):return jsonify({"error":"unauthorized"}),401
    key=_ai_user_key(); items=_read_notifications()
    for n in items:
        if n.get("id")==nid and n.get("user")==key:n["seen"]=True
    _write_notifications(items); return jsonify({"ok":True})

@app.route("/api/notifications/seen-all",methods=["POST"])
def api_notifications_seen_all():
    if not session.get("user"):return jsonify({"error":"unauthorized"}),401
    key=_ai_user_key(); items=_read_notifications()
    for n in items:
        if n.get("user")==key:n["seen"]=True
    _write_notifications(items); return jsonify({"ok":True})

AI_KNOWLEDGE_FILE = DATA_DIR / "ai_maintenance_knowledge.json"

def _ai_is_trainer():
    user=session.get("user") or {}
    return isinstance(user,dict) and user.get("role","agent") == "developer"

def _read_ai_knowledge():
    try:
        if AI_KNOWLEDGE_FILE.exists():
            data=json.loads(AI_KNOWLEDGE_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data,list) else []
    except Exception as e: logger.warning("AI knowledge read failed: %s",e)
    return []

def _write_ai_knowledge(items):
    AI_KNOWLEDGE_FILE.parent.mkdir(parents=True,exist_ok=True)
    tmp=AI_KNOWLEDGE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding="utf-8"); tmp.replace(AI_KNOWLEDGE_FILE)

def _ai_chat_history_matches(query,limit=6):
    """Retrieve relevant prior maintenance conversations across users as untrusted experience.

    Owners are intentionally not exposed to the model. Old assistant answers are evidence of
    prior troubleshooting only and never become approved repair guidance automatically.
    """
    wanted=tokens(query)
    if not wanted:return []
    ranked=[]
    for _owner,chats in chat_store.all_chats():
        for chat in chats:
            messages=chat.get("messages") or []
            for i,message in enumerate(messages):
                if message.get("role")!="user":continue
                question=str(message.get("content") or "")
                answer=next((str(m.get("content") or "") for m in messages[i+1:i+3] if m.get("role")=="assistant"),"")
                hay=question+" "+answer
                overlap=wanted & tokens(hay)
                if not overlap:continue
                score=sum(3 if t in COMPONENTS else 1 for t in overlap)
                ranked.append((score,str(chat.get("updated_at") or chat.get("created_at") or ""),{
                    "conversation":_ai_clip(chat.get("title") or "Maintenance conversation",120),
                    "question":_ai_clip(question,900),
                    "prior_ai_answer":_ai_clip(answer,1400),
                    "evidence_quality":"historical chat - unverified"
                }))
    ranked.sort(key=lambda x:(-x[0],x[1]),reverse=False)
    return [x[2] for x in ranked[:limit]]

def _ai_knowledge_matches(query,limit=12):
    items=_read_ai_knowledge(); q=tokens(query)
    def score(x):
        txt=(str(x.get("title") or "")+" "+str(x.get("content") or "")+" "+" ".join(x.get("tags") or [])).lower()
        return len(q & tokens(txt))
    ranked=sorted(items,key=lambda x:(score(x),x.get("updated_at") or x.get("created_at") or ""),reverse=True)
    hits=[x for x in ranked if score(x)>0]
    return hits[:limit]

@app.route("/api/ai/knowledge",methods=["GET","POST"])
def api_ai_knowledge():
    if not session.get("user"):return jsonify({"error":"unauthorized"}),401
    if not _ai_is_trainer():return jsonify({"error":"forbidden"}),403
    items=_read_ai_knowledge()
    if request.method=="GET":
        return jsonify({"items":sorted(items,key=lambda x:x.get("updated_at") or x.get("created_at") or "",reverse=True),
                        "case_count":len([c for c in load_cases() if not is_testing(c)])})
    data=request.get_json(silent=True) or {}; content=str(data.get("content") or "").strip()[:50000]
    if not content:return jsonify({"error":"Knowledge content is required"}),400
    now=_now_iso() if "_now_iso" in globals() else datetime.now().astimezone().isoformat(timespec="seconds")
    item={"id":uuid.uuid4().hex,"title":str(data.get("title") or "Maintenance knowledge")[:120],
          "content":content, "tags":[str(x)[:50] for x in (data.get("tags") or []) if str(x).strip()][:12],
          "tag_colors":{str(k)[:50]:str(v) for k,v in (data.get("tag_colors") or {}).items() if str(v) in {"red","orange","yellow","green","blue","purple","teal","gray"}},
          "source":"manual","created_at":now,"updated_at":now,"created_by":_ai_user_key() if "_ai_user_key" in globals() else "admin"}
    items.append(item); _write_ai_knowledge(items); return jsonify(item),201

@app.route("/api/ai/knowledge/upload",methods=["POST"])
def api_ai_knowledge_upload():
    if not session.get("user"):return jsonify({"error":"unauthorized"}),401
    if not _ai_is_trainer():return jsonify({"error":"forbidden"}),403
    f=request.files.get("file")
    if not f or not f.filename:return jsonify({"error":"File is required"}),400
    ext=Path(f.filename).suffix.lower()
    if ext not in (".txt",".md",".csv",".json",".pdf",".docx",".xlsx"):
        return jsonify({"error":"Use TXT, MD, CSV, JSON, PDF, DOCX or XLSX"}),400
    raw=f.read(5_000_001)
    if len(raw)>5_000_000:return jsonify({"error":"File is too large (5 MB max)"}),400
    try:
        if ext in (".txt",".md",".csv",".json"):
            try: content=raw.decode("utf-8")
            except UnicodeDecodeError: content=raw.decode("latin-1")
        elif ext==".pdf":
            from pypdf import PdfReader
            reader=PdfReader(io.BytesIO(raw)); content="\n".join((page.extract_text() or "") for page in reader.pages[:100])
        elif ext==".docx":
            from docx import Document
            doc=Document(io.BytesIO(raw)); content="\n".join(p.text for p in doc.paragraphs)
        else:
            from openpyxl import load_workbook
            wb=load_workbook(io.BytesIO(raw),read_only=True,data_only=True); rows=[]
            for ws in wb.worksheets[:30]:
                rows.append("SHEET: "+ws.title)
                for row in ws.iter_rows(values_only=True):
                    vals=[str(v) for v in row if v is not None]
                    if vals:rows.append(" | ".join(vals))
                    if sum(len(x) for x in rows)>150000:break
            content="\n".join(rows)
    except Exception as exc:
        logger.warning("Knowledge file extraction failed: %s",exc);return jsonify({"error":"Could not extract readable content from this file"}),400
    content=content.strip()
    if not content:return jsonify({"error":"No readable text was found in this file"}),400
    now=datetime.now().astimezone().isoformat(timespec="seconds"); items=_read_ai_knowledge()
    item={"id":uuid.uuid4().hex,"title":Path(f.filename).name[:120],"content":content[:150000],
          "tags":["uploaded",ext.lstrip(".")],"source":"file","created_at":now,"updated_at":now,"created_by":_ai_user_key()}
    items.append(item); _write_ai_knowledge(items); return jsonify(item),201

@app.route("/api/ai/knowledge/<item_id>",methods=["PUT","DELETE"])
def api_ai_knowledge_delete(item_id):
    if not session.get("user"):return jsonify({"error":"unauthorized"}),401
    if not _ai_is_trainer():return jsonify({"error":"forbidden"}),403
    items=_read_ai_knowledge(); item=next((x for x in items if str(x.get("id"))==item_id),None)
    if not item:return jsonify({"error":"Not found"}),404
    if request.method=="PUT":
        data=request.get_json(silent=True) or {}
        content=str(data.get("content") if "content" in data else item.get("content") or "").strip()[:120000]
        if not content:return jsonify({"error":"Knowledge content is required"}),400
        item["title"]=str(data.get("title") if "title" in data else item.get("title") or "Maintenance knowledge").strip()[:120]
        item["content"]=content
        if "tags" in data:item["tags"]=[str(x).strip()[:50] for x in (data.get("tags") or []) if str(x).strip()][:12]
        if "tag_colors" in data:item["tag_colors"]={str(k)[:50]:str(v) for k,v in (data.get("tag_colors") or {}).items() if str(v) in {"red","orange","yellow","green","blue","purple","teal","gray"}}
        item["updated_at"]=_now_iso()
        _write_ai_knowledge(items);return jsonify(item)
    new=[x for x in items if str(x.get("id"))!=item_id]
    _write_ai_knowledge(new); return jsonify({"ok":True})

def _extract_ai_chat_file(f):
    name=Path(f.filename or "attachment").name
    ext=Path(name).suffix.lower()
    if ext in (".mp4",".mov",".webm"):
        raw=f.read(MAX_VIDEO_BYTES+1)
        parsed=process_video(raw,ext)
        parsed['blob']=chat_store.put_blob(raw)
        audio=parsed.pop('_audio_bytes',None)
        if audio:parsed['audio_blob']=chat_store.put_blob(audio)
        return name,parsed
    if ext in (".wav",".mp3",".m4a",".ogg",".aac"):
        raw=f.read(25_000_001)
        if len(raw)>25_000_000: raise ValueError("Audio must be under 25 MB")
        if not raw: raise ValueError("Audio is empty")
        transcript=transcribe_audio(raw)
        return name,{"kind":"audio","mime":f.mimetype or "audio/mpeg","transcript":transcript,"audio_status":"transcribed" if transcript else "no_speech","blob":chat_store.put_blob(raw)}
    raw=f.read(5_000_001)
    if len(raw)>5_000_000: raise ValueError("File is too large (5 MB max)")
    if ext in (".jpg",".jpeg",".png",".webp"):
        if not raw: raise ValueError("Image is empty")
        from PIL import Image, ImageOps
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.width*image.height > 25_000_000:raise ValueError("Image exceeds 25 megapixels")
                image=ImageOps.exif_transpose(image).convert("RGB")
                image.thumbnail((1800,1800))
                buffer=io.BytesIO();image.save(buffer,format="JPEG",quality=90)
                raw=buffer.getvalue()
        except Exception as exc:raise ValueError("Unable to read image. Use a valid JPG, PNG or WEBP under 25 megapixels.") from exc
        ext=".jpg"
        mime={".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png",".webp":"image/webp"}[ext]
        return name, {"kind":"image","mime":mime,"data":"data:"+mime+";base64,"+base64.b64encode(raw).decode("ascii")}
    if ext in (".txt",".md",".csv",".json"):
        try: text=raw.decode("utf-8")
        except UnicodeDecodeError: text=raw.decode("latin-1")
    elif ext==".pdf":
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(raw)); text="\n".join((p.extract_text() or "") for p in reader.pages[:80])
    elif ext==".docx":
        from docx import Document
        doc=Document(io.BytesIO(raw)); text="\n".join(p.text for p in doc.paragraphs)
    elif ext==".xlsx":
        from openpyxl import load_workbook
        wb=load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
        rows=[]
        for ws in wb.worksheets[:20]:
            rows.append("SHEET: "+ws.title)
            for row in ws.iter_rows(values_only=True):
                vals=[str(v) for v in row if v is not None]
                if vals: rows.append(" | ".join(vals))
                if sum(len(x) for x in rows)>120000: break
        text="\n".join(rows)
    elif ext==".zip":
        import zipfile
        z=zipfile.ZipFile(io.BytesIO(raw))
        names=[x.filename for x in z.infolist() if not x.is_dir()][:500]
        snippets=[]
        for info in z.infolist()[:100]:
            if info.is_dir() or info.file_size>500000: continue
            if Path(info.filename).suffix.lower() in (".txt",".md",".csv",".json",".py",".js",".css",".html"):
                try:
                    snippets.append("\nFILE: "+info.filename+"\n"+z.read(info).decode("utf-8","replace")[:12000])
                except Exception: pass
        text="ZIP CONTENTS:\n"+"\n".join(names)+"\n"+"".join(snippets)
    else: raise ValueError("Supported files: MP4, MOV, WEBM, WAV, MP3, M4A, OGG, AAC, JPG, PNG, WEBP, PDF, DOCX, XLSX, ZIP, TXT, MD, CSV and JSON")
    text=text.strip()
    if not text: raise ValueError("No readable text was found in this file")
    mime={".pdf":"application/pdf",".docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          ".xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",".zip":"application/zip",
          ".csv":"text/csv",".json":"application/json",".md":"text/markdown",".txt":"text/plain"}.get(ext,"application/octet-stream")
    return name, {"kind":"document","mime":mime,"content":text[:120000],"raw_b64":base64.b64encode(raw).decode("ascii")}

AI_CHAT_FILE = DATA_DIR / "ai_chats.json"

def _ai_user_key():
    user=session.get("user") or {}
    if isinstance(user,dict):
        return str(user.get("id") or user.get("username") or user.get("email") or user.get("first_name") or "user")
    return str(user or "user")

def _user_chats():
    return chat_store.read(_ai_user_key())

def _save_user_chats(chats):
    chat_store.save(_ai_user_key(),chats)

def _chat_title(text):
    clean=re.sub(r"\s+"," ",str(text or "")).strip()
    if not clean:
        return "New maintenance chat"
    # Keep mobile/desktop conversation labels concise and scannable.
    words=clean.split()[:5]
    title=" ".join(words).strip(" -:,.!?;")
    return title or "New maintenance chat"

def _now_iso():
    return chicago_now().isoformat(timespec="seconds")

@app.route("/api/ai/chats",methods=["GET","POST"])
@ai_serialized
def api_ai_chats():
    if not session.get("user"):return jsonify({"error":"unauthorized"}),401
    chats=_user_chats()
    if request.method=="GET":
        summaries=[{"id":c.get("id"),"title":c.get("title") or "Maintenance chat","created_at":c.get("created_at"),
                    "updated_at":c.get("updated_at"),"message_count":len(c.get("messages") or [])} for c in chats]
        summaries.sort(key=lambda x:x.get("updated_at") or "",reverse=True)
        return jsonify({"items":summaries})
    data=request.get_json(silent=True) or {}; title=str(data.get("title") or "New maintenance chat")[:80]
    cid=uuid.uuid4().hex
    chat={"id":cid,"title":title,"created_at":_now_iso(),"updated_at":_now_iso(),"messages":[]}
    chats.append(chat)
    try:
        _save_user_chats(chats)
    except Exception as e:
        logger.exception("AI chat create/save failed")
        return jsonify({"error":"Unable to create chat. Chat storage is not writable.","detail":str(e)[:240]}),500
    return jsonify(chat),201

@app.route("/api/ai/chats/<chat_id>",methods=["GET","DELETE","PATCH"])
@ai_serialized
def api_ai_chat_item(chat_id):
    if not session.get("user"):return jsonify({"error":"unauthorized"}),401
    chats=_user_chats(); chat=next((c for c in chats if str(c.get("id"))==chat_id),None)
    if not chat:return jsonify({"error":"Chat not found"}),404
    if request.method=="GET":
        safe=dict(chat); safe["attachments"]=[{k:a.get(k) for k in ("id","name","kind","mime","created_at","duration","audio_status","warning") if a.get(k) is not None} for a in (chat.get("attachments") or [])]
        return jsonify(safe)
    if request.method=="DELETE":
        _save_user_chats([c for c in chats if str(c.get("id"))!=chat_id]); return jsonify({"ok":True})
    data=request.get_json(silent=True) or {}; title=str(data.get("title") or "").strip()
    if title:chat["title"]=title[:80];chat["updated_at"]=_now_iso();_save_user_chats(chats)
    return jsonify(chat)

@app.route("/api/ai/knowledge/options")
def api_ai_knowledge_options():
    if not session.get("user"):return jsonify({"error":"unauthorized"}),401
    items=_read_ai_knowledge()
    return jsonify({"items":[{"id":x.get("id"),"title":x.get("title"),"tags":x.get("tags") or [],"tag_colors":x.get("tag_colors") or {},"source":x.get("source")} for x in items]})

@app.route("/api/ai/chats/<chat_id>/files",methods=["POST"])
@ai_serialized
def api_ai_chat_file(chat_id):
    if not session.get("user"):return jsonify({"error":"unauthorized"}),401
    chats=_user_chats(); chat=next((c for c in chats if str(c.get("id"))==chat_id),None)
    if not chat:return jsonify({"error":"Chat not found"}),404
    if len(chat.get("attachments",[]))>=48:return jsonify({"error":"This chat has 48 files. Start a new chat or remove older files."}),400
    f=request.files.get("file")
    if not f or not f.filename:return jsonify({"error":"File is required"}),400
    try:name,parsed=_extract_ai_chat_file(f)
    except Exception as e:return jsonify({"error":str(e)}),400
    item={"id":uuid.uuid4().hex,"name":name,"created_at":_now_iso(),**parsed}
    chat.setdefault("attachments",[]).append(item);chat["updated_at"]=_now_iso();_save_user_chats(chats)
    return jsonify({"id":item["id"],"name":name,"kind":item.get("kind"),"mime":item.get("mime"),"audio_status":item.get("audio_status"),"warning":item.get("warning")}),201

@app.route("/api/ai/chats/<chat_id>/files/<file_id>",methods=["GET","DELETE"])
@ai_serialized
def api_ai_chat_file_delete(chat_id,file_id):
    if not session.get("user"):return jsonify({"error":"unauthorized"}),401
    chats=_user_chats(); chat=next((c for c in chats if str(c.get("id"))==chat_id),None)
    if not chat:return jsonify({"error":"Chat not found"}),404
    item=next((x for x in (chat.get("attachments") or []) if str(x.get("id"))==file_id),None)
    if not item:return jsonify({"error":"File not found"}),404
    if request.method=="GET":
        if item.get("kind") in ("video","audio") and item.get("blob"):
            return send_file(chat_store.blob_path(item["blob"]),mimetype=item.get("mime"),download_name=item.get("name"),conditional=True)
        item=chat_store.hydrate(item)
        if item.get("kind")=="image" and item.get("data"):
            try:
                raw=base64.b64decode(item["data"].split(",",1)[-1]); mime=item.get("mime") or "image/jpeg"
                return Response(raw,mimetype=mime,headers={"Content-Disposition":'inline; filename="'+Path(item.get("name") or "image").name.replace('"','')+'"'})
            except Exception:return jsonify({"error":"Image unavailable"}),404
        if item.get("raw_b64"):
            raw=base64.b64decode(item["raw_b64"]); mime=item.get("mime") or "application/octet-stream"
            return Response(raw,mimetype=mime,headers={"Content-Disposition":'attachment; filename="'+Path(item.get("name") or "attachment").name.replace('"','')+'"'})
        return jsonify({"error":"Original file is unavailable for this older attachment"}),404
    chat["attachments"]=[x for x in (chat.get("attachments") or []) if str(x.get("id"))!=file_id];chat["updated_at"]=_now_iso();_save_user_chats(chats);return jsonify({"ok":True})

@app.route("/api/ai/chats/<chat_id>/context",methods=["PATCH"])
@ai_serialized
def api_ai_chat_context(chat_id):
    if not session.get("user"):return jsonify({"error":"unauthorized"}),401
    chats=_user_chats(); chat=next((c for c in chats if str(c.get("id"))==chat_id),None)
    if not chat:return jsonify({"error":"Chat not found"}),404
    data=request.get_json(silent=True) or {}; valid={str(x.get("id")) for x in _read_ai_knowledge()};ids=[]
    for x in data.get("knowledge_ids") or []:
        x=str(x)
        if x in valid and x not in ids:ids.append(x)
    chat["knowledge_ids"]=ids[:20];chat["updated_at"]=_now_iso();_save_user_chats(chats);return jsonify({"ok":True,"knowledge_ids":chat["knowledge_ids"]})

@app.route("/api/ai/status")
def api_ai_status():
    if not session.get("user"): return jsonify({"error":"unauthorized"}),401
    return jsonify({"configured":bool(CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN),"model":KURTEX_AI_MODEL,"can_train":_ai_is_trainer(),"knowledge_count":len(_read_ai_knowledge())})


def _ai_web_search(query, limit=7):
    return search_research(query,limit)

@app.route("/api/ai/chat",methods=["POST"])
@ai_serialized
def api_ai_chat():
    if not session.get("user"): return jsonify({"error":"unauthorized"}),401
    data=request.get_json(silent=True) or {}; message=str(data.get("message") or "").strip()[:4000]
    page=str(data.get("page") or "dashboard")[:80]; chat_id=str(data.get("chat_id") or "").strip()
    requested_attachment_ids=[str(x) for x in (data.get("attachment_ids") or []) if str(x).strip()][:12]
    if not message:return jsonify({"error":"Message is required"}),400
    request_id=str(data.get("request_id") or "")[:80]
    chats=_user_chats()
    if request_id:
        for saved_chat in chats:
            saved=saved_chat.get("messages",[])
            for index,turn in enumerate(saved[:-1]):
                if turn.get("request_id")!=request_id:continue
                if turn.get("content")!=message or (chat_id and saved_chat.get("id")!=chat_id):return jsonify(error="Request ID conflicts with an earlier message"),409
                answer=saved[index+1]
                return jsonify(answer=answer.get("content",""),message_id=answer.get("id"),chat_id=saved_chat["id"],title=saved_chat.get("title"),sources=answer.get("sources",[]),research_status=answer.get("research_status"),similar_cases=answer.get("similar_cases",[]),parts=answer.get("parts",[]),replayed=True)
    chat=next((c for c in chats if str(c.get("id"))==chat_id),None)
    if chat_id and chat is None:
        return jsonify({"error":"Chat not found"}),404
    if chat is None:
        chat={"id":uuid.uuid4().hex,"title":_chat_title(message),"created_at":_now_iso(),"updated_at":_now_iso(),"messages":[]}
        chats.append(chat)
    history=chat.get("messages") if isinstance(chat.get("messages"),list) else []
    stage="context"
    started=time.monotonic()
    try:
        available={str(a.get("id")):a for a in chat.get("attachments",[])}
        if any(i not in available for i in requested_attachment_ids):
            return jsonify({"error":"An attached file is no longer available. Attach it again."}),400
        # Follow-ups reuse only the latest user turn's images, never arbitrary old uploads.
        active_ids=requested_attachment_ids
        if not active_ids:
            active_ids=next((m.get("attachment_ids",[]) for m in reversed(history) if m.get("role")=="user"),[])
        active_media=[chat_store.hydrate(available[i]) for i in active_ids if i in available and available[i].get("kind") in ("image","video","audio")]
        image_items=[];driver_reports=[]
        for media in active_media:
            if media.get("kind")=="image":image_items.append(media)
            elif media.get("kind")=="video":
                image_items.extend([{**frame,"name":media.get("name", "Video")+" - "+frame["name"]} for frame in media.get("frames",[])])
                driver_reports.append({"media":"video","name":media.get("name"),"transcript":media.get("transcript",""),"audio_status":media.get("audio_status"),"warning":media.get("warning","")})
            elif media.get("kind")=="audio":
                driver_reports.append({"media":"audio","name":media.get("name"),"transcript":media.get("transcript",""),"audio_status":media.get("audio_status"),"warning":media.get("warning","")})
        if len(image_items)>12:return jsonify({"error":"Too many visual inputs. Send one video and up to four photos per message."}),400
        image_urls=[a["data"] for a in image_items if a.get("data")]
        visual_description_only=_ai_visual_description_request(message,bool(image_urls))
        wants_similar_cases=bool(data.get("similar_cases")) or _ai_explicit_similar_case_request(message)
        observations=""
        if image_urls:
            stage="visual_inspection"
            labels="\n".join(str(a.get("name")) for a in image_items)
            observations=_cf_ai([
                {"role":"system","content":INSPECTION_POLICY},
                {"role":"user","content":"Inspect all supplied images independently. Report visible defects, location and uncertainty in at most 220 words. Finish with 'Search terms:' and only the observed component/symptom terms. Do not diagnose hidden causes. Labels in image order:\n"+labels+"\nQuestion: "+message}],650,.1,images=image_urls)
        visual_terms=observations.rsplit("Search terms:",1)[-1][:350] if "Search terms:" in observations else ""
        retrieval_query=message+" "+visual_terms+" "+" ".join(x["transcript"][:1200] for x in driver_reports)
        ctx=_ai_context(retrieval_query,10)
        ctx["visual_observations"]=observations
        ctx["image_labels"]=[a.get("name") for a in image_items]
        ctx["driver_speech_reports"]=driver_reports
        ctx["similar_cases_requested"]=wants_similar_cases
        ctx["visual_description_only"]=visual_description_only
        # Chat-scoped context: explicitly attached files and verified knowledge stay with this conversation.
        attachments=[]
        for a in (chat.get("attachments") or [])[-12:]:
            attachments.append({"name":_ai_clip(a.get("name"),180),"content":knowledge_excerpt(a.get("content"),retrieval_query,3500)}) if a.get("kind") not in ("image","video","audio") else None
        selected_ids=set(str(x) for x in (chat.get("knowledge_ids") or []))
        selected=[]
        for k in _read_ai_knowledge():
            if str(k.get("id")) in selected_ids:
                selected.append({"id":k.get("id"),"title":_ai_clip(k.get("title"),160),"tags":(k.get("tags") or [])[:10],"content":knowledge_excerpt(k.get("content"),retrieval_query,3500)})
        ctx["chat_attachments"]=attachments
        ctx["chat_selected_knowledge"]=selected
        ctx["parts_manual_matches"]=_ai_parts_matches(retrieval_query,5)
        # Fresh public technical context complements fleet history. Failure/no API key is non-fatal.
        research=_ai_web_search(retrieval_query,7) if data.get("web_search",True) is not False else {"status":"disabled","results":[]}
        ctx["web_search_results"]=research["results"]
        ctx["web_search_status"]=research["status"]
        stage="prompt"
        messages=[{"role":"system","content":KURTEX_AI_SYSTEM+INSPECTION_POLICY},{"role":"system","content":
          "Current Kurtex page: "+page+"\nRead-only Kurtex context follows. Never claim a record exists unless present here.\nKURTEX CONTEXT:\n"+
          json.dumps(ctx,ensure_ascii=False)}, {"role":"system","content":"""Act as a professional maintenance triage assistant. Use the attached image/document, selected Knowledge, Parts Manual, relevant Kurtex history, and WEB SEARCH RESULTS together when they are relevant to the user's actual request.

IMPORTANT INTENT RULES:
- If visual_description_only is true, answer the user's visual question directly from VISUAL OBSERVATIONS. Do not turn it into a diagnostic report. Do not mention fleet history, similar cases, Parts Manual, repair procedures, or web research unless the user asked for them. Use natural prose and clearly separate what is visible from what cannot be confirmed from the image.
- Similar fleet cases are optional evidence. Discuss them only when similar_cases_requested is true or the user explicitly asks for previous/history/similar cases. Never force a Similar Cases section into an unrelated answer.
- A simple question deserves a simple direct answer. Use the full diagnostic workflow only for diagnosis/troubleshooting requests.

For a diagnostic request, prefer this workflow:
1. Quick assessment — 2-4 sentences stating what is observed vs what is only suspected.
2. Questions to confirm — ask the few highest-value questions (unit make/model, reefer model, fault/alarm code, temperatures/pressures, when symptom started, recent work/damage) only when missing.
3. Diagnostic checks — only checks relevant to the evidence, usually 2-4. Start with safe/simple checks. For each check state: what to inspect/test -> what result matters -> what that result points to.
4. Most likely causes — ranked by evidence, not a random list.
5. Recommended next action — what to do now, when to stop operation/escalate, and what a technician should verify.
6. Matching parts — only when a verified Parts Manual/Knowledge source explicitly supports the part/number. Never invent a part number.
7. Similar Kurtex cases — ONLY when similar_cases_requested is true. Present matching cases as clean, arranged TEXT in the AI reply; the website does not render duplicate Similar Fleet Cases cards. For each useful match, keep factual fields concise (Unit, Driver/Group, Reported issue, Resolution, and optionally Resolved by). Resolution must come ONLY from explicit repair/fix text. If missing, write "Resolution: No recorded resolution". Never use solved_by/resolved_by/closed_by as the resolution. Never invent a match percentage. Historical cases are evidence, not proof. Explicitly ignore misleading keyword matches (for example tire air leaks when diagnosing a truck/trailer pneumatic air-system leak).
8. Sources — compact one-line source list only. Do not add blank bullet lines or excessive spacing.

For images: describe only what is actually visible; do not infer hidden damage as fact. For web results: use them to improve troubleshooting and identify useful technical references, but do not present a search snippet as an OEM procedure. If make/model or alarm code is needed for an exact procedure, ask for it.

Formatting: use clean headings, compact numbered steps, and single-spaced bullets. Do not output literal backslashes before line breaks, bullet characters, or markdown punctuation. Avoid vague advice such as merely "inspect electrical" — say what should be checked and what the result means."""}]
        for item in history[-8:]:
            role=item.get("role"); content=_ai_clip(item.get("content"),1800)
            if role in ("user","assistant") and content:messages.append({"role":role,"content":content})
        messages.append({"role":"user","content":message})
        stage="workers_ai"
        answer=_cf_ai(messages,2200,.1,images=image_urls)
        stage="save_chat"
        answer_id=uuid.uuid4().hex
        similar_cases=_ai_similar_case_cards(retrieval_query,5) if wants_similar_cases else []
        part_matches=[] if visual_description_only else (ctx.get("parts_manual_matches") or [])
        history.extend([{"id":uuid.uuid4().hex,"role":"user","content":message,"at":_now_iso(),"attachment_ids":active_ids,"request_id":request_id},
                        {"id":answer_id,"role":"assistant","content":answer,"at":_now_iso(),"sources":research["results"],"research_status":research["status"],"similar_cases":similar_cases,"parts":part_matches}])
        chat["messages"]=history[-80:]; chat["updated_at"]=_now_iso()
        if not chat.get("title") or chat.get("title")=="New maintenance chat":chat["title"]=_chat_title(message)
        _save_user_chats(chats)
        try:learning_store.capture_chat(_ai_user_key(),chat)
        except Exception:logger.exception("Learning capture failed; chat answer remains saved")
        return jsonify({"message_id":answer_id,"answer":answer,"chat_id":chat["id"],"title":chat["title"],"elapsed_ms":round((time.monotonic()-started)*1000),"retrieval":ctx["retrieval"],"images_analyzed":len(image_urls),"sources":research["results"],"research_status":research["status"],"similar_cases":similar_cases,"parts":part_matches})
    except Exception as e:
        logger.exception("Kurtex AI chat failed at stage=%s: %s",stage,e)
        _notify_user("ai","Kurtex AI unavailable","AI chat failed at "+stage+". Try again or check AI Training diagnostics.","warning")
        payload={"error":"Kurtex AI is temporarily unavailable.","stage":stage}
        if _ai_is_trainer(): payload["detail"]=str(e)[:500]
        return jsonify(payload),503

def _maintenance_tokens(value):
    text=str(value or "").lower()
    replacements={"turbina":"turbo turbocharger","турбина":"turbo turbocharger","турбо":"turbo","presiune":"pressure boost",
    "давление":"pressure boost","pierde aer":"air leak","scapa aer":"air leak","scapă aer":"air leak","утечка воздуха":"air leak",
    "frana":"brake","frână":"brake","тормоз":"brake","тормоза":"brake","motor":"engine","двигатель":"engine",
    "ulei":"oil","масло":"oil","temperatura":"temperature","температура":"temperature","frig":"cooling reefer",
    "remorca":"trailer","remorcă":"trailer","прицеп":"trailer","camion":"truck","грузовик":"truck",
    "baterie":"battery","аккумулятор":"battery"}
    for a,b in replacements.items(): text=text.replace(a,b)
    return set(re.findall(r"[a-z0-9ăâîșşțţа-яё_-]{3,}",text,re.I))

def _part_case_candidates(part,limit=90):
    all_cases=[c for c in load_cases() if not is_testing(c)]
    part_text=" ".join([str(part.get("name") or ""),str(part.get("cat") or ""),str(part.get("keywords") or ""),
      " ".join(str(x) for x in (part.get("issues") or [])),str(part.get("works") or "")])
    wanted=_maintenance_tokens(part_text)-{"truck","trailer","engine","issue","problem","check","repair","service","maintenance","unit","system","part","power"}
    def score(c):
        txt=" ".join(str(c.get(k) or "") for k in ("issue_text","description","notes","vehicle_type","unit_number","report_driver"))
        got=_maintenance_tokens(txt); overlap=wanted & got
        return len(overlap)*4+(12 if str(part.get("name") or "").lower() in txt.lower() else 0)+(1 if c.get("issue_text") else 0)+(1 if c.get("notes") else 0)
    ranked=sorted(all_cases,key=lambda c:(score(c),c.get("opened_at") or ""),reverse=True)
    strong=ranked_cases(part_text,all_cases,limit); recent=sorted(all_cases,key=lambda c:c.get("opened_at") or "",reverse=True)[:20]
    out=[]; seen=set()
    for c in strong[:limit]:
        cid=str(c.get("id") or c.get("full_id") or id(c))
        if cid not in seen:seen.add(cid);out.append(c)
        if len(out)>=limit:break
    return out

@app.route("/api/ai/related_cases",methods=["POST"])
def api_ai_related_cases():
    if not session.get("user"):return jsonify({"error":"unauthorized"}),401
    data=request.get_json(silent=True) or {}; part=data.get("part") if isinstance(data.get("part"),dict) else {}
    name=str(part.get("name") or "").strip()[:150]
    if not name:return jsonify({"error":"Part is required"}),400
    try:
        candidates=_part_case_candidates(part,30)
        pc={"name":name,"category":str(part.get("cat") or "")[:80],"keywords":str(part.get("keywords") or "")[:600],
            "common_issues":part.get("issues") if isinstance(part.get("issues"),list) else [],
            "how_it_works":str(part.get("works") or "")[:900]}
        prompt="""Rerank real Kurtex fleet history for this maintenance component. Understand Romanian, English, Russian,
mixed mechanic slang, misspellings, symptoms and indirect failure descriptions.
A useful match may directly name the component, describe a strongly associated failure mode, or describe a connected
subsystem issue that is diagnostically useful. Do NOT match generic maintenance because words such as oil, engine,
truck or service overlap. Routine oil change is NOT a Turbocharger match; low boost, charge-air leak, actuator,
turbo oil leak, abnormal whistle or underboost can be relevant without the word turbocharger.
Return ONLY JSON: {"matches":[{"id":"exact case id","score":0-100,"reason":"short concrete reason"}]}
Use exact IDs only. Use a relevance rubric, not probability: 90-100 requires same component and same failure mode, compatible equipment and no contradictory codes; 60-89 is only partial relevance. Return only scores of 90 or higher; an empty list is correct when no strong match exists. Never pad the list.
PART:
%s
CANDIDATES:
%s"""%(json.dumps(pc,ensure_ascii=False),json.dumps([_ai_case_record(c) for c in candidates],ensure_ascii=False))
        raw=_cf_ai([{"role":"system","content":KURTEX_AI_SYSTEM},{"role":"user","content":prompt}],1300,0)
        cleaned=re.sub(r"^```(?:json)?\s*|\s*```$","",raw.strip(),flags=re.I|re.S); parsed=json.loads(cleaned)
        by_id={str(c.get("id") or ""):c for c in candidates}; out=[]
        for m in parsed.get("matches",[]) if isinstance(parsed,dict) else []:
            cid=str(m.get("id") or "")
            try: score=int(float(m.get("score") or 0))
            except Exception: score=0
            if cid in by_id and score>=90:
                out.append({"case":serialize_case(by_id[cid]),"score":min(score,100),"score_type":"AI relevance estimate, not diagnostic confidence","reason":str(m.get("reason") or "")[:180]})
        out.sort(key=lambda x:-x["score"])
        return jsonify({"items":out[:8],"ai":True,"searched_cases":len([c for c in load_cases() if not is_testing(c)]),"candidates":len(candidates)})
    except Exception as e:
        logger.warning("AI related cases failed: %s",e)
        _notify_user("ai","AI case matching unavailable","Parts Manual AI matching failed. No unverified cases were shown.","warning")
        return jsonify({"error":"AI matching unavailable","items":[]}),503

@app.route("/api/ai/test",methods=["POST"])
def api_ai_test():
    if not session.get("user"):return jsonify({"error":"unauthorized"}),401
    if not _ai_is_trainer():return jsonify({"error":"forbidden"}),403
    try:
        answer=_cf_ai([{"role":"system","content":"Reply exactly: Kurtex AI connected"},{"role":"user","content":"connection test"}],40,0)
        return jsonify({"ok":True,"response":answer,"model":KURTEX_AI_MODEL})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e),"model":KURTEX_AI_MODEL,
          "account_id_set":bool(CLOUDFLARE_ACCOUNT_ID),"token_set":bool(CLOUDFLARE_API_TOKEN)}),503

# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route("/auth/telegram")
def telegram_auth():
    data = dict(request.args)
    if not data.get("hash"): return redirect("/login?error=missing")
    if verify_telegram_login(data):
        user_id = int(data.get("id", 0))
        from backend.storage.user_store import get_user
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


# ── Persistent UI preferences ────────────────────────────────────────────────

@app.route("/api/preferences/overview", methods=["GET", "PUT"])
def api_overview_preferences():
    if not session.get("user"):
        return jsonify({"error": "unauthorized"}), 401
    from backend.storage.preference_store import get_overview, save_overview
    user_id = session["user"].get("id")
    if request.method == "GET":
        return jsonify({"overview": get_overview(user_id)})
    payload = request.get_json(silent=True) or {}
    overview = payload.get("overview")
    if not isinstance(overview, dict):
        return jsonify({"error": "Invalid overview preferences."}), 400
    allowed_widgets = {"metrics", "agents", "units", "ai_summary", "activity", "cases"}
    clean = {}
    for device in ("desktop", "mobile"):
        src = overview.get(device, {})
        if not isinstance(src, dict):
            src = {}
        order = [x for x in src.get("order", []) if x in allowed_widgets]
        order += [x for x in ("metrics", "agents", "units", "ai_summary", "activity", "cases") if x not in order]
        hidden = [x for x in src.get("hidden", []) if x in allowed_widgets]
        metrics = [x for x in src.get("metrics", []) if isinstance(x, int) and 0 <= x <= 20]
        clean[device] = {"order": order, "hidden": hidden, "metrics": metrics}
    save_overview(user_id, clean)
    return jsonify({"ok": True, "overview": clean})


# ── Website role permissions ─────────────────────────────────────────────────
ROLE_PERMISSION_DEFAULTS = {
    "agent": ["overview","cases","missed","kurtex_intelligence","fleet","fleet_intel","parts_manual","ai_assistant","my_profile"],
    "super_admin": ["overview","cases","missed","leaderboard","kurtex_intelligence","trends","comparison","fleet","fleet_intel","parts_manual","ai_assistant","agents","my_profile"],
    "developer": ["overview","cases","missed","leaderboard","kurtex_intelligence","trends","comparison","fleet","fleet_intel","parts_manual","ai_assistant","ai_knowledge","agents","users","developer","my_profile"],
}
ROLE_PERMISSION_PAGES = ["overview","cases","missed","leaderboard","kurtex_intelligence","trends","comparison","fleet","fleet_intel","parts_manual","ai_assistant","ai_knowledge","agents"]

def _permissions_path():
    return Path(os.getenv("DATA_DIR", "/app/data")) / "role_permissions.json"

def _load_role_permissions():
    data = {k:list(v) for k,v in ROLE_PERMISSION_DEFAULTS.items()}
    try:
        path=_permissions_path()
        if path.exists():
            raw=json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw,dict):
                for role in ("agent","super_admin"):
                    vals=raw.get(role)
                    if isinstance(vals,list): data[role]=[x for x in vals if x in ROLE_PERMISSION_PAGES or x=="my_profile"]
    except Exception as e:
        logger.warning("Role permissions read failed: %s", e)
    data["developer"]=list(ROLE_PERMISSION_DEFAULTS["developer"])
    for role in data:
        if "overview" not in data[role]: data[role].insert(0,"overview")
        if "my_profile" not in data[role]: data[role].append("my_profile")
    return data

def _save_role_permissions(data):
    path=_permissions_path(); path.parent.mkdir(parents=True,exist_ok=True)
    clean={}
    for role in ("agent","super_admin"):
        vals=data.get(role,[]); clean[role]=[x for x in ROLE_PERMISSION_PAGES if x in vals]
        if "overview" not in clean[role]: clean[role].insert(0,"overview")
    tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(clean,indent=2),encoding='utf-8'); tmp.replace(path)
    return clean

def _website_role(role):
    return "super_admin" if role in ("manager","admin","super_admin") else ("developer" if role=="developer" else "agent")

def _current_allowed_pages():
    user=session.get("user") or {}; return _load_role_permissions().get(_website_role(user.get("role","agent")), ROLE_PERMISSION_DEFAULTS["agent"])

@app.route("/api/developer/permissions", methods=["GET","PUT"])
def api_developer_permissions():
    if not session.get("user"): return jsonify({"error":"unauthorized"}),401
    if not _developer_only(): return jsonify({"error":"forbidden"}),403
    if request.method=="GET": return jsonify({"permissions":_load_role_permissions(),"pages":ROLE_PERMISSION_PAGES})
    payload=request.get_json(silent=True) or {}; incoming=payload.get("permissions") or {}
    if not isinstance(incoming,dict): return jsonify({"error":"Invalid permissions."}),400
    saved=_save_role_permissions(incoming)
    return jsonify({"ok":True,"permissions":_load_role_permissions()})

# ── Developer workspace ─────────────────────────────────────────────────────
def _developer_only():
    user = session.get("user") or {}
    return user.get("role") == "developer"

@app.route("/api/developer/status")
def api_developer_status():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    if not _developer_only(): return jsonify({"error":"forbidden"}), 403
    from backend.storage.user_store import get_all_users
    from backend.core.app_time import chicago_date_str, today_str
    data_dir = Path(os.getenv("DATA_DIR", "/app/data"))
    try:
        data_writable = data_dir.exists() and os.access(str(data_dir), os.W_OK)
    except Exception:
        data_writable = False
    try:
        cases = load_cases()
    except Exception:
        cases = []
    today = today_str()
    received_today = sum(1 for c in cases if chicago_date_str(c.get("opened_at")) == today)
    resolved_today = sum(1 for c in cases if c.get("status") == "done" and chicago_date_str(c.get("closed_at")) == today)
    assigned_now = sum(1 for c in cases if c.get("status") == "assigned")
    reported_now = sum(1 for c in cases if c.get("status") == "reported")

    heartbeat = {}
    heartbeat_path = data_dir / "bot_runtime.json"
    try:
        heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8")) if heartbeat_path.exists() else {}
    except Exception:
        heartbeat = {}
    heartbeat_age = None
    bot_alive = False
    try:
        hb = datetime.fromisoformat(str(heartbeat.get("heartbeat_at") or "").replace("Z", "+00:00"))
        if hb.tzinfo is None: hb = hb.replace(tzinfo=timezone.utc)
        heartbeat_age = max(0, int((datetime.now(timezone.utc) - hb).total_seconds()))
        bot_alive = heartbeat_age <= 75 and bool(heartbeat.get("polling_running"))
    except Exception:
        pass
    uptime_seconds = None
    try:
        started = datetime.fromisoformat(str(heartbeat.get("started_at") or "").replace("Z", "+00:00"))
        if started.tzinfo is None: started = started.replace(tzinfo=timezone.utc)
        uptime_seconds = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    except Exception:
        pass

    return jsonify({
        "telegram_configured": bool(os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")),
        "ai_configured": bool(CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN),
        "data_writable": data_writable,
        "data_dir": str(data_dir),
        "case_count": len(cases),
        "user_count": len(get_all_users()),
        "ai_model": KURTEX_AI_MODEL if (CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN) else "",
        "environment": "Railway" if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID") else "Local",
        "bot_runtime": {
            "alive": bot_alive,
            "heartbeat_age_seconds": heartbeat_age,
            "uptime_seconds": uptime_seconds,
            "started_at": heartbeat.get("started_at"),
            "last_update_at": heartbeat.get("last_update_at"),
            "updates_processed": heartbeat.get("updates_processed", 0),
            "callbacks_processed": heartbeat.get("callbacks_processed", 0),
            "polling_running": bool(heartbeat.get("polling_running")),
            "username": heartbeat.get("username", ""),
            "railway_service": heartbeat.get("railway_service", ""),
            "railway_environment": heartbeat.get("railway_environment", ""),
            "railway_deployment_id": heartbeat.get("railway_deployment_id", ""),
            "received_today": received_today,
            "resolved_today": resolved_today,
            "assigned_now": assigned_now,
            "reported_now": reported_now,
        },
    })

@app.route("/api/developer/test/telegram", methods=["POST"])
def api_developer_test_telegram():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    if not _developer_only(): return jsonify({"error":"forbidden"}), 403
    token = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token: return jsonify({"ok":False,"error":"Telegram bot token is not configured"}), 503
    try:
        import urllib.request
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not payload.get("ok"): raise RuntimeError("Telegram API returned an unsuccessful response")
        result = payload.get("result") or {}
        return jsonify({"ok":True,"username":result.get("username",""),"id":result.get("id")})
    except Exception as e:
        logger.warning("Developer Telegram connection test failed: %s", e)
        return jsonify({"ok":False,"error":"Telegram connection failed. Check Railway logs."}), 503

@app.route("/api/developer/users", methods=["GET", "POST"])
def api_developer_users():
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    if not _developer_only(): return jsonify({"error":"forbidden"}), 403
    from backend.storage.user_store import get_all_user_dicts, add_user, VALID_ROLES
    if request.method == "GET":
        users = sorted(get_all_user_dicts(), key=lambda u: ((u.get("name") or "").lower(), str(u.get("id"))))
        return jsonify({"users": users})
    payload = request.get_json(silent=True) or {}
    try: user_id = int(str(payload.get("id", "")).strip())
    except Exception: return jsonify({"error":"Enter a valid Telegram user ID."}), 400
    name = str(payload.get("name") or "").strip()
    username = str(payload.get("username") or "").strip().lstrip("@")
    role = str(payload.get("role") or "agent").strip()
    if not name: return jsonify({"error":"Name is required."}), 400
    if role not in VALID_ROLES: return jsonify({"error":"Invalid role."}), 400
    if not add_user(user_id, name, username, role): return jsonify({"error":"Unable to save user."}), 400
    return jsonify({"ok":True})

@app.route("/api/developer/users/<int:user_id>", methods=["PATCH", "DELETE"])
def api_developer_user(user_id):
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    if not _developer_only(): return jsonify({"error":"forbidden"}), 403
    from backend.storage.user_store import edit_user, remove_user, VALID_ROLES
    current_id = int(session["user"].get("id") or 0)
    if request.method == "DELETE":
        if user_id == current_id: return jsonify({"error":"You cannot remove your own developer access while signed in."}), 400
        if not remove_user(user_id): return jsonify({"error":"User not found."}), 404
        return jsonify({"ok":True})
    payload = request.get_json(silent=True) or {}
    role = str(payload.get("role") or "").strip()
    name = str(payload.get("name") or "").strip()
    username = str(payload.get("username") or "").strip().lstrip("@")
    if role not in VALID_ROLES: return jsonify({"error":"Invalid role."}), 400
    if not name: return jsonify({"error":"Name is required."}), 400
    if user_id == current_id and role != "developer":
        return jsonify({"error":"You cannot remove your own developer role while signed in."}), 400
    if not edit_user(user_id, name, username, role): return jsonify({"error":"User not found."}), 404
    return jsonify({"ok":True})

# ── API ───────────────────────────────────────────────────────────────────────


_PART_IMAGE_CACHE = {}

_PART_IMAGE_CONTEXT = {
    "air": "heavy duty semi truck air brake pneumatic component",
    "brakes": "heavy duty semi truck air brake component",
    "suspension": "American semi truck trailer suspension component",
    "electrical": "heavy duty diesel semi truck electrical component",
    "engine": "heavy duty diesel semi truck engine component",
    "aftertreatment": "heavy duty diesel truck DPF DEF aftertreatment component",
    "trailer": "American semi truck trailer component",
    "reefer": "Thermo King Carrier refrigerated semi trailer reefer component",
}
_PART_IMAGE_BAD_WORDS = {
    "shell", "seashell", "snail", "mollusc", "mollusk", "gastropod", "animal", "plant",
    "flower", "bird", "fish", "food", "toy", "artwork", "painting", "sculpture", "logo", "map",
}
_PART_IMAGE_GOOD_DOMAINS = (
    "bendix", "cummins", "detroit", "freightliner", "paccar", "kenworth", "peterbilt", "volvo",
    "mack", "thermoking", "carrier", "fleetpride", "finditparts", "hendrickson", "safholland",
    "meritor", "wabco", "haldex", "dorman", "dayco", "gates", "denso", "delco", "bosch",
)

def _part_tokens(value: str):
    words = re.findall(r"[a-z0-9]+", (value or "").lower())
    stop = {"and", "the", "system", "assembly", "unit", "truck", "semi", "heavy", "duty", "part", "component"}
    return {w for w in words if len(w) >= 3 and w not in stop}

def _serper_part_images(part_name: str, category: str = "", keywords: str = "", limit: int = 4):
    """Find real heavy-duty component photos through Serper's Google Images endpoint.

    Results stay remote: Kurtex stores only short-lived search metadata, never the image bytes.
    The API key is read server-side from SERPER_API_KEY and is never exposed to the browser.
    """
    api_key = os.getenv("SERPER_API_KEY", "").strip()
    if not api_key:
        return [], "serper_not_configured"

    clean_name = re.sub(r"[^a-zA-Z0-9 /+&()._-]+", " ", (part_name or "")).strip()[:100]
    clean_keywords = re.sub(r"[^a-zA-Z0-9 /+&()._-]+", " ", (keywords or "")).strip()[:180]
    category = re.sub(r"[^a-zA-Z]+", "", (category or "").lower())[:30]
    if not clean_name:
        return [], "missing_query"

    cache_key = ("serper|" + clean_name + "|" + category + "|" + clean_keywords).lower()
    cached = _PART_IMAGE_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < 30 * 86400:
        return cached[1], "serper_cache"

    context = _PART_IMAGE_CONTEXT.get(category, "American heavy duty diesel semi truck component")
    # Component name remains first so Google Images understands what object must be visible.
    queries = [
        f'{clean_name} {context}',
        f'{clean_name} heavy duty truck part',
    ]
    required = _part_tokens(clean_name)
    candidates = {}

    for search_query in queries:
        body = json.dumps({"q": search_query, "gl": "us", "hl": "en", "num": 20}).encode("utf-8")
        req = urllib.request.Request(
            "https://google.serper.dev/images",
            data=body,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json", "User-Agent": "KurtexDashboard/1.2"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.info("Serper part image lookup failed for %r: %s", search_query, exc)
            continue

        for item in data.get("images") or []:
            title = str(item.get("title") or "").strip()
            source = str(item.get("source") or "").strip()
            source_url = str(item.get("link") or "").strip()
            image_url = str(item.get("imageUrl") or item.get("thumbnailUrl") or "").strip()
            thumb_url = str(item.get("thumbnailUrl") or image_url).strip()
            if not image_url or not source_url or not source_url.startswith(("http://", "https://")):
                continue
            hay = (title + " " + source + " " + source_url).lower()
            if any(bad in hay for bad in _PART_IMAGE_BAD_WORDS):
                continue
            tokens = _part_tokens(hay)
            component_hits = len(required & tokens)
            # Multi-word names can tolerate one omitted word; one-word parts must match exactly.
            minimum_hits = 1 if len(required) <= 2 else 2
            if required and component_hits < minimum_hits:
                continue
            automotive = any(x in hay for x in ("truck", "diesel", "semi", "tractor", "trailer", "automotive", "engine", "brake", "suspension", "reefer"))
            trusted = any(domain in hay for domain in _PART_IMAGE_GOOD_DOMAINS)
            score = component_hits * 12 + (8 if automotive else 0) + (10 if trusted else 0)
            if clean_name.lower() in title.lower():
                score += 12
            # Serper already searched with heavy-duty context; accept a strong exact-name result even
            # when the merchant title itself omits words such as "truck".
            if not automotive and not trusted and component_hits < max(1, len(required)):
                continue
            key = image_url
            candidate = {
                "image_url": image_url,
                "thumbnail_url": thumb_url,
                "source_url": source_url,
                "title": title or clean_name,
                "source": source,
                "provider": "Serper / Google Images",
                "score": score,
            }
            if key not in candidates or score > candidates[key]["score"]:
                candidates[key] = candidate

        if len(candidates) >= limit:
            break

    results = sorted(candidates.values(), key=lambda x: (-x["score"], x["title"].lower()))[:max(1, min(limit, 4))]
    for item in results:
        item.pop("score", None)
    _PART_IMAGE_CACHE[cache_key] = (time.time(), results)
    return results, "serper"

@app.route("/api/part_search")
def api_part_search():
    """Live Parts Manual search for names, OEM numbers and manufacturer part numbers."""
    if not session.get("user"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"ok": False, "error": "Enter a part name or part number"}), 400
    q = q[:120]
    part_number_like = bool(len(q) >= 5 and re.search(r"[A-Za-z]", q) and re.search(r"\d", q) and re.fullmatch(r"[A-Za-z0-9._\-/]+", q))
    keywords = "OEM part number exact match heavy duty truck" if part_number_like else "heavy duty truck component OEM replacement"
    results, provider = _serper_part_images(q, "", keywords, 4)
    return jsonify({
        "ok": True,
        "query": q,
        "part_number_like": part_number_like,
        "results": results,
        "provider": provider,
        "configured": bool(os.getenv("SERPER_API_KEY", "").strip()),
    })

@app.route("/api/part_image")
def api_part_image():
    if not session.get("user"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    q = (request.args.get("q") or "").strip()
    cat = (request.args.get("cat") or "").strip()
    keywords = (request.args.get("keywords") or "").strip()
    if not q:
        return jsonify({"ok": False, "error": "Missing part query"}), 400
    results, provider = _serper_part_images(q, cat, keywords, 4)
    return jsonify({
        "ok": bool(results), "results": results, "result": results[0] if results else None,
        "provider": provider,
        "configured": bool(os.getenv("SERPER_API_KEY", "").strip()),
    })

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

        def agent_performance(lst):
            agents = {}
            for c in lst:
                name = (c.get("agent_name") or "").strip()
                if not name:
                    continue
                a = agents.setdefault(name, {"name": name, "handled": 0, "resolved": 0, "active": 0, "missed": 0, "reassigned": 0})
                a["handled"] += 1
                status = c.get("status") or "open"
                if status == "done": a["resolved"] += 1
                elif status in ("assigned", "reported", "open"): a["active"] += 1
                if status == "missed": a["missed"] += 1
                if c.get("reassigned"): a["reassigned"] += 1
            return sorted(agents.values(), key=lambda a: (-a["resolved"], -a["handled"], a["missed"], a["name"].lower()))

        def performance_summary(lst):
            perf = agent_performance(lst)
            return {
                "total": len(lst),
                "resolved": sum(1 for c in lst if c.get("status") == "done"),
                "active": sum(1 for c in lst if c.get("status") in ("open", "assigned", "reported")),
                "missed": sum(1 for c in lst if c.get("status") == "missed"),
                "reassigned": sum(1 for c in lst if c.get("reassigned")),
                "agents": perf,
            }

        def performance_trend(days):
            # Today needs more than a single point, otherwise a line chart renders as dots.
            # Use 2-hour Chicago-time buckets for Today; longer ranges remain daily.
            if days == 1:
                now = chicago_now()
                today_date = now.date()
                buckets = []
                for hour in range(0, 24, 2):
                    bucket_start = hour
                    bucket_end = hour + 2
                    opened_count = 0
                    resolved_count = 0
                    for c in real:
                        opened = chicago_timestamp(c.get("opened_at")) if c.get("opened_at") else None
                        if opened and opened.date() == today_date and bucket_start <= opened.hour < bucket_end:
                            opened_count += 1
                        closed = chicago_timestamp(c.get("closed_at")) if c.get("closed_at") else None
                        if closed and closed.date() == today_date and bucket_start <= closed.hour < bucket_end:
                            resolved_count += 1
                    label_hour = hour % 12 or 12
                    suffix = "AM" if hour < 12 else "PM"
                    buckets.append({"date": f"{label_hour} {suffix}", "total": opened_count, "resolved": resolved_count})
                return buckets
            end = chicago_now().date()
            start = end - timedelta(days=days - 1)
            buckets = []
            for i in range(days):
                d = start + timedelta(days=i)
                ds = d.isoformat()
                day_cases = [c for c in real if case_local_date(c) == ds]
                buckets.append({
                    "date": d.strftime("%b %d"),
                    "total": len(day_cases),
                    "resolved": sum(1 for c in day_cases if c.get("status") == "done"),
                    "missed": sum(1 for c in day_cases if c.get("status") == "missed"),
                })
            return buckets
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
            "leaderboard_day": lb(tc), "leaderboard_week": lb(wc), "leaderboard_month": lb(mc), "leaderboard_all": lb(real),
            "performance_day": performance_summary(tc),
            "performance_week": performance_summary([c for c in real if case_local_date(c) >= (chicago_now().date()-timedelta(days=6)).isoformat()]),
            "performance_month": performance_summary([c for c in real if case_local_date(c) >= (chicago_now().date()-timedelta(days=29)).isoformat()]),
            "performance_trend_day": performance_trend(1),
            "performance_trend_week": performance_trend(7),
            "performance_trend_month": performance_trend(30),
            "top_groups": group_stats[:6],
            "top_problem_units": top_problem_units,
            "top_problem_units_day": top_units_for(tc, 6),
            "top_problem_units_week": top_units_for(wc, 6),
            "top_problem_units_month": top_units_for(mc, 6),
            "top_problem_units_all": top_problem_units,
            "top_words": [{"word":w,"count":v} for w,v in Counter(hashtags).most_common(15)],
            "reassigned_count": sum(1 for c in cases if c.get("reassigned")),
        })
    except DataUnavailable:
        raise
    except Exception as e:
        logger.error(f"api_stats error: {e}")
        return jsonify({"error": "Unable to load data. Please retry."}), 500




def _ki_category(c):
    text=" ".join(str(c.get(k) or "") for k in ("issue_text","description","notes","report_text","comments")).lower()
    groups=[
        ("Tires & Wheel End",("tire","tyre","wheel","hub","bearing","seal")),
        ("Air & Suspension",("air bag","suspension","air leak","shock")),
        ("Brakes",("brake","abs")),
        ("Electrical & Battery",("electrical","battery","voltage","alternator","wiring")),
        ("Cooling System",("coolant","radiator","overheat","cooling")),
        ("Reefer System",("reefer","thermo king","carrier unit","temperature","setpoint")),
        ("Engine / Emissions",("engine","oil pressure","misfire","def","dpf")),
        ("Lighting",("light","lamp","headlight","marker")),
        ("Fuel System",("fuel","diesel","injector"))]
    return next((name for name,words in groups if any(w in text for w in words)),"Other")

def _ki_date(c):
    try:
        value=case_local_date(c)
        return datetime.fromisoformat(value).date() if value else None
    except Exception:
        return None

def _ki_case_id(c):
    return str(c.get("full_id") or c.get("id") or "")

def build_kurtex_intelligence(cases):
    """Deterministic evidence engine. AI may explain findings elsewhere, never invent them."""
    cases=[c for c in cases if not is_testing(c)]
    today=chicago_now().date(); resolved={"done","resolved","closed"}
    recent_start=today-timedelta(days=13); prev_start=today-timedelta(days=27); prev_end=recent_start-timedelta(days=1)
    recent=[c for c in cases if _ki_date(c) and recent_start<=_ki_date(c)<=today]
    previous=[c for c in cases if _ki_date(c) and prev_start<=_ki_date(c)<=prev_end]
    findings=[]; case_tags={}
    def tag(case_ids, label, kind, finding_id):
        for cid in case_ids:
            if cid: case_tags.setdefault(cid,[]).append({"label":label,"kind":kind,"finding_id":finding_id})
    rc=Counter(_ki_category(c) for c in recent); pc=Counter(_ki_category(c) for c in previous)
    for cat,n in rc.items():
        old=pc.get(cat,0)
        if cat=="Other" or n<3 or n<old+2 or (old and n<old*2): continue
        related=[c for c in recent if _ki_category(c)==cat]; ids=[_ki_case_id(c) for c in related]
        units=sorted({str(c.get("unit_number") or "").strip() for c in related if str(c.get("unit_number") or "").strip()})
        pct=None if old==0 else round((n-old)/old*100)
        fid="emerging:"+cat.lower().replace(" ","-").replace("&","and").replace("/","-")
        severity="critical" if n>=8 and (old==0 or n>=old*3) else "elevated"
        evidence=f"{n} cases · {len(units)} units · " + ("new pattern vs previous 14 days" if old==0 else f"+{pct}% vs previous 14 days")
        findings.append({"id":fid,"type":"emerging_problem","title":f"{cat} reports are increasing","category":cat,"severity":severity,"evidence":evidence,"summary":f"Kurtex found {n} {cat.lower()} cases in the last 14 days compared with {old} in the previous 14 days.","case_ids":ids,"units":units,"count":n,"previous_count":old})
        tag(ids,"Emerging","emerging",fid)
    by_unit={}
    for c in cases:
        u=str(c.get("unit_number") or "").strip(); d=_ki_date(c)
        if u and d: by_unit.setdefault(u,[]).append((d,c))
    for unit,rows in by_unit.items():
        rows.sort(key=lambda x:x[0]); unit_recent=[c for d,c in rows if d>=today-timedelta(days=29)]
        if len(unit_recent)>=4:
            ids=[_ki_case_id(c) for c in unit_recent]; cats=Counter(_ki_category(c) for c in unit_recent if _ki_category(c)!="Other")
            fid="chronic:"+unit
            findings.append({"id":fid,"type":"chronic_unit","title":f"Unit {unit} has frequent maintenance reports","category":cats.most_common(1)[0][0] if cats else "Multiple systems","severity":"elevated" if len(unit_recent)<6 else "critical","evidence":f"{len(unit_recent)} cases in the last 30 days","summary":f"Unit {unit} has returned {len(unit_recent)} times in the last 30 days. Review its case history for related failures.","case_ids":ids,"units":[unit],"count":len(unit_recent)})
            tag(ids,"Chronic unit","chronic",fid)
        for i,(d,c) in enumerate(rows):
            if str(c.get("status") or "").lower() in resolved: continue
            fam=_ki_category(c)
            if fam=="Other" or d<today-timedelta(days=30): continue
            prior=[(pd,p) for pd,p in rows[:i] if str(p.get("status") or "").lower() in resolved and _ki_category(p)==fam and 0<=(d-pd).days<=30]
            if not prior: continue
            pd,prior_case=prior[-1]; days=(d-pd).days; ids=[_ki_case_id(prior_case),_ki_case_id(c)]
            fid="repeat:"+unit+":"+fam.lower().replace(" ","-").replace("/","-")
            findings.append({"id":fid,"type":"repeat_repair","title":f"Unit {unit} returned with a similar issue","category":fam,"severity":"watch" if days>7 else "elevated","evidence":f"Returned {days} day{'s' if days!=1 else ''} after resolution","summary":f"A {fam.lower()} case on unit {unit} appeared {days} day{'s' if days!=1 else ''} after a similar case was resolved.","case_ids":ids,"units":[unit],"count":2})
            tag(ids,"Repeat issue","repeat",fid)
    # De-duplicate repeated unit/category findings, keeping newest evidence.
    unique={}
    for f in findings: unique[f["id"]]=f
    findings=list(unique.values())
    rank={"critical":0,"elevated":1,"watch":2}
    findings.sort(key=lambda f:(rank.get(f["severity"],9),-f.get("count",0),f["title"]))
    return findings,case_tags

KI_STATE_FILE=DATA_DIR/"kurtex_intelligence_state.json"
def _load_ki_state():
    try:
        data=json.loads(KI_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data,dict) else {}
    except Exception:return {}
def _save_ki_state(data):
    tmp=KI_STATE_FILE.with_suffix(".tmp");tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8");tmp.replace(KI_STATE_FILE)

@app.route("/api/kurtex_intelligence")
def api_kurtex_intelligence():
    if not session.get("user"): return jsonify({"error":"unauthorized"}),401
    try:
        findings,_=build_kurtex_intelligence(load_cases()); state=_load_ki_state()
        for f in findings: f["state"]=state.get(f["id"],{}).get("state","new")
        return jsonify({"findings":findings,"generated_at":chicago_now().isoformat(),"source":"live case history"})
    except Exception:
        logger.exception("Kurtex Intelligence failed");return jsonify({"error":"Unable to build intelligence."}),500

@app.route("/api/kurtex_intelligence/state",methods=["POST"])
def api_kurtex_intelligence_state():
    if not session.get("user"): return jsonify({"error":"unauthorized"}),401
    body=request.get_json(silent=True) or {}; fid=str(body.get("id") or "").strip(); value=str(body.get("state") or "").strip().lower()
    if not fid or value not in {"new","watching","acknowledged","investigating","resolved","dismissed"}: return jsonify({"error":"Invalid intelligence state."}),400
    state=_load_ki_state(); state[fid]={"state":value,"updated_at":chicago_now().isoformat(),"updated_by":(session.get("user") or {}).get("first_name","")}; _save_ki_state(state)
    return jsonify({"ok":True,"state":value})

@app.route("/api/home/ai-summary")
def api_home_ai_summary():
    """Surface actionable fleet intelligence; never repeat Home KPI totals."""
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    try:
        cases=[c for c in load_cases() if not is_testing(c)]
        today=chicago_now().date()
        resolved={"done","resolved","closed"}

        def category(c):
            text=" ".join(str(c.get(k) or "") for k in ("issue_text","description","notes","report_text")).lower()
            groups=[
                ("Tires & Wheel End",("tire","tyre","wheel","hub","bearing","seal")),
                ("Air & Suspension",("air bag","suspension","air leak","shock")),
                ("Brakes",("brake","abs")),
                ("Electrical & Battery",("electrical","battery","voltage","alternator","wiring")),
                ("Cooling System",("coolant","radiator","overheat","cooling")),
                ("Reefer System",("reefer","thermo king","carrier unit","temperature","setpoint")),
                ("Engine / Emissions",("engine","oil pressure","misfire","def","dpf")),
                ("Lighting",("light","lamp","headlight","marker")),
                ("Fuel System",("fuel","diesel","injector"))]
            return next((name for name,words in groups if any(w in text for w in words)),"Other")

        def dated(c):
            try:
                d=case_local_date(c)
                return datetime.fromisoformat(d).date() if d else None
            except Exception: return None

        # Emerging problems: recent 14 days versus the immediately preceding 14 days.
        recent_start=today-timedelta(days=13); previous_start=today-timedelta(days=27); previous_end=recent_start-timedelta(days=1)
        recent=[c for c in cases if dated(c) and recent_start<=dated(c)<=today]
        previous=[c for c in cases if dated(c) and previous_start<=dated(c)<=previous_end]
        rc,pc=Counter(category(c) for c in recent),Counter(category(c) for c in previous)
        emerging=[]
        for name,n in rc.items():
            old=pc.get(name,0)
            # Require real evidence: at least 3 recent cases and either +2 cases or >=2x prior volume.
            if name!="Other" and n>=3 and n>=old+2 and (old==0 or n>=old*2):
                units=len({str(c.get("unit_number") or "").strip() for c in recent if category(c)==name and str(c.get("unit_number") or "").strip()})
                emerging.append((n-old,n,name,old,units))
        emerging.sort(reverse=True)

        # Repair follow-up: same unit + problem family returned after a resolved case within 30 days.
        by_unit={}
        for c in cases:
            unit=str(c.get("unit_number") or "").strip()
            d=dated(c)
            if unit and d: by_unit.setdefault(unit,[]).append((d,c))
        followups=[]
        for unit,rows in by_unit.items():
            rows.sort(key=lambda x:x[0])
            for i,(d,c) in enumerate(rows):
                if str(c.get("status") or "").lower() in resolved: continue
                fam=category(c)
                if fam=="Other": continue
                prior=[(pd,p) for pd,p in rows[:i] if str(p.get("status") or "").lower() in resolved and category(p)==fam and 0 <= (d-pd).days <= 30]
                if prior:
                    pd,p=prior[-1]; followups.append(((d-pd).days,d,unit,fam,c,p))
        followups.sort(key=lambda x:x[1],reverse=True)

        insights=[]
        if emerging:
            _,n,name,old,units=emerging[0]
            unit_text=f" across {units} units" if units else ""
            facts={"kind":"emerging_problem","category":name,"recent_14_days":n,"previous_14_days":old,"units":units}
            text=f"{name} reports increased to {n} in the last 14 days{unit_text}, compared with {old} in the previous 14 days."
            insights.append({"type":"emerging_problem","title":"Emerging problem","text":text,"meta":"Last 14 days vs previous 14 days","facts":facts})
        if followups:
            days,_,unit,fam,current,prior=followups[0]
            text=f"Unit {unit} has a new {fam.lower()} case {days} day{'s' if days!=1 else ''} after a similar case was resolved."
            insights.append({"type":"repair_followup","title":"Repair follow-up","text":text,"meta":"Possible repeat after a recent resolution","case_id":current.get("id") or current.get("full_id")})

        # AI may improve wording only; detection and numbers above remain deterministic.
        if insights:
            try:
                facts=[{"type":x["type"],"title":x["title"],"text":x["text"],"meta":x["meta"]} for x in insights]
                prompt=("Rewrite each maintenance insight for a fleet manager. Keep every number, unit, category and time period exactly as provided. "
                        "Do not add diagnoses, causes, recommendations, trends or facts. Return JSON only as an array of strings in the same order. Facts: "+json.dumps(facts,ensure_ascii=False))
                raw=_cf_ai([{"role":"system","content":"You edit verified Kurtex fleet insights. Never invent information."},{"role":"user","content":prompt}],max_tokens=220,temperature=0.05).strip()
                if raw.startswith("```"): raw=raw.strip("`").replace("json\n","",1).strip()
                rewritten=json.loads(raw)
                if isinstance(rewritten,list) and len(rewritten)==len(insights) and all(isinstance(x,str) and x.strip() for x in rewritten):
                    for item,text in zip(insights,rewritten): item["text"]=text.strip()
            except Exception as e:
                logger.info("Kurtex Intelligence wording fallback: %s",e)
        return jsonify({"insights":insights[:2],"source":"live","generated_at":chicago_now().isoformat()})
    except Exception as e:
        logger.exception("home intelligence error: %s",e)
        return jsonify({"error":"Intelligence is temporarily unavailable."}),500


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
        try:
            _,ki_tags=build_kurtex_intelligence([c for c in load_cases() if not is_testing(c)])
        except Exception:
            ki_tags={}
        serialized=[]
        for c in page:
            row=serialize_case(c); cid=_ki_case_id(c); row["ki_tags"]=ki_tags.get(cid,[])
            serialized.append(row)
        return jsonify({
            "cases": serialized,
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
                try:
                    _,ki_tags=build_kurtex_intelligence([x for x in load_cases() if not is_testing(x)])
                    data["ki_tags"]=ki_tags.get(_ki_case_id(c),[])
                except Exception:
                    data["ki_tags"]=[]
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
            from backend.storage.user_store import get_all_user_dicts
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
        import re
        def norm(value):
            return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
        needle = norm(q)
        if not needle: return jsonify({"error":"no query"}), 400
        all_cases = load_cases()
        matches = []
        searchable_fields = ("unit_number","issue_text","description","report_driver","driver","vehicle_type","status","full_id","id","part_number","error_code","resolution","notes")
        for c in all_cases:
            if not c.get("unit_number"): continue
            if vtype and (c.get("vehicle_type") or "").strip().lower() != vtype: continue
            values = [c.get(k, "") for k in searchable_fields]
            # Include simple/list/dict values so case history and imported metadata are searchable too.
            values.extend(v for k,v in c.items() if k not in searchable_fields and isinstance(v,(str,int,float)))
            haystacks = [norm(v) for v in values]
            unit_norm = norm(c.get("unit_number"))
            # Unit searches are forgiving: R168, r168 and 168 all resolve the same unit.
            unit_digits = re.sub(r"[^0-9]+", "", unit_norm)
            needle_digits = re.sub(r"[^0-9]+", "", needle)
            unit_match = needle in unit_norm or (needle_digits and needle_digits == unit_digits)
            if unit_match or any(needle in h for h in haystacks):
                matches.append(c)
        from collections import defaultdict
        by_unit = defaultdict(lambda: {"cases": [], "vtype": ""})
        for c in matches:
            unit = (c.get("unit_number") or "").strip()
            ctype = (c.get("vehicle_type") or "").lower()
            by_unit[(unit, ctype)]["vtype"] = c.get("vehicle_type","")
            by_unit[(unit, ctype)]["cases"].append(c)
        results = []
        for (unit, _), d in by_unit.items():
            cases = sorted(d["cases"], key=lambda c: c.get("opened_at", ""), reverse=True)
            last = cases[0]
            results.append({"unit":unit,"vtype":d["vtype"],"count":len(cases),"last_seen":fmt_dt(last.get("opened_at")),"sample_issue":(last.get("issue_text") or last.get("description") or "")[:100]})
        results.sort(key=lambda x: (-x["count"], x["unit"].lower()))
        return jsonify({"query":q,"results":results,"total_matches":len(matches)})
    except DataUnavailable:
        raise
    except Exception as e:
        logger.error(f"api_issue_search error: {e}")
        return jsonify({"error":"Unable to load data. Please retry."}), 500


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


@app.route("/api/report/pdf")
def api_report_pdf():
    """Generate a real A4 PDF from the same live report data used by the dashboard."""
    if not session.get("user"):
        return jsonify({"error": "unauthorized"}), 401
    try:
        report_response = api_report()
        if isinstance(report_response, tuple):
            return report_response
        data = report_response.get_json() or {}
        selected = {x.strip() for x in request.args.get("sections", "summary,agents,groups,vtype,units,missed").split(",") if x.strip()}

        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, PageBreak

        buf = io.BytesIO()
        accent = colors.HexColor("#20242B")
        red = colors.HexColor("#D92D20")
        green = colors.HexColor("#16803A")
        muted = colors.HexColor("#667085")
        line = colors.HexColor("#E4E7EC")
        soft = colors.HexColor("#F7F8FA")
        styles = getSampleStyleSheet()
        title = ParagraphStyle("KurtexTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=20, leading=23, textColor=accent, alignment=TA_CENTER, spaceAfter=3)
        kicker = ParagraphStyle("KurtexKicker", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=red, alignment=TA_CENTER, spaceAfter=12)
        section = ParagraphStyle("KurtexSection", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=9.5, leading=12, textColor=accent, spaceBefore=10, spaceAfter=6, uppercase=True)
        body = ParagraphStyle("KurtexBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=8.5, leading=11, textColor=accent)
        small = ParagraphStyle("KurtexSmall", parent=body, fontSize=7.5, leading=9, textColor=muted)
        metric_num = ParagraphStyle("MetricNum", parent=body, fontName="Helvetica-Bold", fontSize=17, leading=19, alignment=TA_CENTER, textColor=accent)
        metric_label = ParagraphStyle("MetricLabel", parent=small, fontName="Helvetica-Bold", fontSize=6.8, alignment=TA_CENTER, textColor=muted)

        def footer(canvas, doc):
            canvas.saveState()
            w, _ = A4
            canvas.setStrokeColor(line); canvas.line(18*mm, 13*mm, w-18*mm, 13*mm)
            canvas.setFont("Helvetica", 7); canvas.setFillColor(muted)
            canvas.drawString(18*mm, 8*mm, "Kurtex Maintenance - generated from live case data")
            canvas.drawRightString(w-18*mm, 8*mm, f"Page {doc.page}")
            canvas.restoreState()

        doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=15*mm, bottomMargin=18*mm,
                                title="Kurtex Maintenance Report", author="Kurtex Maintenance")
        story = [Paragraph("KURTEX MAINTENANCE", title), Paragraph("OFFICIAL MAINTENANCE REPORT", kicker)]
        meta = Table([[Paragraph("REPORT PERIOD", metric_label), Paragraph("GENERATED", metric_label)],
                      [Paragraph(str(data.get("label", "")), body), Paragraph(chicago_now().strftime("%B %d, %Y - %I:%M %p CT"), body)]],
                     colWidths=[85*mm, 85*mm])
        meta.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),soft),("BOX",(0,0),(-1,-1),0.5,line),("INNERGRID",(0,0),(-1,-1),0.35,line),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),("LEFTPADDING",(0,0),(-1,-1),8)]))
        story += [meta, Spacer(1, 8*mm)]

        if "summary" in selected:
            story.append(Paragraph("SUMMARY", section))
            metrics = [(data.get("total",0),"TOTAL CASES"),(data.get("done",0),"RESOLVED"),(data.get("open",0),"OPEN CASES"),(f'{data.get("rate",0)}%',"RESOLVED %")]
            cells=[]
            for val,lab in metrics:
                cells.append([Paragraph(str(val),metric_num),Paragraph(lab,metric_label)])
            t=Table([cells], colWidths=[42.5*mm]*4)
            t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),soft),("BOX",(0,0),(-1,-1),0.5,line),("INNERGRID",(0,0),(-1,-1),0.35,line),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7)]))
            story += [t, Spacer(1,4*mm)]

        def data_table(title_text, headers, rows, widths=None):
            if not rows: return
            story.append(Paragraph(title_text.upper(), section))
            vals=[[Paragraph(str(x), metric_label) for x in headers]] + [[Paragraph(str(x), body) for x in row] for row in rows]
            t=Table(vals, colWidths=widths, repeatRows=1, hAlign="LEFT")
            t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),soft),("TEXTCOLOR",(0,0),(-1,0),muted),("LINEBELOW",(0,0),(-1,0),0.7,line),("LINEBELOW",(0,1),(-1,-1),0.35,line),("VALIGN",(0,0),(-1,-1),"TOP"),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5)]))
            story += [t, Spacer(1,3*mm)]

        if "agents" in selected:
            data_table("Agent activity", ["Agent","Cases"], [[x.get("name","-"),x.get("count",0)] for x in data.get("leaderboard",[])], [135*mm,35*mm])
        if "groups" in selected:
            data_table("Most active groups", ["Group","Cases"], [[x.get("name","-"),x.get("count",0)] for x in data.get("top_groups",[])], [135*mm,35*mm])
        if "vtype" in selected:
            rows=[]
            labels={"truck":"Trucks","trailer":"Trailers","reefer":"Reefers"}
            for vt, block in (data.get("by_vtype") or {}).items():
                for x in block.get("top_issues",[]): rows.append([labels.get(vt,vt.title()), x.get("issue","-"), x.get("count",0)])
            data_table("Issues by equipment", ["Equipment","Issue","Cases"], rows, [32*mm,118*mm,20*mm])
        if "units" in selected:
            data_table("Problem units", ["Unit","Type","Reports"], [[x.get("unit","-"),str(x.get("vtype","-")).title(),x.get("count",0)] for x in data.get("top_units",[])], [75*mm,55*mm,40*mm])
        if "missed" in selected:
            rows=[]
            for x in data.get("missed_cases",[]): rows.append([x.get("driver_name") or x.get("driver") or "-", x.get("group_name") or x.get("group") or "-", x.get("opened_at") or x.get("opened") or "-"])
            data_table("Unresolved cases", ["Driver","Group","Opened"], rows, [65*mm,60*mm,45*mm])

        story += [Spacer(1,5*mm), Paragraph("This report contains only the sections selected in Kurtex Report Builder and was generated from live fleet case data.", small)]
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        buf.seek(0)
        filename = "kurtex-maintenance-report-" + chicago_now().strftime("%Y-%m-%d") + ".pdf"
        return Response(buf.getvalue(), mimetype="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except Exception as e:
        logger.exception("report PDF generation failed")
        return jsonify({"error": "Unable to generate PDF report."}), 500


@app.route("/api/export")
def api_export():
    """Full-fidelity case export. Includes report fields visible in the dashboard.

    Nested report/media data is JSON-encoded in a cell instead of silently dropped.
    """
    if not session.get("user"): return jsonify({"error":"unauthorized"}), 401
    cases = load_cases()
    preferred=["id","driver_name","driver_username","group_name","agent_name","agent_username","status",
      "opened_at","assigned_at","closed_at","response_secs","resolution_secs","description","notes",
      "vehicle_type","unit_number","report_driver","issue_text","load_type","location","priority",
      "pickup","delivery","comments","setpoint","current_temp","temp_recorder","resolution","solution",
      "close_notes","closing_notes","resolution_notes","report_text","report_data","media","report_msg_id"]
    discovered=set()
    for c in cases: discovered.update(c.keys())
    fields=[x for x in preferred if x in discovered]
    fields += sorted(x for x in discovered if x not in fields)
    out=io.StringIO();w=csv.writer(out);w.writerow(fields)
    for c in sorted(cases,key=lambda x:x.get("opened_at","") or "",reverse=True):
        row=[]
        for key in fields:
            value=c.get(key,"")
            if isinstance(value,(dict,list)):value=json.dumps(value,ensure_ascii=False,default=str)
            row.append(csv_cell(value))
        w.writerow(row)
    out.seek(0);today=chicago_now().strftime("%Y-%m-%d")
    return Response(out.getvalue(),mimetype="text/csv",headers={"Content-Disposition":f"attachment; filename=kurtex-full-{today}.csv"})

@app.route("/api/ai/fleet-knowledge",methods=["GET","POST"])
def api_fleet_knowledge():
    if not session.get("user"):return jsonify(error="unauthorized"),401
    if not _ai_is_trainer():return jsonify(error="forbidden"),403
    if request.method=="POST":
        result=fleet_knowledge_store.ingest_cases([c for c in load_cases() if not is_testing(c)])
        return jsonify(ok=True,stats=fleet_knowledge_store.stats(),**result)
    return jsonify(fleet_knowledge_store.stats())

@app.route("/api/ai/fleet-knowledge/import-csv",methods=["POST"])
def api_fleet_knowledge_csv():
    if not session.get("user"):return jsonify(error="unauthorized"),401
    if not _ai_is_trainer():return jsonify(error="forbidden"),403
    upload=request.files.get("file")
    if not upload or not (upload.filename or "").lower().endswith(".csv"):return jsonify(error="Choose a CSV file."),400
    raw=upload.read(25*1024*1024+1)
    if len(raw)>25*1024*1024:return jsonify(error="CSV is larger than 25 MB."),413
    try:text=raw.decode("utf-8-sig")
    except UnicodeDecodeError:return jsonify(error="CSV must be UTF-8 encoded."),400
    rows=[]
    for row in csv.DictReader(io.StringIO(text)):
        # Accept both Kurtex full export field names and the older friendly headers.
        normalized={str(k or "").strip():v for k,v in row.items()}
        aliases={"ID":"id","Reported By":"driver_name","Group":"group_name","Assigned To":"agent_name",
          "Status":"status","Opened":"opened_at","Closed":"closed_at","Description":"description","Notes":"notes"}
        for old,new_key in aliases.items():
            if old in normalized and new_key not in normalized:normalized[new_key]=normalized[old]
        for key in ("report_data","media"):
            if isinstance(normalized.get(key),str) and normalized[key].strip().startswith(("{","[")):
                try:normalized[key]=json.loads(normalized[key])
                except Exception:pass
        if normalized.get("id"):rows.append(normalized)
    result=fleet_knowledge_store.ingest_cases(rows)
    return jsonify(ok=True,rows=len(rows),stats=fleet_knowledge_store.stats(),**result)


# ── HTML pages ────────────────────────────────────────────────────────────────






@app.route("/api/trends")
def api_trends():
    if not session.get("user"): return jsonify({"error":"unauthorized"}),401
    if session["user"].get("role","agent") not in ("manager","admin","super_admin","developer"):
        return jsonify({"error":"This analytics tab is not available for your role."}),403
    from datetime import timedelta
    try:
        days=max(1,min(366,int(request.args.get("period","30"))))
        cases=[c for c in load_cases() if not is_testing(c)]; today=chicago_now().date()
        resolved_status={"done","resolved","closed"}
        def cat(c):
            t=" ".join(str(c.get(k) or "") for k in ("issue_text","description","notes")).lower()
            groups=[("Tires & Wheel End",("tire","wheel","hub","bearing","seal")),("Air & Suspension",("air bag","suspension","air leak","shock")),("Brakes",("brake","abs")),("Electrical & Battery",("electrical","battery","voltage","alternator","wiring")),("Cooling System",("coolant","radiator","overheat","cooling")),("Reefer System",("reefer","thermo king","carrier unit","temperature")),("Engine",("engine","oil pressure","misfire","def","dpf")),("Lighting",("light","lamp","headlight","marker")),("Fuel System",("fuel","diesel","injector"))]
            return next((n for n,ks in groups if any(k in t for k in ks)),"Other")
        start=today-timedelta(days=days-1); cur=[c for c in cases if start.isoformat()<=case_local_date(c)<=today.isoformat()]
        units={(str(c.get("unit_number") or "").strip(),str(c.get("vehicle_type") or "").lower()) for c in cur if str(c.get("unit_number") or "").strip()}
        pairs=Counter((str(c.get("unit_number") or "").strip(),cat(c)) for c in cur if str(c.get("unit_number") or "").strip())
        recurring={u for (u,_),n in pairs.items() if n>=2}; repeat=sum(max(0,n-1) for n in pairs.values())
        incomplete=sum(1 for c in cur if not str(c.get("issue_text") or c.get("description") or "").strip() or str(c.get("status") or "").lower() in ("incomplete","pending"))
        resolved=sum(1 for c in cur if str(c.get("status") or "").lower() in resolved_status)
        open_all=[c for c in cases if str(c.get("status") or "").lower() not in resolved_status]
        aged=0
        for c in open_all:
            try:
                d=case_local_date(c); aged += bool(d and (today-datetime.fromisoformat(d).date()).days>=7)
            except Exception: pass
        metrics={"total":len(cur),"units":len(units),"recurring_units":len(recurring),"recurrence_rate":round(len(recurring)/len(units)*100) if units else 0,"repeat_problems":repeat,"repeat_case_share":round(repeat/len(cur)*100) if cur else 0,"resolved":resolved,"resolution_share":round(resolved/len(cur)*100) if cur else 0,"incomplete":incomplete,"incomplete_share":round(incomplete/len(cur)*100) if cur else 0,"open_backlog":len(open_all),"aged_open":aged}
        cats=Counter(cat(c) for c in cur); categories=[{"label":k,"value":v,"detail":f"{round(v/len(cur)*100) if cur else 0}% of selected-period cases"} for k,v in cats.most_common(7)]
        equipment=Counter((str(c.get("vehicle_type") or "Unknown").strip().title() or "Unknown") for c in cur)
        equipment_mix=[{"label":k,"value":v,"detail":f"{round(v/len(cur)*100) if cur else 0}% of case volume"} for k,v in equipment.most_common()]
        hotspots=[]
        for (u,k),n in sorted(pairs.items(),key=lambda x:x[1],reverse=True):
            if n<2: continue
            vt=next((str(c.get("vehicle_type") or "Unit").title() for c in cur if str(c.get("unit_number") or "").strip()==u),"Unit")
            hotspots.append({"label":u,"value":f"{n} cases","detail":f"{vt} · {k} · same problem reported {n} times"})
            if len(hotspots)>=7: break
        complete=max(0,len(cur)-incomplete)
        data_quality=[{"label":"Complete case records","value":complete,"detail":f"{round(complete/len(cur)*100) if cur else 0}% usable without cleanup"},{"label":"Incomplete / pending records","value":incomplete,"detail":"May reduce analytics confidence"},{"label":"Cases with unit number","value":sum(1 for c in cur if str(c.get('unit_number') or '').strip()),"detail":"Can be linked to unit history"}]
        insights=[]
        if hotspots: insights.append(f"{hotspots[0]['label']} has the clearest repeat problem: {hotspots[0]['detail']}.")
        if categories: insights.append(f"{categories[0]['label']} is the most common problem category with {categories[0]['value']} cases ({categories[0]['detail']}).")
        if aged: insights.append(f"{aged} open cases have been open for more than 7 days.")
        if incomplete: insights.append(f"{incomplete} reports are incomplete or pending and may need more information.")
        if not insights: insights=["No major repeat-problem or report-quality issue stands out in the selected period."]
        return jsonify(metrics=metrics,categories=categories,recurring_hotspots=hotspots,equipment_mix=equipment_mix,data_quality=data_quality,insights=insights,sample_size=len(cur),period=days)
    except Exception:
        logger.exception("Fleet analytics failed"); return jsonify({"error":"Unable to load fleet analytics."}),500

@app.route("/api/comparison")
def api_comparison():
    if not session.get("user"): return jsonify({"error":"unauthorized"}),401
    if session["user"].get("role","agent") not in ("manager","admin","super_admin","developer"):
        return jsonify({"error":"This management tab is not available for your role."}),403
    from datetime import timedelta
    try:
        days=max(1,min(366,int(request.args.get("period","30")))); cases=[c for c in load_cases() if not is_testing(c)]; today=chicago_now().date(); start=today-timedelta(days=days-1); pe=start-timedelta(days=1); ps=pe-timedelta(days=days-1)
        cur=[c for c in cases if start.isoformat()<=case_local_date(c)<=today.isoformat()]; prev=[c for c in cases if ps.isoformat()<=case_local_date(c)<=pe.isoformat()]
        resolved_status={"done","resolved","closed"}
        def category(c):
            t=" ".join(str(c.get(k) or "") for k in ("issue_text","description","notes")).lower(); maps=[("Tires / Wheel",("tire","wheel","bearing","hub","seal")),("Air / Suspension",("air leak","suspension","air bag")),("Brakes",("brake","abs")),("Electrical",("battery","electrical","wiring","voltage")),("Cooling",("coolant","radiator","overheat")),("Reefer",("reefer","thermo king","temperature")),("Engine",("engine","dpf","def","oil pressure"))];return next((n for n,ks in maps if any(k in t for k in ks)),"Other")
        def snapshot(rows):
            us={str(c.get("unit_number") or "").strip() for c in rows if str(c.get("unit_number") or "").strip()}; pairs=Counter((str(c.get("unit_number") or "").strip(),category(c)) for c in rows if str(c.get("unit_number") or "").strip()); recurring={u for (u,_),n in pairs.items() if n>=2}; repeats=sum(max(0,n-1) for n in pairs.values()); res=sum(1 for c in rows if str(c.get("status") or "").lower() in resolved_status); inc=sum(1 for c in rows if not str(c.get("issue_text") or c.get("description") or "").strip() or str(c.get("status") or "").lower() in ("incomplete","pending")); return {"total":len(rows),"units":len(us),"recurring":recurring,"repeat":repeats,"resolved_pct":round(res/len(rows)*100) if rows else 0,"incomplete_pct":round(inc/len(rows)*100) if rows else 0}
        a,b=snapshot(cur),snapshot(prev)
        def pct(x,y): return (0 if not x else 100) if not y else round((x-y)/y*100)
        vals=[("Cases",a['total'],b['total'],"%","Total cases in each period."),("Units with cases",a['units'],b['units'],"%","Unique units with at least one case in each period."),("Units with repeat problems",len(a['recurring']),len(b['recurring']),"%","Units where the same problem category appeared at least twice."),("Repeated problems",a['repeat'],b['repeat'],"%","Additional cases for a repeated problem on the same unit."),("Resolved cases %",a['resolved_pct'],b['resolved_pct']," pp","Percentage of period cases marked resolved, done or closed."),("Incomplete reports %",a['incomplete_pct'],b['incomplete_pct']," pp","Percentage of cases missing useful issue information or marked incomplete/pending.")]
        rows=[]
        for l,x,y,u,help_text in vals:
            delta=(x-y) if u==" pp" else pct(x,y); rows.append({"label":l,"current":f"{x}%" if u==" pp" else x,"previous":f"{y}%" if u==" pp" else y,"delta":delta,"unit":u,"help":help_text})
        cc,pc=Counter(category(c) for c in cur),Counter(category(c) for c in prev); category_change=[]
        for k in set(cc)|set(pc): category_change.append({"label":k,"value":cc[k],"detail":f"Previous {pc[k]} · {pct(cc[k],pc[k]):+d}%"})
        category_change=sorted(category_change,key=lambda x:abs(int(x['detail'].split()[-1].replace('%',''))),reverse=True)[:7]
        ce=Counter((str(c.get("vehicle_type") or "Unknown").title()) for c in cur); peq=Counter((str(c.get("vehicle_type") or "Unknown").title()) for c in prev); equipment_change=[]
        for k in set(ce)|set(peq): equipment_change.append({"label":k,"value":ce[k],"detail":f"Previous {peq[k]} · {pct(ce[k],peq[k]):+d}%"})
        equipment_change=sorted(equipment_change,key=lambda x:x['value'],reverse=True)
        new=sorted(a['recurring']-b['recurring']); cleared=sorted(b['recurring']-a['recurring'])
        new_rows=[{"label":u,"value":"Repeat","detail":"Repeats a problem now, but did not in the previous period"} for u in new[:8]]; cleared_rows=[{"label":u,"value":"Clear","detail":"Repeated a problem previously, but not in the current period"} for u in cleared[:8]]
        insights=[f"Cases changed {pct(a['total'],b['total']):+d}%: {a['total']} now versus {b['total']} previously.",f"Units with repeat problems changed from {len(b['recurring'])} to {len(a['recurring'])}. {len(new)} are new repeat-problem units and {len(cleared)} no longer repeat a problem.",f"Repeated problems changed {pct(a['repeat'],b['repeat']):+d}%: {a['repeat']} now versus {b['repeat']} previously.",f"Incomplete reports are {a['incomplete_pct']}% of current cases versus {b['incomplete_pct']}% previously."]
        return jsonify(rows=rows,category_change=category_change,equipment_change=equipment_change,new_recurring=new_rows,cleared_recurring=cleared_rows,insights=insights,period=days)
    except Exception:
        logger.exception("Management comparison failed");return jsonify({"error":"Unable to build comparison."}),500

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
            "top_units": top_units[:50],
            "total_units": len(unit_data),
            "top_drivers": top_drivers[:20],
            "total_reports": len(reported),
        })
    except DataUnavailable:
        raise
    except Exception:
        logger.exception("Dashboard request failed")
        return jsonify({"error": "Unable to load data. Please retry."}), 500




def _verify_webapp_init_data(raw: str):
    """Verify Telegram Mini App initData and return the Telegram user payload."""
    if not BOT_TOKEN or not raw:
        return None
    try:
        pairs = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))
        supplied = pairs.pop("hash", "")
        auth_date = int(pairs.get("auth_date", "0"))
        if not supplied or abs(time.time() - auth_date) > 86400:
            return None
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            return None
        user = json.loads(pairs.get("user", "{}"))
        from backend.storage.user_store import is_authorized
        return user if user.get("id") and is_authorized(int(user["id"])) else None
    except Exception:
        return None

def _miniapp_user():
    return _verify_webapp_init_data(request.headers.get("X-Telegram-Init-Data", ""))

def _report_markdown(data):
    labels={"truck":"Truck","trailer":"Trailer","reefer":"Reefer"}; icons={"low":"🟢","medium":"🟡","high":"🔴"}
    def esc(v):
        v=str(v or "—")
        for ch in "_*`[": v=v.replace(ch,"\\"+ch)
        return v
    vt=data.get("vehicle_type","truck"); pr=data.get("priority","medium")
    unit_label="Truck" if vt=="truck" else "Trailer"
    lines=[f"{icons.get(pr,'🟡')} *Case Report — {labels.get(vt,vt.title())}*",f"Priority: *{pr.title()}*","",f"*{unit_label}:* {esc(data.get('unit_number'))}",f"*Driver:* {esc(data.get('driver'))}","",f"*Issue:* {esc(data.get('issue'))}","",f"*{esc(data.get('load'))}*",f"Pick up Location/Time: {esc(data.get('pickup'))}",f"Delivery Location/Time: {esc(data.get('delivery'))}",f"Current Location: {esc(data.get('location'))}"]
    if vt=="reefer": lines += ["",f"*Setpoint:* {esc(data.get('setpoint'))}",f"*Current temp:* {esc(data.get('current_temp'))}",f"*Temp recorder:* {esc(data.get('temp_recorder'))}"]
    if data.get("comments"): lines += ["",f"*Comments:* {esc(data.get('comments'))}"]
    lines += ["",f"*Reported by:* {esc(data.get('handler'))}"]
    return "\n".join(lines)

@app.route("/telegram/report")
def telegram_report_app():
    return render_template("telegram_report.html")

@app.route("/api/telegram-report/case")
def telegram_report_case():
    user=_miniapp_user()
    if not user: return jsonify({"error":"Open this report from Kurtex in Telegram."}),401
    case_id=request.args.get("case_id","")
    case=next((c for c in load_cases() if str(c.get("id"))==case_id),None)
    if not case or case.get("status") not in ("assigned","reported"):
        return jsonify({"error":"This case is no longer active."}),404
    return jsonify({"id":case_id,"driver_name":case.get("driver_name",""),"group_name":case.get("group_name",""),"description":case.get("description","")})

@app.route("/api/telegram-report/submit",methods=["POST"])
def telegram_report_submit():
    user=_miniapp_user()
    if not user: return jsonify({"error":"Telegram authorization expired. Reopen the report from the bot."}),401
    case_id=(request.form.get("case_id") or "").strip()
    cases=load_cases(); case=next((c for c in cases if str(c.get("id"))==case_id),None)
    if not case or case.get("status") not in ("assigned","reported"):
        return jsonify({"error":"This case is no longer active."}),409
    from backend.storage.user_store import get_user
    stored=get_user(int(user["id"])) or {}
    data={k:(request.form.get(k) or "").strip() for k in ("vehicle_type","unit_number","driver","issue","load","pickup","delivery","location","setpoint","current_temp","temp_recorder","comments","priority")}
    if data["vehicle_type"] not in ("truck","trailer","reefer") or not data["unit_number"] or not data["issue"]:
        return jsonify({"error":"Equipment, unit number and issue are required."}),400
    if data["priority"] not in ("low","medium","high"): data["priority"]="medium"
    data["handler"]=stored.get("name") or user.get("first_name") or "Kurtex user"
    report_text=_report_markdown(data)
    uploads=request.files.getlist("attachments")[:10]
    try:
        import asyncio
        from telegram import Bot, InputFile
        async def send():
            bot=Bot(BOT_TOKEN); dest=int(os.getenv("REPORTS_GROUP_ID","0") or 0)
            if not dest: raise RuntimeError("No reports group configured")
            await bot.send_message(dest,report_text,parse_mode="Markdown")
            for f in uploads:
                raw=f.read(); f.seek(0); inp=InputFile(io.BytesIO(raw),filename=f.filename or "attachment")
                mime=(f.mimetype or "").lower()
                if mime.startswith("image/"): await bot.send_photo(dest,inp)
                elif mime.startswith("video/"): await bot.send_video(dest,inp)
                else: await bot.send_document(dest,inp)
        asyncio.run(send())
        from backend.storage.case_store import report_case, _load, _save, CASES_FILE
        report_case(case_id); fresh=_load(CASES_FILE)
        for c in fresh:
            if str(c.get("id"))==case_id:
                c.update({"vehicle_type":data["vehicle_type"],"unit_number":data["unit_number"],"report_driver":data["driver"],"issue_text":data["issue"],"load_type":data["load"],"location":data["location"],"priority":data["priority"],"pickup":data["pickup"],"delivery":data["delivery"],"comments":data["comments"],"setpoint":data["setpoint"],"current_temp":data["current_temp"],"temp_recorder":data["temp_recorder"],"report_text":report_text,"report_data":data,"media":[{"name":f.filename,"type":f.mimetype} for f in uploads]}); break
        _save(CASES_FILE,fresh)
        return jsonify({"ok":True})
    except Exception as exc:
        logger.exception("Telegram mini report submit failed")
        return jsonify({"error":"Report could not be sent. Please retry."}),500


@app.route("/login")
def login():
    return render_template("login.html", bot_username=get_bot_username(), error=request.args.get("error"))

@app.route("/")
def index():
    if not session.get("user"): return redirect("/login")
    user = session["user"]
    is_manager = user.get("role","agent") in ("manager","admin","developer","super_admin")
    is_developer = user.get("role","agent") == "developer"
    allowed_pages = _load_role_permissions().get(_website_role(user.get("role","agent")), ROLE_PERMISSION_DEFAULTS["agent"])
    return render_template("dashboard.html", user=user, is_manager=is_manager, is_developer=is_developer, allowed_pages=allowed_pages)

def _learning_sync_worker():
    from threading import Event
    try:
        for owner,chats in chat_store.all_chats():
            for chat in chats:learning_store.capture_chat(owner,chat)
    except Exception:logger.exception("Initial chat-learning import failed")
    signature=None
    while True:
        try:
            path=DATA_DIR / "cases.json";stat=path.stat();current=(stat.st_ino,stat.st_size,stat.st_mtime_ns)
            if current!=signature:
                cases,stale=case_snapshot.read(path)
                if not stale:
                    fleet_knowledge_store.ingest_cases([c for c in cases if not is_testing(c)])
                    signature=current
        except Exception:logger.exception("Case-learning sync failed")
        Event().wait(60)

def run_dashboard():
    Thread(target=_learning_sync_worker,daemon=True,name="maintenance-learning-sync").start()
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=DASHBOARD_PORT, debug=False, use_reloader=False)

def start_dashboard_thread():
    Thread(target=run_dashboard, daemon=True).start()
    logger.info(f"Dashboard started on port {DASHBOARD_PORT}")



# ── Operations intelligence: recurring issues, similarity, knowledge ─────────
def _case_search_text(c):
    return " ".join(str(c.get(k) or "") for k in ("description","notes","issue_text","vehicle_type","unit_number","load_type")).lower()

def _terms(text):
    stop={"the","and","for","with","from","that","this","truck","trailer","unit","driver","case","issue","was","are","not","but","has","have","had","into","out","too","very","need","needs","vehicle"}
    return {w for w in re.findall(r"[a-z0-9][a-z0-9_-]{2,}",(text or "").lower()) if w not in stop}

@app.route("/api/recurring_problems")
def api_recurring_problems():
    if not session.get("user"): return jsonify({"error":"unauthorized"}),401
    try:
        cases=load_cases(); groups=defaultdict(list)
        for c in cases:
            unit=(c.get("unit_number") or "").strip()
            if not unit: continue
            words=_terms(_case_search_text(c))
            # stable operational buckets, only returned when supported by real case text
            buckets={
              "Air / suspension":{"air","suspension","bag","spring","leak","pressure"},
              "Brakes":{"brake","abs","chamber","slack"},
              "Cooling / overheating":{"coolant","cooling","overheat","thermostat","radiator"},
              "Electrical / starting":{"battery","starter","alternator","electrical","voltage","crank"},
              "Reefer":{"reefer","temperature","temp","thermo","carrier","cooling"},
              "Tires / wheel end":{"tire","wheel","hub","bearing","seal"},
              "Aftertreatment":{"def","dpf","scr","regen","emission"},
            }
            for label,keys in buckets.items():
                if words & keys: groups[(unit,label)].append(c)
        rows=[]
        for (unit,label),items in groups.items():
            if len(items)<2: continue
            latest=max((x.get("opened_at") or "") for x in items)
            vtype=next(((x.get("vehicle_type") or "").strip().lower() for x in items if (x.get("vehicle_type") or "").strip()), "")
            rows.append({"unit":unit,"vehicle_type":vtype,"problem":label,"count":len(items),"latest":fmt_dt(latest),"cases":[serialize_case(x) for x in sorted(items,key=lambda z:z.get("opened_at","") or "",reverse=True)[:4]]})
        rows.sort(key=lambda x:(-x["count"],x["unit"]))
        return jsonify({"items":rows[:30]})
    except Exception as e:
        logger.error("recurring problems error: %s",e); return jsonify({"items":[]})

@app.route("/api/similar_cases")
def api_similar_cases():
    if not session.get("user"): return jsonify({"error":"unauthorized"}),401
    cid=request.args.get("id","").strip(); query=request.args.get("q","").strip()
    try:
        cases=load_cases(); current=None
        if cid:
            current=next((c for c in cases if (c.get("id") or "")==cid or (c.get("id") or "").startswith(cid)),None)
            if current: query=_case_search_text(current)
        qterms=_terms(query)
        scored=[]
        for c in cases:
            if current is c: continue
            terms=_terms(_case_search_text(c)); overlap=qterms & terms
            if not overlap: continue
            score=len(overlap)/(max(1,len(qterms|terms))**0.5)
            if (current and current.get("unit_number") and c.get("unit_number")==current.get("unit_number")): score+=.35
            scored.append((score,c,sorted(overlap)[:6]))
        scored.sort(key=lambda x:x[0],reverse=True)
        return jsonify({"items":[{"score":round(sc,2),"matched":m,"case":serialize_case(c)} for sc,c,m in scored[:6]]})
    except Exception as e:
        logger.error("similar cases error: %s",e); return jsonify({"items":[]})

@app.route("/api/part_cases")
def api_part_cases():
    if not session.get("user"): return jsonify({"error":"unauthorized"}),401
    q=request.args.get("q","").strip(); keys=_terms(q)
    try:
        rows=[]
        for c in load_cases():
            text=_case_search_text(c); hit=[k for k in keys if k in text]
            if hit: rows.append((len(hit),c,hit))
        rows.sort(key=lambda x:(-x[0],x[1].get("opened_at","") or ""),reverse=False)
        return jsonify({"items":[{"matched":h[:5],"case":serialize_case(c)} for _,c,h in rows[:30]]})
    except Exception as e:
        logger.error("part cases error: %s",e); return jsonify({"items":[]})

KNOWLEDGE_FILE=DATA_DIR/"knowledge_notes.json"
def _read_knowledge():
    try:
        x=json.loads(KNOWLEDGE_FILE.read_text(encoding="utf-8")) if KNOWLEDGE_FILE.exists() else []
        return x if isinstance(x,list) else []
    except Exception: return []
def _write_knowledge(items):
    tmp=KNOWLEDGE_FILE.with_suffix('.tmp'); tmp.write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(KNOWLEDGE_FILE)

@app.route("/api/knowledge_notes",methods=["GET","POST"])
def api_knowledge_notes():
    if not session.get("user"): return jsonify({"error":"unauthorized"}),401
    items=_read_knowledge()
    if request.method=="GET":
        part=request.args.get("part","").strip().lower()
        if part: items=[x for x in items if (x.get("part") or "").lower()==part]
        return jsonify({"items":items[-50:][::-1]})
    data=request.get_json(silent=True) or {}; note=str(data.get("note") or "").strip()[:1500]; part=str(data.get("part") or "").strip()[:120]
    if not note: return jsonify({"error":"Note is required"}),400
    u=session.get("user") or {}; author=(u.get("first_name") or u.get("name") or u.get("username") or "Agent") if isinstance(u,dict) else str(u); item={"id":secrets.token_hex(6),"part":part,"note":note,"author":author,"created":chicago_now().strftime("%Y-%m-%d %H:%M")}
    items.append(item); _write_knowledge(items[-1000:]); return jsonify(item),201

@app.route("/api/knowledge_notes/<note_id>",methods=["PUT","DELETE"])
def api_knowledge_note_item(note_id):
    if not session.get("user"): return jsonify({"error":"unauthorized"}),401
    items=_read_knowledge(); idx=next((i for i,x in enumerate(items) if str(x.get("id"))==str(note_id)),None)
    if idx is None: return jsonify({"error":"not found"}),404
    if request.method=="DELETE":
        deleted=items.pop(idx); _write_knowledge(items); return jsonify({"ok":True,"id":deleted.get("id")})
    data=request.get_json(silent=True) or {}; note=str(data.get("note") or "").strip()[:1500]
    if not note: return jsonify({"error":"Note is required"}),400
    u=session.get("user") or {}; author=(u.get("first_name") or u.get("name") or u.get("username") or "Agent") if isinstance(u,dict) else str(u)
    items[idx]["note"]=note; items[idx]["updated"]=chicago_now().strftime("%Y-%m-%d %H:%M"); items[idx]["updated_by"]=author
    _write_knowledge(items); return jsonify(items[idx])


@app.route('/api/ai/chats/<chat_id>/files/<file_id>/transcript',methods=['GET','PATCH','POST'])
@ai_serialized
def api_video_transcript(chat_id,file_id):
    chats=_user_chats();chat=next((c for c in chats if c.get('id')==chat_id),None)
    if not chat:return jsonify(error='Chat not found'),404
    item=next((a for a in chat.get('attachments',[]) if a.get('id')==file_id and a.get('kind') in ('video','audio')),None)
    if not item:return jsonify(error='Media not found'),404
    if request.method=='PATCH':
        data=request.get_json(silent=True) or {}
        item['transcript']=str(data.get('transcript','')).strip()[:16000]
        item['audio_status']='agent_corrected';item['warning']='';_save_user_chats(chats)
    elif request.method=='POST':
        audio_ref=item.get('audio_blob') or (item.get('blob') if item.get('kind')=='audio' else None)
        if not audio_ref:return jsonify(error='No audio is available. Type the driver explanation instead.'),400
        try:item['transcript']=transcribe_audio(chat_store.blob_path(audio_ref).read_bytes())
        except Exception:return jsonify(error='Speech transcription failed. Please try again or enter the explanation.'),503
        item['audio_status']='transcribed' if item['transcript'] else 'no_speech';item['warning']='';_save_user_chats(chats)
    return jsonify(transcript=item.get('transcript',''),audio_status=item.get('audio_status'),warning=item.get('warning',''))

register_learning_routes(app,learning_store,chat_store,_ai_user_key,_ai_is_trainer,load_cases,is_testing,ai_serialized)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_dashboard()
