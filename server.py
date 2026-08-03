"""
OILLOG — Flask server for Railway deployment
Serves mobile + admin PWA, Telegram auth, JSON-file storage on Railway volume, real-time sync API.

User accounts/roles are NOT managed here — they live in the shared user_store
(storage/user_store.py), the same store used by the Kurtex Alert Bot. That bot
is the single source of truth: an admin adds/removes people and sets roles
there (via /adduser, /removeuser, /editrole in Telegram), and this app just
reads that same data to decide who gets in. See storage/user_store.py.
"""
import hashlib, hmac, json, logging, os, time
from pathlib import Path
from functools import wraps

from flask import Flask, jsonify, request, session, redirect, send_from_directory

from storage.user_store import get_user

logger = logging.getLogger(__name__)
app = Flask(__name__)
app.secret_key = os.getenv("OILLOG_SECRET", "oillog-secret-change-me")

DATA_DIR = Path(os.getenv("OILLOG_DATA_DIR", "/app/data"))
BOT_TOKEN = os.getenv("BOT_TOKEN", "")  # ← SET THIS to the SAME bot token as the alert bot

# Roles allowed into the admin panel. Everyone else (e.g. "agent") only gets the mobile app.
ADMIN_ROLES = {"developer", "super_admin"}

DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── File-backed stores ─────────────────────────────────────────────────────

def _read_json(name):
    p = DATA_DIR / name
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except:
        return []

def _write_json(name, data):
    p = DATA_DIR / name
    p.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")

def load_entries():
    return _read_json("entries.json")

def save_entries(entries):
    _write_json("entries.json", entries)

def load_units():
    return _read_json("units.json")

def save_units(units):
    _write_json("units.json", units)

def load_settings():
    s = _read_json("settings.json")
    if isinstance(s, dict):
        return s
    return {"T": 15000, "R": 500}

def save_settings(s):
    _write_json("settings.json", s)

# ── Auth helpers ──────────────────────────────────────────────────────────

def verify_telegram_login(data):
    check_hash = data.pop("hash", "")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    computed = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
    data["hash"] = check_hash
    if abs(time.time() - int(data.get("auth_date", 0))) > 86400:
        return False
    return hmac.compare_digest(computed, check_hash)

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper

# ── Auth routes ─────────────────────────────────────────────────────────────
# Access is controlled entirely by the shared user_store (see storage/user_store.py),
# the same file the Kurtex Alert Bot manages via /adduser, /removeuser, /editrole.
# There is no separate whitelist or Telegram webhook here anymore — the bot is
# the single place admins manage who's allowed in and with what role.

@app.route("/auth/telegram")
def telegram_auth():
    data = dict(request.args)
    next_url = data.pop("next", "/") or "/"
    if not next_url.startswith("/"):
        next_url = "/"  # never redirect off-site

    if not data.get("hash"):
        return redirect(f"{next_url}?error=missing")
    if not verify_telegram_login(data):
        return redirect(f"{next_url}?error=invalid")

    telegram_id = int(data.get("id", 0))
    u = get_user(telegram_id)
    if not u:
        return redirect(f"{next_url}?error=not_whitelisted")

    role = u.get("role", "agent")
    if next_url.startswith("/admin") and role not in ADMIN_ROLES:
        return redirect("/admin?error=forbidden_role")

    session["user"] = {
        "id": telegram_id,
        "first_name": data.get("first_name", "") or u.get("name", ""),
        "username": data.get("username", "") or u.get("username", ""),
        "photo_url": data.get("photo_url", ""),
        "auth_date": data.get("auth_date", ""),
        "role": role,
    }
    return redirect(next_url)

@app.route("/auth/guest", methods=["POST"])
def guest_auth():
    data = request.json or {}
    name = data.get("name", "Guest")
    session["user"] = {
        "id": 0,
        "first_name": name,
        "username": name.lower(),
        "photo_url": "",
        "auth_date": str(int(time.time())),
        "role": "guest",
    }
    return jsonify({"ok": True, "user": session["user"]})

@app.route("/auth/status", methods=["GET"])
def auth_status():
    if session.get("user"):
        return jsonify({"ok": True, "user": session["user"]})
    return jsonify({"ok": False, "user": None})

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})

# ── API ──────────────────────────────────────────────────────────────────

@app.route("/api/sync")
@login_required
def api_sync():
    """Return all data for real-time sync."""
    entries = load_entries()
    units = load_units()
    settings = load_settings()
    # Return newest first
    entries.sort(key=lambda e: e.get("createdAt", 0), reverse=True)
    return jsonify({
        "entries": entries,
        "units": units,
        "settings": settings,
    })

@app.route("/api/entries", methods=["POST"])
@login_required
def api_create_entry():
    data = request.json or {}
    if not data.get("id") or not data.get("unit"):
        return jsonify({"error": "missing_fields"}), 400
    entries = load_entries()
    entries.append(data)
    save_entries(entries)
    return jsonify(data), 201

@app.route("/api/entries/<entry_id>", methods=["PUT"])
@login_required
def api_update_entry(entry_id):
    data = request.json or {}
    entries = load_entries()
    for i, e in enumerate(entries):
        if e.get("id") == entry_id:
            entries[i] = data
            save_entries(entries)
            return jsonify(data)
    return jsonify({"error": "not_found"}), 404

@app.route("/api/entries/<entry_id>", methods=["DELETE"])
@login_required
def api_delete_entry(entry_id):
    entries = load_entries()
    new_entries = [e for e in entries if e.get("id") != entry_id]
    if len(new_entries) < len(entries):
        save_entries(new_entries)
        return jsonify({"ok": True})
    return jsonify({"error": "not_found"}), 404

@app.route("/api/units/<path:unit_key>", methods=["POST"])
@login_required
def api_save_unit(unit_key):
    data = request.json or {}
    val = data.get("currentValue")
    units = load_units()
    units[unit_key] = {
        "currentValue": val,
        "updatedAt": int(time.time() * 1000),
        "updatedBy": session["user"].get("first_name", ""),
    }
    save_units(units)
    return jsonify(units[unit_key])

@app.route("/api/settings", methods=["PUT"])
@login_required
def api_update_settings():
    data = request.json or {}
    safe = {}
    if "T" in data:
        safe["T"] = int(data["T"])
    if "R" in data:
        safe["R"] = int(data["R"])
    if safe:
        save_settings(safe)
    return jsonify(safe)

# ── Serve static files ────────────────────────────────────────────────────

FRONTEND_DIR = Path(__file__).parent

@app.route("/")
def serve_index():
    return send_from_directory(str(FRONTEND_DIR), "oillog-mobile.html")

@app.route("/admin")
def serve_admin():
    return send_from_directory(str(FRONTEND_DIR), "oillog-admin.html")

@app.route("/<path:filename>")
def serve_static(filename):
    return send_from_directory(str(FRONTEND_DIR), filename)

# ── Main ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
