"""Bounded staging-only synthetic collaboration demo; never sends Telegram messages."""
import json
import threading
import time
import uuid
from datetime import datetime, timezone

from scripts.seed_staging import guard

# Above Telegram's documented 52-bit identifier range, within JS safe integers.
BASE = 2**52 + 1000
NAMES = ("Alex", "Maria", "Chris", "Anna", "Max", "Kate", "Sam", "Nina", "Leo", "Eva", "Tom", "Mia")
def start(count=8, duration=1800):
    guard()
    if not 1 <= count <= len(NAMES) or not 1 <= duration <= 3600:
        raise ValueError("Invalid simulation limits")
    from backend.storage import db, board_presence
    from backend.storage.case_store import _upsert_case_row
    agents = [(BASE+i, "Sim "+NAMES[i], str(uuid.uuid4())) for i in range(count)]
    with db.write_tx() as conn:
        for uid,name,_ in agents:
            existing = conn.execute("SELECT extra_json FROM users WHERE id=?", (uid,)).fetchone()
            if existing and not json.loads(existing[0]).get("synthetic_staging"):
                raise RuntimeError("Synthetic identity collision")
            conn.execute("INSERT OR IGNORE INTO users(id,name,username,role,extra_json,updated_at) VALUES(?,?,?,?,?,?)",
                (uid,name,"","agent",json.dumps({"synthetic_staging":True}),datetime.now(timezone.utc).isoformat()))
        db.bump_version(conn,"users_version")
        # Distribute only generated active cases that have not been manually moved.
        rows=conn.execute("SELECT data_json FROM cases WHERE id LIKE 'stg-load-v1-%' AND status='reported' AND closed_at IS NULL ORDER BY id").fetchall()
        for i,row in enumerate(rows):
            case=json.loads(row[0])
            if case.get("synthetic_dataset") != "staging-load-v1" or case.get("board_stage") or case.get("synthetic_assigned"):
                continue
            uid,name,_=agents[i%count]
            case.update(agent_id=uid,agent_name=name,agent_username="",synthetic_assigned=True)
            _upsert_case_row(conn,case,event_type="updated",actor_name="Staging Simulator")
    def loop():
        deadline=time.monotonic()+duration
        tick=0
        try:
            while time.monotonic()<deadline:
                with db.read_conn() as conn:
                    ids=[r[0] for r in conn.execute("SELECT id FROM cases WHERE id LIKE 'stg-load-v1-%' AND status='reported' AND closed_at IS NULL ORDER BY id LIMIT 100").fetchall()]
                for i,(uid,name,sid) in enumerate(agents):
                    phase=(tick+i*2)%12
                    action="idle" if phase>=9 or not ids else ("viewing" if phase<2 else "editing" if phase<7 else "dragging")
                    cid=ids[(i+tick//12)%len(ids)] if action!="idle" else ""
                    field=("notes","issue","shop_info")[i%3] if action=="editing" else ""
                    draft=("Checking repair ETA","Inspecting air leak","Confirming shop estimate")[i%3][:max(1,(phase-1)*7)] if action=="editing" else ""
                    board_presence.heartbeat(sid,uid,name,action,cid,field,draft=draft)
                tick+=1
                time.sleep(4)
        finally:
            for uid,_,sid in agents:
                board_presence.remove_session(sid,user_id=uid)
            print("STAGING_PRESENCE finished",flush=True)
    thread=threading.Thread(target=loop,name="staging-presence",daemon=True)
    thread.start()
    print("STAGING_PRESENCE started agents="+str(count)+" duration="+str(duration),flush=True)
