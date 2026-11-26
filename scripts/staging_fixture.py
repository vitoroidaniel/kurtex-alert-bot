"""Disposable staging API with real storage/routes and synthetic users only.

This is not an application entry point for production. The fixture has no
Telegram, external AI, production credentials, mounts, or imported records.
"""
import os
import tempfile
from pathlib import Path

if os.environ.get("KURTEX_STAGING_FIXTURE") != "1":
    raise RuntimeError("Fixture execution requires explicit isolation")
if any(os.getenv(name) for name in ("BOT_TOKEN", "DATABASE_URL", "REDIS_URL", "RAILWAY_VOLUME_MOUNT_PATH", "CF_API_TOKEN", "CLOUDFLARE_API_TOKEN", "OPENAI_API_KEY")):
    raise RuntimeError("Real credentials or storage detected in fixture environment")
secret = os.environ.get("KURTEX_FIXTURE_SIGNING_KEY", "")
if len(secret) < 32:
    raise RuntimeError("A dedicated synthetic signing key is required")
root = tempfile.TemporaryDirectory(prefix="kurtex-isolated-fixture-")
os.environ["DATA_DIR"] = root.name
os.environ["KURTEX_LISTS_DB"] = str(Path(root.name) / "lists.sqlite3")
os.environ["API_SESSION_SECRET"] = secret

from api import server
from backend.storage import case_store, user_store

app = server.app
for i in range(20):
    user_store.add_user(910000+i, f"Fixture Agent {i}", f"fixture{i}", "developer" if i==0 else "agent")
actor = {"id":910000,"name":"Fixture Agent 0","username":"fixture0","role":"developer"}
for i in range(3):
    case_store.workspace_write(actor, {
        "id":f"CASE-FIXTURE-{i}",
        "request_id":f"fixture-request-{i}",
        "driver":f"Synthetic Driver {i}",
        "unit_number":f"FIX{i}",
        "issue":"Synthetic battery inspection",
        "vehicle_type":"truck",
        "priority":"normal",
        "target_agent":{"id":910001,"name":"Fixture Agent 1","username":"fixture1"},
    })

@app.get("/__fixture_health")
def fixture_health():
    return {"status":"ready","fixture":True,"users":20}
