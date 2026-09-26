"""
dashboard.py — Kurtex Alert Bot Web Dashboard
Routes and read-only API. Presentation lives in templates/ and static/.
"""
import csv, hashlib, hmac, io, json, logging, os, re, secrets, time, uuid, urllib.parse, urllib.request
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
KURTEX_AI_MODEL = os.getenv("KURTEX_AI_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast").strip()

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
- Keep answers practical and concise for dispatch/maintenance agents.
- Reply in the language used by the agent unless asked otherwise.
"""

def _cf_ai(messages, max_tokens=700, temperature=0.2):
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        raise RuntimeError("Workers AI is not configured")
    url=f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/{KURTEX_AI_MODEL}"
    req=urllib.request.Request(url,data=json.dumps({"messages":messages,"max_tokens":max_tokens,"temperature":temperature}).encode("utf-8"),
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
    return {"id":c.get("id") or "","driver":c.get("report_driver") or c.get("driver_name") or "",
      "unit":c.get("unit_number") or "","type":(c.get("vehicle_type") or "").lower(),
      "issue":c.get("issue_text") or c.get("description") or "","description":c.get("description") or "",
      "notes":c.get("notes") or "","status":c.get("status") or "","opened":c.get("opened_at") or ""}

def _ai_context(query="",limit=60):
    cases=[c for c in load_cases() if not is_testing(c)]; terms=_terms(query) if "_terms" in globals() else set()
    def rank(c):
        txt=" ".join(str(c.get(k) or "") for k in ("description","notes","issue_text","vehicle_type","unit_number","report_driver")).lower()
        return (sum(1 for t in terms if t in txt),c.get("opened_at") or "")
    cases=sorted(cases,key=rank,reverse=True)[:limit]
    notes=_read_knowledge()[-30:] if "_read_knowledge" in globals() else []
    return {"cases":[_ai_case_record(c) for c in cases],"knowledge_notes":notes,"approved_maintenance_knowledge":_ai_knowledge_matches(query,12) if "_ai_knowledge_matches" in globals() else []}


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
    return isinstance(user,dict) and user.get("role","agent") in ("developer","super_admin")

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

def _ai_knowledge_matches(query,limit=12):
    items=_read_ai_knowledge(); q=set(re.findall(r"[\w-]{3,}",str(query).lower(),flags=re.UNICODE))
    def score(x):
        txt=(str(x.get("title") or "")+" "+str(x.get("content") or "")+" "+" ".join(x.get("tags") or [])).lower()
        return sum(1 for t in q if t in txt)
    ranked=sorted(items,key=lambda x:(score(x),x.get("updated_at") or x.get("created_at") or ""),reverse=True)
    hits=[x for x in ranked if score(x)>0]
    return (hits or ranked[:4])[:limit]

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
          "content":content,"tags":[str(x)[:50] for x in (data.get("tags") or []) if str(x).strip()][:12],
          "source":"manual","created_at":now,"updated_at":now,"created_by":_ai_user_key() if "_ai_user_key" in globals() else "admin"}
    items.append(item); _write_ai_knowledge(items); return jsonify(item),201

@app.route("/api/ai/knowledge/upload",methods=["POST"])
def api_ai_knowledge_upload():
    if not session.get("user"):return jsonify({"error":"unauthorized"}),401
    if not _ai_is_trainer():return jsonify({"error":"forbidden"}),403
    f=request.files.get("file")
    if not f or not f.filename:return jsonify({"error":"File is required"}),400
    ext=Path(f.filename).suffix.lower()
    if ext not in (".txt",".md",".csv",".json"):return jsonify({"error":"Use TXT, MD, CSV or JSON for this version"}),400
    raw=f.read(2_000_001)
    if len(raw)>2_000_000:return jsonify({"error":"File is too large (2 MB max)"}),400
    try: content=raw.decode("utf-8")
    except UnicodeDecodeError:
        try: content=raw.decode("latin-1")
        except Exception:return jsonify({"error":"Could not read text file"}),400
    now=datetime.now().astimezone().isoformat(timespec="seconds"); items=_read_ai_knowledge()
    item={"id":uuid.uuid4().hex,"title":Path(f.filename).name[:120],"content":content[:120000],
          "tags":["uploaded"],"source":"file","created_at":now,"updated_at":now,"created_by":_ai_user_key()}
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
        item["updated_at"]=_now_iso()
        _write_ai_knowledge(items);return jsonify(item)
    new=[x for x in items if str(x.get("id"))!=item_id]
    _write_ai_knowledge(new); return jsonify({"ok":True})

AI_CHAT_FILE = DATA_DIR / "ai_chats.json"

def _ai_user_key():
    user=session.get("user") or {}
    if isinstance(user,dict):
        return str(user.get("id") or user.get("username") or user.get("email") or user.get("first_name") or "user")
    return str(user or "user")

def _read_ai_chats():
    try:
        if AI_CHAT_FILE.exists():
            data=json.loads(AI_CHAT_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data,dict) else {}
    except Exception as e: logger.warning("AI chat history read failed: %s",e)
    return {}

def _write_ai_chats(data):
    AI_CHAT_FILE.parent.mkdir(parents=True,exist_ok=True)
    tmp=AI_CHAT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(AI_CHAT_FILE)

def _user_chats():
    data=_read_ai_chats(); key=_ai_user_key()
    chats=data.get(key,[])
    return chats if isinstance(chats,list) else []

def _save_user_chats(chats):
    data=_read_ai_chats(); data[_ai_user_key()]=chats[-100:]; _write_ai_chats(data)

def _chat_title(text):
    clean=re.sub(r"\s+"," ",str(text or "")).strip()
    return (clean[:52]+"…") if len(clean)>52 else (clean or "New maintenance chat")

def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")

@app.route("/api/ai/chats",methods=["GET","POST"])
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
    chats.append(chat); _save_user_chats(chats)
    return jsonify(chat),201

@app.route("/api/ai/chats/<chat_id>",methods=["GET","DELETE","PATCH"])
def api_ai_chat_item(chat_id):
    if not session.get("user"):return jsonify({"error":"unauthorized"}),401
    chats=_user_chats(); chat=next((c for c in chats if str(c.get("id"))==chat_id),None)
    if not chat:return jsonify({"error":"Chat not found"}),404
    if request.method=="GET":return jsonify(chat)
    if request.method=="DELETE":
        _save_user_chats([c for c in chats if str(c.get("id"))!=chat_id]); return jsonify({"ok":True})
    data=request.get_json(silent=True) or {}; title=str(data.get("title") or "").strip()
    if title:chat["title"]=title[:80];chat["updated_at"]=_now_iso();_save_user_chats(chats)
    return jsonify(chat)

@app.route("/api/ai/status")
def api_ai_status():
    if not session.get("user"): return jsonify({"error":"unauthorized"}),401
    return jsonify({"configured":bool(CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN),"model":KURTEX_AI_MODEL,"can_train":_ai_is_trainer(),"knowledge_count":len(_read_ai_knowledge())})

@app.route("/api/ai/chat",methods=["POST"])
def api_ai_chat():
    if not session.get("user"): return jsonify({"error":"unauthorized"}),401
    data=request.get_json(silent=True) or {}; message=str(data.get("message") or "").strip()[:4000]
    page=str(data.get("page") or "dashboard")[:80]; chat_id=str(data.get("chat_id") or "").strip()
    if not message:return jsonify({"error":"Message is required"}),400
    chats=_user_chats(); chat=next((c for c in chats if str(c.get("id"))==chat_id),None)
    if chat is None:
        chat={"id":uuid.uuid4().hex,"title":_chat_title(message),"created_at":_now_iso(),"updated_at":_now_iso(),"messages":[]}
        chats.append(chat)
    history=chat.get("messages") if isinstance(chat.get("messages"),list) else []
    try:
        ctx=_ai_context(message,70)
        messages=[{"role":"system","content":KURTEX_AI_SYSTEM},{"role":"system","content":
          "Current Kurtex page: "+page+"\nRead-only Kurtex context follows. Never claim a record exists unless present here.\nKURTEX CONTEXT:\n"+
          json.dumps(ctx,ensure_ascii=False)}]
        for item in history[-12:]:
            role=item.get("role"); content=str(item.get("content") or "")[:3000]
            if role in ("user","assistant") and content:messages.append({"role":role,"content":content})
        messages.append({"role":"user","content":message})
        answer=_cf_ai(messages,850,.2)
        history.extend([{"role":"user","content":message,"at":_now_iso()},{"role":"assistant","content":answer,"at":_now_iso()}])
        chat["messages"]=history[-80:]; chat["updated_at"]=_now_iso()
        if not chat.get("title") or chat.get("title")=="New maintenance chat":chat["title"]=_chat_title(message)
        _save_user_chats(chats)
        return jsonify({"answer":answer,"chat_id":chat["id"],"title":chat["title"]})
    except Exception as e:
        logger.warning("Kurtex AI chat failed: %s",e)
        _notify_user("ai","Kurtex AI unavailable","The AI request failed. Check Workers AI access or try again later.","warning")
        return jsonify({"error":"Kurtex AI is temporarily unavailable."}),503

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
    strong=[c for c in ranked if score(c)>0]; recent=sorted(all_cases,key=lambda c:c.get("opened_at") or "",reverse=True)[:20]
    out=[]; seen=set()
    for c in strong[:limit]+recent:
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
        candidates=_part_case_candidates(part,90)
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
Use exact IDs only. Include useful matches at 60+ confidence; prefer 3-8 when evidence exists.
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
            if cid in by_id and score>=60:
                out.append({"case":serialize_case(by_id[cid]),"score":min(score,100),"reason":str(m.get("reason") or "")[:180]})
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
            rows.append({"unit":unit,"problem":label,"count":len(items),"latest":fmt_dt(latest),"cases":[serialize_case(x) for x in sorted(items,key=lambda z:z.get("opened_at","") or "",reverse=True)[:4]]})
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
