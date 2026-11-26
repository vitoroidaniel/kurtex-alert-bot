"""Additive, idempotent staging-only board capacity fixture."""
import json
import os
import shutil
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from backend.storage import db
from backend.storage.case_store import _upsert_case_row
from scripts.seed_staging import guard
from scripts.backup_sqlite import snapshot
from scripts.staging_presence import BASE, NAMES

PREFIX = "stg-capacity-v2-"
STAGES = ("jbs_plant", "meijer", "dealer_shop", "monitoring", "to_yard", "at_yard")
ISSUES = ("Battery fails to hold charge", "Air suspension leak", "Reefer temperature above setpoint", "ABS warning", "Coolant leak", "DPF regeneration required", "Trailer tire damage", "Starter intermittent failure")
NOTES = ("Driver reported issue and sent photos.", "Contacted shop to confirm availability.", "Technician confirmed initial diagnosis.", "Requested estimate and parts availability.", "Updated dispatcher with repair ETA.", "Reviewed estimate and requested approval.", "Shop confirmed parts ordered.", "Followed up with driver.", "Updated repair cost and ETA.", "Confirmed repair status with technician.", "Received service paperwork.", "Verified repair completion with driver.")
def seed_capacity():
    guard()
    root = Path("/app/data")
    if shutil.disk_usage(root).free < 300*1024*1024:
        raise RuntimeError("Insufficient staging disk space")
    backup = root / "staging-pre-capacity-v2.sqlite3"
    if not backup.exists():
        snapshot(root/"kurtex.sqlite3", backup)
    rng = random.Random(20261126)
    now = datetime.now(timezone.utc)
    with db.write_tx(root) as conn:
        for i,name in enumerate(NAMES):
            uid=BASE+i
            old=conn.execute("SELECT extra_json FROM users WHERE id=?",(uid,)).fetchone()
            if old and not json.loads(old[0]).get("synthetic_staging"):
                raise RuntimeError("Synthetic identity collision")
            conn.execute("INSERT OR IGNORE INTO users(id,name,username,role,extra_json,updated_at) VALUES(?,?,?,?,?,?)",(uid,"Sim "+name,"","agent",json.dumps({"synthetic_staging":True}),now.isoformat()))
        db.bump_version(conn,"users_version")
        agents=[(int(r[0]),r[1]) for r in conn.execute("SELECT id,name FROM users WHERE role IN ('agent','developer','super_admin') ORDER BY id").fetchall()]
    plan=[]
    for i in range(3000):
        plan.append(("done","",agents[i%len(agents)]))
    lane_counts={}
    for stage in STAGES:
        lane_counts[stage]=rng.randint(5,20)
        for j in range(lane_counts[stage]):
            plan.append(("reported",stage,agents[j%len(agents)]))
    agent_counts={}
    for uid,name in agents:
        agent_counts[str(uid)]=rng.randint(5,10)
        for j in range(agent_counts[str(uid)]):
            plan.append(("reported","",(uid,name)))
    inserted=0
    for base in range(0,len(plan),100):
        with db.write_tx(root) as conn:
            for i in range(base,min(base+100,len(plan))):
                status,stage,(uid,name)=plan[i]
                cid=PREFIX+str(i).zfill(6)
                note_count=rng.randint(6,14)
                cost=rng.randint(150,2500)
                priority=rng.choice(("normal","normal","high"))
                if conn.execute("SELECT 1 FROM cases WHERE id=?",(cid,)).fetchone(): continue
                opened=now-timedelta(days=(i%180+2 if status=="done" else i%3),hours=i%10)
                stamp=opened.isoformat()
                end=(opened+timedelta(hours=12)).isoformat() if status=="done" else None
                issue=ISSUES[i%len(ISSUES)]
                notes=[dict(id=cid+"-n"+str(k),author_id=agents[(i+k)%len(agents)][0],author=agents[(i+k)%len(agents)][1],text="SIM: "+NOTES[k%len(NOTES)],created_at=(opened+timedelta(minutes=20*k)).isoformat()) for k in range(note_count)]
                case=dict(id=cid,status=status,agent_id=uid,agent_name=name,driver_name="Synthetic Driver "+str(i+1),report_driver="Synthetic Driver "+str(i+1),group_name="Synthetic Fleet",description=issue,issue_text=issue,opened_at=stamp,assigned_at=stamp,reported_at=stamp,updated_at=end or stamp,closed_at=end,source="workspace",workspace_request_id=cid,created_by_id=uid,vehicle_type="truck" if i%2 else "trailer",unit_number="CAP"+str(10000+i),priority=priority,board_stage=stage,board_rank=float(i),shop_info="SIM Repair Center "+str(i%12+1),amount=str(cost),load_type="Loaded" if i%2 else "Empty",workspace_notes=notes,workspace_attachments=[],synthetic_dataset="staging-capacity-v2")
                _upsert_case_row(conn,case,event_type="created",actor_id=str(uid),actor_name="Capacity Fixture")
                inserted+=1
    with db.read_conn(root) as conn:
        integrity=conn.execute("PRAGMA quick_check").fetchone()[0]
        counts=dict(conn.execute("SELECT status,count(*) FROM cases WHERE id LIKE ? GROUP BY status",(PREFIX+"%",)).fetchall())
        notes=conn.execute("SELECT count(*) FROM case_notes WHERE case_id LIKE ?",(PREFIX+"%",)).fetchone()[0]
    if integrity!="ok": raise RuntimeError("Staging database integrity check failed")
    print("STAGING_CAPACITY_RESULT "+json.dumps(dict(inserted=inserted,counts=counts,planned_lanes=lane_counts,planned_agents=agent_counts,notes=notes,integrity=integrity)),flush=True)
