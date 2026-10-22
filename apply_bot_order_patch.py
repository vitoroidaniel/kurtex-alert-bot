from pathlib import Path
import sys
path=Path("api/server.py")
if not path.exists():sys.exit("Run this from the Alert Bot project root")
s=path.read_text()
marker='@app.route("/api/workspace/board")'
if 'def api_workspace_order()' in s:sys.exit("Shared ordering already installed")
if s.count(marker)!=1:sys.exit("Unexpected server.py version; not modifying it")
endpoint='@app.route("/api/workspace/order", methods=["GET", "PUT"])\ndef api_workspace_order():\n    """Shared cosmetic card order; Telegram case records remain untouched."""\n    from backend.core.workspace_order import snapshot, update\n    if request.method == "GET":\n        actor, error = _workspace_actor_read()\n        if error: return error\n        return jsonify({"order": snapshot(DATA_DIR)})\n    actor, error = _workspace_actor()\n    if error: return error\n    data = request.get_json(silent=True)\n    if not isinstance(data, dict): return jsonify({"error": "Invalid order"}), 400\n    try:\n        ok, state = update(DATA_DIR, data.get("group"), data.get("ids"), data.get("version"))\n        if not ok: return jsonify({"error": "Card order changed by another agent", "order": state}), 409\n        return jsonify({"order": state})\n    except ValueError as exc:\n        return jsonify({"error": str(exc)}), 400\n\n\n'
path.with_name("server.py.before-shared-order").write_text(s)
path.write_text(s.replace(marker,endpoint+marker,1))
print("Patched api/server.py; backup: api/server.py.before-shared-order")
