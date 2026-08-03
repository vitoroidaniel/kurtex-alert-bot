"""
OILLOG Server — Flask backend with REST API + Telegram Login
Stores data as JSON files on /data/ (Railway persistent volume)
"""
import hashlib, hmac, json, logging, os, time
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps

from flask import Flask, jsonify, request, session, redirect, send_from_directory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder=None)
app.secret_key = os.getenv("OILLOG_SECRET", "oillog-secret-change-me")

# ── Configuration ────────────────────────────────────────────────────────────
DATA_DIR       = Path(os.getenv("DATA_DIR", "/app/data"))
BOT_TOKEN      = os.getenv("BOT_TOKEN", "8783000783:AAH0nsNC0Mh0egLVdKd9i5lm1-fZuQ7Ltos")
BOT_USERNAME   = os.getenv("BOT_USERNAME", "@kurtexalertsbot")
PORT           = int(os.getenv("PORT", "8080"))

# Ensure data directory exists
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── File-based storage helpers ───────────────────────────────────────────────
def _entries_path():
    return DATA_DIR / "entries.json"

def _units_path():
    return DATA_DIR / "units.json"

def _settings_path():
    return DATA_DIR / "settings.json"

def _users_path():
    return DATA_DIR / "users.json"

def read_json(path, default=None):
    if default is None: default = []
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"read_json error {path}: {e}")
    return default

def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def load_entries():
    return read_json(_entries_path(), [])

def save_entries(entries):
    write_json(_entries_path(), entries)

def load_units():
    return read_json(_units_path(), {})

def save_units(units):
    write_json(_units_path(), units)

def load_settings():
    default = {"T": 15000, "R": 500}
    return read_json(_settings_path(), default)

def save_settings(settings):
    write_json(_settings_path(), settings)

def load_users():
    return read_json(_users_path(), {})

def save_users(users):
    write_json(_users_path(), users)

# ── Telegram Auth ────────────────────────────────────────────────────────────
def verify_telegram_login(data):
    """Verify Telegram Login Widget data (HMAC-SHA256)."""
    data = dict(data)
    check_hash = data.pop("hash", "")
    if not check_hash:
        return False
    # Sort keys alphabetically, build data_check_string
    items = sorted(data.items())
    data_check_string = "\n".join(f"{k}={v}" for k, v in items)
    secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    # Re-add hash
    data["hash"] = check_hash
    # Check auth_date is not older than 24h
    auth_date = int(data.get("auth_date", 0))
    if abs(time.time() - auth_date) > 86400:
        return False
    return hmac.compare_digest(computed, check_hash)

# ── Auth helpers ─────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

def get_user_role():
    user = session.get("user", {})
    return user.get("role", "mechanic")

# ── Auth Routes ──────────────────────────────────────────────────────────────
@app.route("/auth/telegram", methods=["POST"])
def telegram_auth():
    """Telegram Login Widget callback — accepts JSON body from client-side auth."""
    data = request.get_json(force=True)
    if not verify_telegram_login(data):
        return jsonify({"ok": False, "error": "Invalid auth"}), 403

    user_id = int(data.get("id", 0))
    first_name = data.get("first_name", "")
    username = data.get("username", "")
    photo_url = data.get("photo_url", "")

    # Determine role: if already registered, keep role; default "mechanic"
    users = load_users()
    existing = users.get(str(user_id), {})
    role = existing.get("role", "mechanic")

    # Save/update user
    users[str(user_id)] = {
        "id": user_id,
        "first_name": first_name,
        "username": username,
        "photo_url": photo_url,
        "role": role,
        "last_login": datetime.now(timezone.utc).isoformat()
    }
    save_users(users)

    session["user"] = {
        "id": user_id,
        "first_name": first_name,
        "username": username,
        "photo_url": photo_url,
        "role": role,
    }
    return jsonify({"ok": True, "user": session["user"]})

@app.route("/auth/guest", methods=["POST"])
def guest_auth():
    """Guest login — just set a simple session."""
    data = request.get_json(force=True) or {}
    name = data.get("name", "Guest")
    session["user"] = {
        "id": 0,
        "first_name": name,
        "username": "guest",
        "photo_url": "",
        "role": "mechanic",
    }
    return jsonify({"ok": True, "user": session["user"]})

@app.route("/auth/status", methods=["GET"])
def auth_status():
    if session.get("user"):
        return jsonify({"ok": True, "user": session["user"]})
    return jsonify({"ok": False}), 200

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})

# ── API: Entries ─────────────────────────────────────────────────────────────
@app.route("/api/entries", methods=["GET", "POST"])
@login_required
def api_entries():
    if request.method == "GET":
        entries = load_entries()
        # Optional filter by since timestamp (for sync)
        since = request.args.get("since", "")
        if since:
            try:
                since_ts = int(since)
                entries = [e for e in entries if e.get("createdAt", 0) > since_ts or e.get("updatedAt", 0) > since_ts]
            except ValueError:
                pass
        return jsonify(entries)

    # POST — create new entry
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "no data"}), 400

    entry = {
        "id": data.get("id", str(int(time.time() * 1000)) + "_" + os.urandom(4).hex()),
        "date": data.get("date", ""),
        "type": data.get("type", "T"),
        "unit": data.get("unit", "").strip(),
        "unitOfValue": data.get("unitOfValue", "mi"),
        "value": data.get("value", "").strip(),
        "addedBy": data.get("addedBy", session["user"].get("first_name", "Unknown")),
        "sent": bool(data.get("sent", False)),
        "createdAt": data.get("createdAt", int(time.time() * 1000)),
        "updatedAt": data.get("updatedAt") or data.get("createdAt", int(time.time() * 1000)),
    }

    entries = load_entries()
    entries.insert(0, entry)
    save_entries(entries)
    return jsonify(entry), 201


@app.route("/api/entries/<entry_id>", methods=["GET", "PUT", "DELETE"])
@login_required
def api_entry(entry_id):
    entries = load_entries()
    entry = next((e for e in entries if e.get("id") == entry_id), None)
    if not entry:
        return jsonify({"error": "not found"}), 404

    if request.method == "GET":
        return jsonify(entry)

    if request.method == "PUT":
        data = request.get_json(force=True)
        for key in ("date", "type", "unit", "unitOfValue", "value", "addedBy", "sent"):
            if key in data:
                entry[key] = data[key]
        entry["updatedAt"] = int(time.time() * 1000)
        save_entries(entries)
        return jsonify(entry)

    if request.method == "DELETE":
        entries = [e for e in entries if e.get("id") != entry_id]
        save_entries(entries)
        return jsonify({"ok": True})


@app.route("/api/entries/bulk-sent", methods=["POST"])
@login_required
def api_bulk_sent():
    """Mark multiple entries as sent."""
    data = request.get_json(force=True) or {}
    ids = data.get("ids", [])
    if not ids:
        return jsonify({"error": "no ids"}), 400

    entries = load_entries()
    now = int(time.time() * 1000)
    for e in entries:
        if e.get("id") in ids:
            e["sent"] = True
            e["updatedAt"] = now
    save_entries(entries)
    return jsonify({"ok": True, "count": len(ids)})


# ── API: Units ───────────────────────────────────────────────────────────────
@app.route("/api/units/<key>", methods=["GET", "POST"])
@login_required
def api_unit(key):
    units = load_units()
    if request.method == "GET":
        return jsonify(units.get(key, {}))

    # POST — save/update unit profile
    data = request.get_json(force=True)
    profile = {
        "currentValue": data.get("currentValue"),
        "updatedAt": int(time.time() * 1000),
        "updatedBy": session["user"].get("first_name", ""),
    }
    units[key] = profile
    save_units(units)
    return jsonify(profile)


@app.route("/api/units", methods=["GET"])
@login_required
def api_units():
    return jsonify(load_units())


# ── API: Settings ────────────────────────────────────────────────────────────
@app.route("/api/settings", methods=["GET", "PUT"])
@login_required
def api_settings():
    if request.method == "GET":
        return jsonify(load_settings())

    data = request.get_json(force=True)
    settings = {
        "T": int(data.get("T", 15000)),
        "R": int(data.get("R", 500)),
    }
    save_settings(settings)
    return jsonify(settings)


# ── API: Sync (for mobile polling) ──────────────────────────────────────────
@app.route("/api/sync", methods=["GET"])
@login_required
def api_sync():
    """Returns all data since a given timestamp."""
    since = request.args.get("since", "0")
    try:
        since_ts = int(since)
    except ValueError:
        since_ts = 0

    entries = load_entries()
    if since_ts > 0:
        entries = [e for e in entries if e.get("createdAt", 0) > since_ts or e.get("updatedAt", 0) > since_ts]

    return jsonify({
        "entries": entries,
        "units": load_units(),
        "settings": load_settings(),
        "serverTime": int(time.time() * 1000),
    })


# ── Serve static files ───────────────────────────────────────────────────────
@app.route("/")
def index():
    return redirect("/oillog-mobile.html")

@app.route("/<path:filename>")
def serve_static(filename):
    # Serve from current directory
    static_dir = Path(__file__).parent
    file_path = static_dir / filename
    if file_path.exists() and file_path.is_file():
        return send_from_directory(static_dir, filename)
    return jsonify({"error": "not found"}), 404


# ── Health check ─────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "time": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(DATA_DIR),
    })


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"OILLOG Server starting on port {PORT}")
    logger.info(f"Data directory: {DATA_DIR}")
    app.run(host="0.0.0.0", port=PORT, debug=True)

